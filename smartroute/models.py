"""数据模型与协议常量。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


CONFIG_TYPE_NAMES = {
    1: "VMess",
    2: "自定义",
    3: "Shadowsocks",
    4: "SOCKS",
    5: "VLESS",
    6: "Trojan",
    7: "Hysteria2",
    8: "TUIC",
    9: "WireGuard",
    10: "HTTP",
}

# v2rayN 使用的 ECoreType
CORE_XRAY = 2
CORE_SING_BOX = 24

XRAY_CONFIG_TYPES = {1, 3, 4, 5, 6, 10}
SINGBOX_CONFIG_TYPES = {7, 8}
SUPPORTED_CONFIG_TYPES = XRAY_CONFIG_TYPES | SINGBOX_CONFIG_TYPES

# 订阅里由机场塞进来的“剩余流量 / 套餐到期”假节点
_INFO_PREFIXES = (
    "剩余流量",
    "套餐到期",
    "套餐剩余",
    "到期时间",
    "过期时间",
    "订阅到期",
    "订阅地址",
    "官网地址",
    "流量重置",
    "距离下次重置",
    "下次重置",
    "重置剩余",
    "重置时间",
)


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _text(value) -> str:
    return "" if value is None else str(value)


@dataclass
class Profile:
    index_id: str
    config_type: int
    remarks: str
    address: str
    port: int
    ports: str = ""
    uuid: str = ""
    alter_id: int = 0
    security: str = ""
    network: str = ""
    header_type: str = ""
    request_host: str = ""
    path: str = ""
    stream_security: str = ""
    allow_insecure: bool = False
    sub_id: str = ""
    flow: str = ""
    sni: str = ""
    alpn: str = ""
    core_type: Optional[int] = None
    fingerprint: str = ""
    public_key: str = ""
    short_id: str = ""
    spider_x: str = ""
    extra: str = ""
    delay: Optional[int] = None
    speed: Optional[float] = None
    sort: Optional[int] = None
    message: str = ""

    message_in_db: str = ""
    db_delay: int = 0
    db_speed: float = 0.0

    @classmethod
    def from_row(cls, row) -> "Profile":
        d = dict(row)
        delay = d.get("Delay")
        speed = d.get("Speed")
        return cls(
            index_id=_text(d.get("IndexId")),
            config_type=_to_int(d.get("ConfigType")),
            remarks=_text(d.get("Remarks")),
            address=_text(d.get("Address")).strip(),
            port=_to_int(d.get("Port")),
            ports=_text(d.get("Ports")).strip(),
            uuid=_text(d.get("Id")).strip(),
            alter_id=_to_int(d.get("AlterId")),
            security=_text(d.get("Security")).strip(),
            network=_text(d.get("Network")).strip().lower(),
            header_type=_text(d.get("HeaderType")).strip(),
            request_host=_text(d.get("RequestHost")).strip(),
            path=_text(d.get("Path")).strip(),
            stream_security=_text(d.get("StreamSecurity")).strip().lower(),
            allow_insecure=_to_bool(d.get("AllowInsecure")),
            sub_id=_text(d.get("Subid")),
            flow=_text(d.get("Flow")).strip(),
            sni=_text(d.get("Sni")).strip(),
            alpn=_text(d.get("Alpn")).strip(),
            core_type=d.get("CoreType"),
            fingerprint=_text(d.get("Fingerprint")).strip(),
            public_key=_text(d.get("PublicKey")).strip(),
            short_id=_text(d.get("ShortId")).strip(),
            spider_x=_text(d.get("SpiderX")).strip(),
            extra=_text(d.get("Extra")).strip(),
            delay=delay,
            speed=speed,
            sort=d.get("Sort"),
            message=_text(d.get("Message")),
        )

    @property
    def protocol_name(self) -> str:
        return CONFIG_TYPE_NAMES.get(self.config_type, f"类型{self.config_type}")

    @property
    def display_name(self) -> str:
        name = (self.remarks or self.address or self.index_id).strip()
        name = re.sub(r"\s*\|.*$", "", name).strip()
        return name or self.index_id

    @property
    def is_info_row(self) -> bool:
        name = self.remarks.strip()
        if not name:
            return False
        return any(name.startswith(p) for p in _INFO_PREFIXES)

    @property
    def is_supported(self) -> bool:
        if self.config_type not in SUPPORTED_CONFIG_TYPES:
            return False
        if not self.address or self.port <= 0:
            return False
        if self.config_type in (1, 5, 7, 8) and not self.uuid:
            return False
        if self.stream_security == "reality" and not self.public_key:
            return False
        return True

    @property
    def uses_singbox(self) -> bool:
        return self.config_type in SINGBOX_CONFIG_TYPES

    @property
    def latency_url(self) -> str:
        return self.sni or self._first_host or self.address

    @property
    def _first_host(self) -> str:
        return split_hosts(self.request_host)[0] if self.request_host else ""

    @property
    def server_name(self) -> str:
        return self.sni or self._first_host or self.address

    @property
    def is_hy2_like(self) -> bool:
        return self.config_type in (7, 8)


def split_hosts(value: str) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in re.split(r"[,;]", value) if x.strip()]


@dataclass
class TestResult:
    profile: Profile
    latency_ms: Optional[int] = None
    speed_mbs: Optional[float] = None
    attempts: int = 0
    successes: int = 0
    error: str = ""
    needs_insecure: bool = False
    used_url: str = ""
    score: float = 0.0
    rank: int = 0
    status: str = ""
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.latency_ms is not None

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def latency_text(self) -> str:
        return f"{self.latency_ms} ms" if self.latency_ms is not None else "-"

    @property
    def speed_text(self) -> str:
        if self.speed_mbs is None:
            return "-"
        return f"{self.speed_mbs:.1f} MB/s"

    @property
    def status_text(self) -> str:
        if self.status:
            return self.status
        if self.ok:
            bits = ["可用"]
            if self.needs_insecure:
                bits.append("自签证书")
            if self.attempts > 1 and self.successes < self.attempts:
                bits.append(f"{self.successes}/{self.attempts} 次成功")
            return " · ".join(bits)
        return self.error or "不可用"

    @property
    def display_name(self) -> str:
        return self.profile.display_name


@dataclass
class TestOptions:
    mode: str = "balanced"  # balanced / latency / speed
    concurrency: int = 8
    speed_top_n: int = 5
    timeout: float = 5.0
    latency_url: str = "https://www.gstatic.com/generate_204"
    fallback_url: str = "https://cp.cloudflare.com/generate_204"
    speed_url: str = "https://speed.cloudflare.com/__down?bytes=4000000"
    allow_insecure_hy2: bool = True
    speed_max_bytes: int = 4_000_000
    speed_max_seconds: float = 8.0
    keep_debug: bool = False


@dataclass
class AppSettings:
    v2rayn_dir: str = ""
    mode: str = "balanced"
    concurrency: int = 8
    speed_top_n: int = 5
    allow_insecure_hy2: bool = True
    sys_proxy_mode: str = "keep"  # keep / on / off
    only_test: bool = False
    timeout: float = 5.0
    latency_url: str = "https://www.gstatic.com/generate_204"
    fallback_url: str = "https://cp.cloudflare.com/generate_204"
    speed_url: str = "https://speed.cloudflare.com/__down?bytes=4000000"
    advanced_open: bool = False
    window_geometry: str = ""
    extra: dict = field(default_factory=dict)
