"""Windows 进程/窗口相关的小工具，只使用标准库 + ctypes。"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import socket
import subprocess
import time
from typing import Iterable, Optional


CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102
TH32CS_SNAPPROCESS = 0x00000002

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
]
_kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wt.DWORD]
_kernel32.WaitForSingleObject.restype = wt.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


_kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = wt.BOOL
_kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wt.BOOL

_EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_user32.EnumWindows.argtypes = [_EnumWindowsProc, wt.LPARAM]
_user32.EnumWindows.restype = wt.BOOL


def _iter_process_entries() -> Iterable[PROCESSENTRY32W]:
    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            yield entry
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)


def find_processes(exe_name: str) -> list[tuple[int, str]]:
    """返回 [(pid, 完整路径)]。路径拿不到时为空字符串。"""
    target = exe_name.lower()
    result: list[tuple[int, str]] = []
    for entry in _iter_process_entries():
        if (entry.szExeFile or "").lower() != target:
            continue
        pid = int(entry.th32ProcessID)
        result.append((pid, get_process_path(pid) or ""))
    return result


def get_process_path(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def is_process_running(pid: int) -> bool:
    handle = _kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        return _kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(handle)


def wait_process_exit(pid: int, timeout: float) -> bool:
    handle = _kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True
    try:
        return _kernel32.WaitForSingleObject(handle, int(timeout * 1000)) != WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(handle)


def post_close_to_pid(pid: int) -> int:
    """给指定进程的所有顶层窗口发 WM_CLOSE，返回发送数量。"""
    sent = 0

    def callback(hwnd, lparam):
        nonlocal sent
        wnd_pid = wt.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wnd_pid))
        if wnd_pid.value == pid:
            title = ctypes.create_unicode_buffer(512)
            _user32.GetWindowTextW(hwnd, title, 512)
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            if title.value or cls.value:
                _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                sent += 1
        return True

    _user32.EnumWindows(_EnumWindowsProc(callback), 0)
    return sent


def kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
    except Exception:
        pass


def kill_process_by_path(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
    except Exception:
        pass


def kill_cores_under(bin_dir: str, names: Iterable[str] = ("xray.exe", "sing-box.exe", "mihomo.exe")) -> int:
    """结束位于指定 bin 目录下的 v2rayN 核心进程，返回结束数量。"""
    bin_dir = os.path.normcase(os.path.abspath(bin_dir)).rstrip("\\/")
    bin_prefix = bin_dir + os.sep
    killed = 0
    for name in names:
        for pid, path in find_processes(name):
            if not path:
                continue
            image = os.path.normcase(os.path.abspath(path))
            if image.startswith(bin_prefix):
                kill_process_by_path(pid)
                killed += 1
    return killed


def port_is_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port_listening(port: int, timeout: float, pid: Optional[int] = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid and not is_process_running(pid):
            return False
        if port_is_listening(port):
            return True
        time.sleep(0.1)
    return False


def wait_port_free(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_is_listening(port):
            return True
        time.sleep(0.1)
    return not port_is_listening(port)


def set_dpi_awareness() -> None:
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def start_detached(exe: str, cwd: str, env: Optional[dict[str, str]] = None) -> subprocess.Popen:
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [exe],
        cwd=cwd,
        close_fds=True,
        creationflags=flags,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
