"""用户设置与日志目录。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields

from .models import AppSettings


APP_NAME = "V2rayNSmartRoute"


def data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def log_dir() -> str:
    path = os.path.join(data_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def backup_dir() -> str:
    path = os.path.join(data_dir(), "backups")
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


def load_settings() -> AppSettings:
    path = settings_path()
    if not os.path.isfile(path):
        return AppSettings()
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        valid = {item.name for item in fields(AppSettings)}
        filtered = {key: value for key, value in data.items() if key in valid}
        settings = AppSettings(**filtered)
        # 防止旧版本写入非法值
        if settings.mode not in ("balanced", "latency", "speed"):
            settings.mode = "balanced"
        if settings.sys_proxy_mode not in ("keep", "on", "off"):
            settings.sys_proxy_mode = "keep"
        settings.concurrency = max(1, min(16, int(settings.concurrency or 8)))
        settings.speed_top_n = max(0, min(12, int(settings.speed_top_n or 5)))
        settings.timeout = max(2.0, min(20.0, float(settings.timeout or 5.0)))
        return settings
    except Exception:
        return AppSettings()


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(asdict(settings), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
