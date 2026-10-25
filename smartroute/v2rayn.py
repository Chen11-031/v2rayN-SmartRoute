"""读取 v2rayN 节点库、写回选择结果、重启 v2rayN。"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from . import netprobe, resources, winutil
from .models import Profile, TestResult


# v2rayN 7.10.4 生成的 sing-box 配置仍使用旧版 inbound / dns 出站字段。
# 1.13.0 起这些字段被移除，所以 sing-box 节点需要 1.12.x。
SINGBOX_COMPAT_MIN = (1, 12, 0)
SINGBOX_COMPAT_MAX = (1, 13, 0)
BUNDLED_SINGBOX_VERSION = (1, 12, 9)


class V2rayNError(Exception):
    pass


@dataclass
class InstallInfo:
    root: str
    exe: str
    db_path: str
    gui_config_path: str
    xray_exe: str
    singbox_exe: str
    local_port: int = 10808
    version: str = ""

    @property
    def bin_dir(self) -> str:
        return os.path.join(self.root, "bin")

    @property
    def gui_dir(self) -> str:
        return os.path.join(self.root, "guiConfigs")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def inspect_install(root: str) -> Optional[InstallInfo]:
    if not root:
        return None
    root = os.path.abspath(os.path.expanduser(root.strip().strip('"')))
    exe = os.path.join(root, "v2rayN.exe")
    db_path = os.path.join(root, "guiConfigs", "guiNDB.db")
    gui_config = os.path.join(root, "guiConfigs", "guiNConfig.json")
    if not os.path.isfile(exe) or not os.path.isfile(db_path):
        return None
    xray = os.path.join(root, "bin", "xray", "xray.exe")
    singbox = os.path.join(root, "bin", "sing_box", "sing-box.exe")
    if not os.path.isfile(xray):
        xray = ""
    if not os.path.isfile(singbox):
        singbox = ""
    if not xray and not singbox:
        return None
    info = InstallInfo(root, exe, db_path, gui_config, xray, singbox)
    try:
        with open(gui_config, "r", encoding="utf-8-sig") as handle:
            cfg = json.load(handle)
        inbounds = cfg.get("Inbound") or []
        if inbounds and isinstance(inbounds[0], dict):
            info.local_port = int(inbounds[0].get("LocalPort") or 10808)
    except Exception:
        pass
    try:
        import ctypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(exe, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if ctypes.windll.version.GetFileVersionInfoW(exe, 0, size, buf):
                ptr = ctypes.c_void_p()
                length = ctypes.c_uint()
                if ctypes.windll.version.VerQueryValueW(
                    buf, "\\", ctypes.byref(ptr), ctypes.byref(length)
                ):
                    class VS_FIXEDFILEINFO(ctypes.Structure):
                        _fields_ = [
                            ("dwSignature", ctypes.c_uint32),
                            ("dwStrucVersion", ctypes.c_uint32),
                            ("dwFileVersionMS", ctypes.c_uint32),
                            ("dwFileVersionLS", ctypes.c_uint32),
                            ("dwProductVersionMS", ctypes.c_uint32),
                            ("dwProductVersionLS", ctypes.c_uint32),
                            ("dwFileFlagsMask", ctypes.c_uint32),
                            ("dwFileFlags", ctypes.c_uint32),
                            ("dwFileOS", ctypes.c_uint32),
                            ("dwFileType", ctypes.c_uint32),
                            ("dwFileSubtype", ctypes.c_uint32),
                            ("dwFileDateMS", ctypes.c_uint32),
                            ("dwFileDateLS", ctypes.c_uint32),
                        ]

                    fixed = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
                    major = fixed.dwFileVersionMS >> 16
                    minor = fixed.dwFileVersionMS & 0xFFFF
                    build = fixed.dwFileVersionLS >> 16
                    info.version = f"{major}.{minor}.{build}"
    except Exception:
        pass
    return info


def detect_install(saved: str = "") -> Optional[InstallInfo]:
    candidates: list[str] = []
    if saved:
        candidates.append(saved)

    for pid, path in winutil.find_processes("v2rayN.exe"):
        if path:
            candidates.append(os.path.dirname(path))

    home = os.path.expanduser("~")
    candidates.extend(
        [
            r"D:\software\v2rayN-windows-64-SelfContained",
            r"D:\software\v2rayN-windows-64",
            os.path.join(home, "Desktop", "v2rayN-windows-64-SelfContained"),
            os.path.join(home, "Desktop", "v2rayN-windows-64"),
            os.path.join(home, "Downloads", "v2rayN-windows-64-SelfContained"),
            os.path.join(home, "Downloads", "v2rayN-windows-64"),
            os.path.join(os.environ.get("LOCALAPPDATA", home), "v2rayN"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "v2rayN"),
        ]
    )

    # 在常见软件目录里再找一层，避免用户把 v2rayN 放在别处。
    for base in (r"D:\software", r"C:\software", os.path.join(home, "Desktop")):
        try:
            with os.scandir(base) as entries:
                for entry in entries:
                    if entry.is_dir() and "v2rayn" in entry.name.lower():
                        candidates.append(entry.path)
        except OSError:
            pass

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = _norm(candidate)
        if key in seen:
            continue
        seen.add(key)
        info = inspect_install(candidate)
        if info:
            return info
    return None


def _copy_with_sidecars(src: str, dst_dir: str) -> list[str]:
    copied: list[str] = []
    base = os.path.basename(src)
    for suffix in ("", "-wal", "-shm"):
        source = src + suffix
        if os.path.isfile(source):
            target = os.path.join(dst_dir, base + suffix)
            shutil.copy2(source, target)
            copied.append(target)
    return copied


def read_profiles(info: InstallInfo) -> list[Profile]:
    tmp_dir = tempfile.mkdtemp(prefix="v2rayn-db-")
    tmp_db = os.path.join(tmp_dir, "guiNDB.db")
    try:
        _copy_with_sidecars(info.db_path, tmp_dir)
        con = sqlite3.connect(tmp_db, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                SELECT p.*, e.Delay AS Delay, e.Speed AS Speed, e.Sort AS Sort,
                       e.Message AS Message
                FROM ProfileItem p
                LEFT JOIN ProfileExItem e ON e.IndexId = p.IndexId
                WHERE p.Port > 0
                ORDER BY COALESCE(e.Sort, 999999), p.Remarks
                """
            ).fetchall()
        finally:
            con.close()
        return [Profile.from_row(row) for row in rows]
    except sqlite3.DatabaseError as exc:
        raise V2rayNError(f"读取节点库失败：{exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class V2rayNController:
    def __init__(self, info: InstallInfo):
        self.info = info
        self.backup_root = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "V2rayNSmartRoute",
            "backups",
        )
        self.core_backup_root = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "V2rayNSmartRoute",
            "core-backups",
        )

    # ------------------------------------------------------------------ core
    @staticmethod
    def _read_singbox_version(exe: str) -> Optional[tuple[int, int, int]]:
        if not exe or not os.path.isfile(exe):
            return None
        try:
            proc = subprocess.run(
                [exe, "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=winutil.CREATE_NO_WINDOW,
            )
        except Exception:
            return None
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(
            r"sing-box version\s+v?(\d+)\.(\d+)\.(\d+)", output, re.IGNORECASE
        )
        if not match:
            return None
        return tuple(int(part) for part in match.groups())  # type: ignore[return-value]

    @staticmethod
    def _version_text(version: Optional[tuple[int, int, int]]) -> str:
        return ".".join(str(part) for part in version) if version else "未知版本"

    @staticmethod
    def _sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _backup_core(self, version: Optional[tuple[int, int, int]]) -> str:
        source = self.info.singbox_exe
        if not os.path.isfile(source):
            raise V2rayNError("没有找到 v2rayN 的 sing-box 核心，无法备份")
        os.makedirs(self.core_backup_root, exist_ok=True)
        digest = self._sha256(source)
        name = f"sing-box-{self._version_text(version)}-{digest[:12]}.exe"
        target = os.path.join(self.core_backup_root, name)
        if not os.path.isfile(target):
            shutil.copy2(source, target)
            entry = {
                "time": datetime.now().isoformat(timespec="seconds"),
                "original_version": self._version_text(version),
                "original_sha256": digest,
                "backup": target,
                "replaced_with": self._version_text(BUNDLED_SINGBOX_VERSION),
            }
            history = os.path.join(self.core_backup_root, "history.jsonl")
            try:
                with open(history, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return target

    def _replace_singbox(self, source: str) -> None:
        target = self.info.singbox_exe
        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)
        tmp = target + ".smartroute-new"
        try:
            shutil.copy2(source, tmp)
            last_error: Optional[Exception] = None
            for _ in range(12):
                try:
                    if os.path.isfile(target):
                        try:
                            os.chmod(target, stat.S_IWRITE)
                        except OSError:
                            pass
                    os.replace(tmp, target)
                    last_error = None
                    break
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.5)
            if last_error is not None:
                raise V2rayNError(
                    f"无法替换 sing-box 核心，文件可能仍被占用：{last_error}"
                )
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def ensure_compatible_core(self) -> str:
        """把 1.13+ 的 sing-box 换成随程序附带的 1.12.9（会先备份）。

        只应在 v2rayN 已经停止后调用。返回给用户看的说明；无需改动时为空。
        """
        if not self.info.singbox_exe:
            raise V2rayNError("v2rayN 目录里没有 sing-box 核心，无法启用 Hysteria2/TUIC")
        current = self._read_singbox_version(self.info.singbox_exe)
        if current and SINGBOX_COMPAT_MIN <= current < SINGBOX_COMPAT_MAX:
            return ""

        source = resources.bundled_singbox()
        if not os.path.isfile(source):
            raise V2rayNError(
                "当前 sing-box 版本无法运行 v2rayN 生成的 Hysteria2 配置，"
                "但程序目录下缺少 vendor\\sing-box-1.12.9.exe。"
                "请重新解压完整安装包，或改用 Xray 节点。"
            )
        source_version = self._read_singbox_version(source)
        if source_version != BUNDLED_SINGBOX_VERSION:
            raise V2rayNError(
                "随程序附带的 sing-box 核心版本不正确，"
                f"应为 1.12.9，实际为 {self._version_text(source_version)}"
            )

        backup = self._backup_core(current)
        self._replace_singbox(source)
        new_version = self._read_singbox_version(self.info.singbox_exe)
        if new_version != BUNDLED_SINGBOX_VERSION:
            # 极端情况下（例如被杀毒软件拦截）新核心不可用，尽量把原核心换回去，
            # 至少保证 v2rayN 还能用 Xray / 原节点启动。
            try:
                self._replace_singbox(backup)
            except Exception:
                pass
            raise V2rayNError(
                "sing-box 核心替换后版本校验失败："
                f"期望 1.12.9，实际 {self._version_text(new_version)}"
            )
        return (
            f"已将 sing-box {self._version_text(current)} 替换为 1.12.9"
            f"（原核心已备份到 {backup}）"
        )

    # ---------------------------------------------------------------- status
    def running_pids(self) -> list[int]:
        pids: list[int] = []
        target = _norm(self.info.exe)
        for pid, path in winutil.find_processes("v2rayN.exe"):
            if path and _norm(path) == target:
                pids.append(pid)
        return pids

    def is_running(self) -> bool:
        return bool(self.running_pids())

    def stop(self) -> bool:
        """关闭 v2rayN，失败时抛异常。返回之前是否在运行。"""
        pids = self.running_pids()
        if not pids:
            return False

        for pid in pids:
            winutil.post_close_to_pid(pid)

        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            if not any(winutil.is_process_running(pid) for pid in pids):
                break
            time.sleep(0.2)

        for pid in pids:
            if winutil.is_process_running(pid):
                winutil.kill_tree(pid)

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not any(winutil.is_process_running(pid) for pid in pids):
                break
            time.sleep(0.2)

        if any(winutil.is_process_running(pid) for pid in pids):
            raise V2rayNError("无法关闭 v2rayN，请手动退出后重试")

        # v2rayN 被强制结束时核心可能残留，顺手清理它自己的 bin 目录下的核心。
        winutil.kill_cores_under(self.info.bin_dir)
        if not winutil.wait_port_free(self.info.local_port, timeout=8):
            raise V2rayNError(
                f"端口 {self.info.local_port} 仍被占用，请检查是否有其他代理程序在运行"
            )
        return True

    def start(self) -> None:
        # v2rayN 7.10.4 生成的 sing-box 配置仍使用旧版 DNS 和特殊出站写法；
        # sing-box 1.12.x 默认会拒绝启动，仅对本进程生效，不修改系统环境。
        env = os.environ.copy()
        env["ENABLE_DEPRECATED_LEGACY_DNS_SERVERS"] = "true"
        env["ENABLE_DEPRECATED_SPECIAL_OUTBOUNDS"] = "true"
        winutil.start_detached(self.info.exe, self.info.root, env=env)

    def wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        saw_process = False
        while time.monotonic() < deadline:
            if self.running_pids():
                saw_process = True
                if winutil.port_is_listening(self.info.local_port):
                    return
            time.sleep(0.2)
        if not saw_process:
            raise V2rayNError("v2rayN 没有启动成功")
        raise V2rayNError(
            f"v2rayN 已启动，但代理端口 {self.info.local_port} 没有监听。"
            "可能是节点配置不可用，已尝试恢复之前的节点。"
        )

    def verify_proxy(self, url: str = "", timeout: float = 18.0) -> None:
        """重启后通过本地端口做一次真实 HTTPS 请求，确认新节点确实可用。"""
        candidates: list[str] = []
        for candidate in (
            url,
            "https://www.gstatic.com/generate_204",
            "https://cp.cloudflare.com/generate_204",
        ):
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        deadline = time.monotonic() + timeout
        last_error = "代理没有响应"
        while time.monotonic() < deadline:
            for candidate in candidates:
                remaining = deadline - time.monotonic()
                if remaining <= 0.2:
                    break
                try:
                    probe = netprobe.probe_https(
                        self.info.local_port,
                        candidate,
                        timeout=min(remaining, 6.0),
                    )
                    if netprobe.is_acceptable_status(probe.status_code):
                        return
                    last_error = f"HTTP {probe.status_code}"
                except Exception as exc:
                    last_error = str(exc) or last_error
            time.sleep(0.35)
        raise V2rayNError(f"v2rayN 已重启，但新节点实测不可用：{last_error}")

    # ---------------------------------------------------------------- backup
    def _backup(self) -> str:
        os.makedirs(self.backup_root, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = os.path.join(self.backup_root, stamp)
        os.makedirs(backup_dir, exist_ok=True)
        for source in (self.info.gui_config_path, self.info.db_path):
            _copy_with_sidecars(source, backup_dir)
        self._prune_backups()
        return backup_dir

    def _prune_backups(self, keep: int = 10) -> None:
        try:
            dirs = sorted(
                [d for d in glob.glob(os.path.join(self.backup_root, "*")) if os.path.isdir(d)],
                key=os.path.getmtime,
                reverse=True,
            )
            for old in dirs[keep:]:
                shutil.rmtree(old, ignore_errors=True)
        except Exception:
            pass

    def _restore(self, backup_dir: str) -> None:
        config_backup = os.path.join(backup_dir, os.path.basename(self.info.gui_config_path))
        db_backup = os.path.join(backup_dir, os.path.basename(self.info.db_path))
        if os.path.isfile(config_backup):
            shutil.copy2(config_backup, self.info.gui_config_path)
        if os.path.isfile(db_backup):
            for suffix in ("-wal", "-shm"):
                target = self.info.db_path + suffix
                if os.path.isfile(target):
                    try:
                        os.remove(target)
                    except OSError:
                        pass
            shutil.copy2(db_backup, self.info.db_path)

    def _save_failure_diagnostics(self, backup_dir: str) -> None:
        """切换失败时保留 v2rayN 生成的配置和当天日志，便于排查节点/核心问题。"""
        try:
            diag_dir = os.path.join(backup_dir, "diagnostics")
            os.makedirs(diag_dir, exist_ok=True)
            sources = [
                self.info.gui_config_path,
                os.path.join(self.info.root, "binConfigs", "config.json"),
            ]
            log_dir = os.path.join(self.info.root, "guiLogs")
            try:
                logs = sorted(
                    (
                        os.path.join(log_dir, name)
                        for name in os.listdir(log_dir)
                        if name.lower().endswith(".txt")
                    ),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if logs:
                    sources.append(logs[0])
            except OSError:
                pass
            for source in sources:
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(diag_dir, os.path.basename(source)))
        except Exception:
            pass

    # ---------------------------------------------------------------- update
    def _load_gui_config(self) -> dict:
        with open(self.info.gui_config_path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _save_gui_config(self, cfg: dict) -> None:
        target = self.info.gui_config_path
        tmp = target + ".smartroute.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cfg, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, target)

    def _update_gui_config(self, winner: TestResult, sys_proxy_mode: str) -> None:
        cfg = self._load_gui_config()
        cfg["IndexId"] = winner.profile.index_id
        cfg["RunningCoreType"] = 24 if winner.profile.uses_singbox else 2
        if sys_proxy_mode == "on":
            cfg.setdefault("SystemProxyItem", {})["SysProxyType"] = 1
        elif sys_proxy_mode == "off":
            cfg.setdefault("SystemProxyItem", {})["SysProxyType"] = 0
        self._save_gui_config(cfg)

    def _update_db(
        self,
        results: Iterable[TestResult],
        winner: TestResult,
        allow_insecure: bool,
    ) -> None:
        con = sqlite3.connect(self.info.db_path, timeout=15)
        try:
            con.execute("BEGIN IMMEDIATE")
            exists = con.execute(
                "SELECT 1 FROM ProfileItem WHERE IndexId=?", (winner.profile.index_id,)
            ).fetchone()
            if not exists:
                raise V2rayNError("选中的节点已不在 v2rayN 节点库中，请重新测速")

            max_sort = con.execute(
                "SELECT COALESCE(MAX(Sort), 0) FROM ProfileExItem"
            ).fetchone()[0]
            next_sort = int(max_sort) + 1

            for result in results:
                profile = result.profile
                if result.skipped:
                    continue
                if result.ok:
                    delay = int(result.latency_ms or 0)
                    speed = float(result.speed_mbs or 0.0)
                    message = ""
                    insecure_row = con.execute(
                        "SELECT AllowInsecure FROM ProfileItem WHERE IndexId=?",
                        (profile.index_id,),
                    ).fetchone()
                    db_allows_insecure = bool(
                        insecure_row
                        and str(insecure_row[0] or "").strip().lower()
                        in ("1", "true", "yes", "on")
                    )
                    if result.needs_insecure and not (
                        db_allows_insecure
                        or (
                            winner.profile.index_id == profile.index_id
                            and allow_insecure
                        )
                    ):
                        message = "证书不受信任（需开启 allowInsecure）"
                        delay = 0
                        speed = 0.0
                else:
                    delay = 0
                    speed = 0.0
                    message = _short_message(result.error or result.status_text)

                row = con.execute(
                    "SELECT Sort FROM ProfileExItem WHERE IndexId = ?", (profile.index_id,)
                ).fetchone()
                if row:
                    con.execute(
                        "UPDATE ProfileExItem SET Delay=?, Speed=?, Message=? WHERE IndexId=?",
                        (delay, round(speed, 2), message, profile.index_id),
                    )
                else:
                    con.execute(
                        "INSERT INTO ProfileExItem (IndexId, Delay, Speed, Sort, Message) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (profile.index_id, delay, round(speed, 2), next_sort, message),
                    )
                    next_sort += 1

            # 保证 v2rayN 用正确的核心启动这个节点：Xray=2，sing-box=24。
            core_type = 24 if winner.profile.uses_singbox else 2
            con.execute(
                "UPDATE ProfileItem SET CoreType=? WHERE IndexId=?",
                (core_type, winner.profile.index_id),
            )
            if allow_insecure and winner.needs_insecure:
                con.execute(
                    "UPDATE ProfileItem SET AllowInsecure='true' WHERE IndexId=?",
                    (winner.profile.index_id,),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def apply(
        self,
        winner: TestResult,
        results: Iterable[TestResult],
        sys_proxy_mode: str = "keep",
        allow_insecure: bool = False,
    ) -> str:
        """切换节点。返回需要提示给用户的核心变更说明（没有则为空）。"""
        # 核心不兼容时提前检查，避免白白关闭 v2rayN。
        if winner.profile.uses_singbox:
            if not self.info.singbox_exe:
                raise V2rayNError(
                    "v2rayN 目录里没有 sing-box 核心，无法启用 Hysteria2/TUIC 节点"
                )
            current = self._read_singbox_version(self.info.singbox_exe)
            needs_replace = not (
                current and SINGBOX_COMPAT_MIN <= current < SINGBOX_COMPAT_MAX
            )
            if needs_replace and not os.path.isfile(resources.bundled_singbox()):
                raise V2rayNError(
                    "当前 sing-box 无法运行 v2rayN 生成的 Hysteria2 配置，"
                    "但程序目录下缺少 vendor\\sing-box-1.12.9.exe。"
                    "请重新解压完整安装包，或先改用 Xray 节点。"
                )

        # 未运行时也继续执行，并在切换后让它保持运行，符合“一键启用”的预期。
        self.stop()
        backup_dir = self._backup()
        core_note = ""
        try:
            if winner.profile.uses_singbox:
                core_note = self.ensure_compatible_core()
            self._update_gui_config(winner, sys_proxy_mode)
            self._update_db(results, winner, allow_insecure)
            self.start()
            self.wait_ready()
            self.verify_proxy(winner.used_url)
            return core_note
        except Exception as exc:
            problems = [str(exc)]
            restored = False
            self._save_failure_diagnostics(backup_dir)
            # 恢复前先关掉刚刚启动的 v2rayN，避免它占着数据库/配置继续写入。
            try:
                self.stop()
            except Exception as stop_exc:
                problems.append(f"无法关闭异常的 v2rayN：{stop_exc}")
            try:
                self._restore(backup_dir)
            except Exception as restore_exc:
                problems.append(f"恢复备份失败：{restore_exc}")
            try:
                self.start()
                self.wait_ready(timeout=20)
                restored = True
            except Exception as restart_exc:
                problems.append(f"恢复原节点后仍无法启动：{restart_exc}")

            if restored:
                problems.append("已恢复原节点并重新启动 v2rayN")
            if len(problems) > 1:
                raise V2rayNError("；".join(problems)) from exc
            raise


def _short_message(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text
