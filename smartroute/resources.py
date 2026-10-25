"""打包后仍能定位随程序附带的资源。"""

from __future__ import annotations

import os
import sys


def app_dir() -> str:
    """程序所在目录；源码运行时是项目根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        dirs.append(os.path.join(meipass, "vendor"))
    dirs.append(os.path.join(app_dir(), "vendor"))
    # 有人会把 vendor 放在 exe 同级的 _internal 下，也一并兼容。
    dirs.append(os.path.join(app_dir(), "_internal", "vendor"))
    return dirs


def _asset_dirs() -> list[str]:
    dirs: list[str] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        dirs.append(os.path.join(meipass, "assets"))
    dirs.append(os.path.join(app_dir(), "assets"))
    dirs.append(os.path.join(app_dir(), "_internal", "assets"))
    return dirs


def asset_file(name: str) -> str:
    """返回 assets 目录下指定文件；找不到时返回空字符串。"""
    for directory in _asset_dirs():
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return ""


def vendor_file(name: str) -> str:
    """返回 vendor 目录下指定文件；找不到时返回空字符串。"""
    for directory in _candidate_dirs():
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return ""


def bundled_singbox() -> str:
    return vendor_file("sing-box-1.12.9.exe")
