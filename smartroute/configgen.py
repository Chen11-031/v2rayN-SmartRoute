"""把 v2rayN 的 ProfileItem 转成一次性的 xray / sing-box 测速配置。"""

from __future__ import annotations

import json
from typing import Any

from .models import Profile, split_csv, split_hosts


class ConfigError(Exception):
    pass


def _stream_settings(profile: Profile) -> dict[str, Any]:
    network = (profile.network or "tcp").lower()
    security = (profile.stream_security or "").lower()
    host = profile.request_host.strip()
    first_host = split_hosts(host)[0] if host else ""
    path = profile.path or ""
    sni = profile.sni or first_host or profile.address
    fingerprint = profile.fingerprint or "chrome"

    stream: dict[str, Any] = {"network": network}

    if security == "tls":
        tls: dict[str, Any] = {
            "serverName": sni,
            "allowInsecure": bool(profile.allow_insecure),
            "fingerprint": fingerprint,
        }
        alpn = split_csv(profile.alpn)
        if alpn:
            tls["alpn"] = alpn
        stream["security"] = "tls"
        stream["tlsSettings"] = tls
    elif security == "reality":
        if not profile.public_key:
            raise ConfigError("Reality 节点缺少 publicKey")
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": profile.sni or first_host or profile.address,
            "fingerprint": fingerprint,
            "publicKey": profile.public_key,
            "shortId": profile.short_id or "",
            "spiderX": profile.spider_x or "",
            "show": False,
        }

    if network == "ws":
        ws: dict[str, Any] = {"path": path or "/"}
        if host:
            ws["headers"] = {"Host": host}
        stream["wsSettings"] = ws
    elif network == "grpc":
        grpc: dict[str, Any] = {}
        if path:
            grpc["serviceName"] = path.lstrip("/")
        if host:
            grpc["authority"] = host
        stream["grpcSettings"] = grpc
    elif network == "h2":
        http_settings: dict[str, Any] = {"path": path or "/"}
        if host:
            http_settings["host"] = split_hosts(host)
        stream["httpSettings"] = http_settings
    elif network == "httpupgrade":
        upgrade: dict[str, Any] = {"path": path or "/"}
        if host:
            upgrade["host"] = host
        stream["httpupgradeSettings"] = upgrade
    elif network == "xhttp":
        xhttp: dict[str, Any] = {"path": path or "/"}
        if host:
            xhttp["host"] = host
        if profile.header_type and profile.header_type.lower() not in ("", "none"):
            xhttp["mode"] = profile.header_type
        if profile.extra:
            try:
                xhttp["extra"] = json.loads(profile.extra)
            except Exception:
                pass
        stream["xhttpSettings"] = xhttp
    elif network == "kcp":
        kcp: dict[str, Any] = {"header": {"type": profile.header_type or "none"}}
        if path:
            kcp["seed"] = path
        if host:
            kcp["header"]["domain"] = host
        stream["kcpSettings"] = kcp
    elif network == "quic":
        quic: dict[str, Any] = {
            "security": host,
            "key": path,
            "header": {"type": profile.header_type or "none"},
        }
        stream["quicSettings"] = quic
        if security == "tls":
            stream.setdefault("tlsSettings", {})["serverName"] = sni
    elif network == "tcp":
        if (profile.header_type or "").lower() == "http":
            request_host = split_hosts(host)
            stream["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "version": "1.1",
                        "method": "GET",
                        "path": [path or "/"],
                        "headers": {"Host": request_host} if request_host else {},
                    },
                }
            }
    return stream


def _xray_outbound(profile: Profile) -> dict[str, Any]:
    ct = profile.config_type
    stream = _stream_settings(profile)
    out: dict[str, Any] = {"tag": "proxy", "streamSettings": stream}

    if ct == 1:  # VMess
        user = {
            "id": profile.uuid,
            "alterId": profile.alter_id,
            "security": profile.security if profile.security else "auto",
            "email": "t@t.tt",
        }
        out["protocol"] = "vmess"
        out["settings"] = {
            "vnext": [
                {
                    "address": profile.address,
                    "port": profile.port,
                    "users": [user],
                }
            ]
        }
    elif ct == 5:  # VLESS
        user: dict[str, Any] = {
            "id": profile.uuid,
            "encryption": profile.security if profile.security else "none",
            "email": "t@t.tt",
        }
        if profile.flow:
            user["flow"] = profile.flow
        out["protocol"] = "vless"
        out["settings"] = {
            "vnext": [
                {
                    "address": profile.address,
                    "port": profile.port,
                    "users": [user],
                }
            ]
        }
    elif ct == 6:  # Trojan
        out["protocol"] = "trojan"
        out["settings"] = {
            "servers": [
                {
                    "address": profile.address,
                    "port": profile.port,
                    "password": profile.uuid,
                    "email": "t@t.tt",
                }
            ]
        }
    elif ct == 3:  # Shadowsocks
        out["protocol"] = "shadowsocks"
        out["settings"] = {
            "servers": [
                {
                    "address": profile.address,
                    "port": profile.port,
                    "password": profile.uuid,
                    "method": profile.security or "none",
                    "ota": False,
                    "level": 1,
                }
            ]
        }
    elif ct in (4, 10):  # SOCKS / HTTP
        server: dict[str, Any] = {
            "address": profile.address,
            "port": profile.port,
            "level": 1,
        }
        if profile.security and profile.uuid:
            server["users"] = [
                {"user": profile.security, "pass": profile.uuid, "level": 1}
            ]
        out["protocol"] = "socks" if ct == 4 else "http"
        out["settings"] = {"servers": [server]}
    else:
        raise ConfigError(f"xray 不支持的协议类型: {ct}")

    out["mux"] = {"enabled": False, "concurrency": -1}
    return out


def _singbox_outbound(profile: Profile, force_insecure: bool) -> dict[str, Any]:
    ct = profile.config_type
    insecure = bool(force_insecure or profile.allow_insecure)
    server_name = profile.server_name

    if ct == 7:  # Hysteria2
        out: dict[str, Any] = {
            "type": "hysteria2",
            "tag": "proxy",
            "server": profile.address,
            "password": profile.uuid,
            "tls": {
                "enabled": True,
                "server_name": server_name,
                "insecure": insecure,
            },
            "up_mbps": 100,
            "down_mbps": 100,
        }
        if profile.ports:
            ports = [p.strip().replace("-", ":") for p in profile.ports.split(",") if p.strip()]
            if ports:
                out["server_ports"] = ports
                out["hop_interval"] = "30s"
            else:
                out["server_port"] = profile.port
        else:
            out["server_port"] = profile.port
        if profile.path:
            out["obfs"] = {"type": "salamander", "password": profile.path.strip()}
        return out

    if ct == 8:  # TUIC
        alpn = split_csv(profile.alpn) or ["h3"]
        out = {
            "type": "tuic",
            "tag": "proxy",
            "server": profile.address,
            "server_port": profile.port,
            "uuid": profile.uuid,
            "password": profile.security,
            "congestion_control": profile.header_type or "bbr",
            "udp_relay_mode": "native",
            "tls": {
                "enabled": True,
                "server_name": server_name,
                "insecure": insecure,
                "alpn": alpn,
            },
        }
        return out

    raise ConfigError(f"sing-box 不支持的协议类型: {ct}")


def build_core_config(
    profile: Profile,
    port: int,
    force_insecure: bool = False,
) -> tuple[str, dict[str, Any]]:
    """返回 ("xray" | "singbox", config dict)。"""
    if not profile.address or profile.port <= 0:
        raise ConfigError("节点地址或端口为空")

    if profile.config_type in (7, 8):
        outbound = _singbox_outbound(profile, force_insecure)
        cfg = {
            "log": {"level": "warn", "timestamp": False},
            "inbounds": [
                {
                    "type": "socks",
                    "tag": "socks",
                    "listen": "127.0.0.1",
                    "listen_port": port,
                }
            ],
            "outbounds": [outbound],
        }
        return "singbox", cfg

    outbound = _xray_outbound(profile)
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        ],
        "outbounds": [outbound],
    }
    return "xray", cfg
