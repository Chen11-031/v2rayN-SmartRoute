"""通过本地 SOCKS5 代理做一次真实的 HTTPS 请求，测量 TTFB 和下载速度。"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import struct
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit


class ProbeError(Exception):
    pass


@dataclass
class ProbeResult:
    status_code: int
    ttfb_ms: float
    body_bytes: int = 0
    total_ms: float = 0.0
    speed_mbs: float = 0.0
    url: str = ""


def _recv_exact(sock: socket.socket, count: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("读取超时")
        sock.settimeout(min(remaining, 5.0))
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ProbeError("连接被关闭")
        data.extend(chunk)
    return bytes(data)


def _socks5_connect(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    deadline: float,
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=max(0.1, deadline - time.monotonic()))
    try:
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        sock.sendall(b"\x05\x01\x00")
        if _recv_exact(sock, 2, deadline) != b"\x05\x00":
            raise ProbeError("SOCKS5 认证失败")

        try:
            ip = ipaddress.ip_address(target_host)
            if ip.version == 4:
                addr = b"\x01" + ip.packed
            else:
                addr = b"\x04" + ip.packed
        except ValueError:
            host_bytes = target_host.encode("idna")
            if len(host_bytes) > 255:
                raise ProbeError("域名过长")
            addr = b"\x03" + bytes([len(host_bytes)]) + host_bytes

        sock.sendall(b"\x05\x01\x00" + addr + struct.pack("!H", target_port))
        head = _recv_exact(sock, 4, deadline)
        if head[0] != 5:
            raise ProbeError("SOCKS5 响应无效")
        if head[1] != 0:
            reasons = {
                1: "服务器故障",
                2: "规则不允许",
                3: "网络不可达",
                4: "主机不可达",
                5: "连接被拒绝",
                6: "TTL 过期",
                7: "不支持的命令",
                8: "不支持的地址",
            }
            raise ProbeError(reasons.get(head[1], f"SOCKS5 错误 {head[1]}"))

        atyp = head[3]
        if atyp == 1:
            _recv_exact(sock, 4, deadline)
        elif atyp == 4:
            _recv_exact(sock, 16, deadline)
        elif atyp == 3:
            length = _recv_exact(sock, 1, deadline)[0]
            _recv_exact(sock, length, deadline)
        else:
            raise ProbeError("SOCKS5 地址类型无效")
        _recv_exact(sock, 2, deadline)
        return sock
    except Exception:
        try:
            sock.close()
        except Exception:
            pass
        raise


def probe_https(
    proxy_port: int,
    url: str,
    timeout: float = 5.0,
    max_bytes: int = 0,
    max_seconds: float = 0.0,
    proxy_host: str = "127.0.0.1",
) -> ProbeResult:
    """通过 SOCKS5 代理请求 url。

    max_bytes=0 时只读响应头，用于测延迟；否则最多下载 max_bytes 字节或 max_seconds 秒，
    用于测速。TLS 证书校验被关闭：这里只测连通性，不评价目标网站证书。
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ProbeError("只支持 http/https 测速地址")
    host = parts.hostname
    if not host:
        raise ProbeError("测速地址缺少主机名")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    deadline = time.monotonic() + timeout
    started = time.monotonic()
    sock: Optional[socket.socket] = None
    stream: Optional[socket.socket] = None
    try:
        sock = _socks5_connect(proxy_host, proxy_port, host, port, deadline)
        if parts.scheme == "https":
            ctx = ssl._create_unverified_context()
            stream = ctx.wrap_socket(sock, server_hostname=host)
            sock = None
        else:
            stream = sock
            sock = None

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36\r\n"
            "Accept: */*\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        stream.sendall(request)

        buf = bytearray()
        header_end = -1
        while header_end < 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待响应头超时")
            stream.settimeout(min(remaining, 5.0))
            chunk = stream.recv(8192)
            if not chunk:
                break
            buf.extend(chunk)
            header_end = buf.find(b"\r\n\r\n")
            if len(buf) > 65536:
                raise ProbeError("响应头过大")

        ttfb_ms = (time.monotonic() - started) * 1000.0
        if header_end < 0:
            raise ProbeError("没有收到完整响应头")

        header_bytes = bytes(buf[:header_end])
        first_line = header_bytes.split(b"\r\n", 1)[0].decode("latin1", "replace")
        fields = first_line.split()
        status = 0
        if len(fields) >= 2 and fields[0].startswith("HTTP/"):
            try:
                status = int(fields[1])
            except ValueError:
                status = 0
        if status == 0:
            raise ProbeError("HTTP 响应无效")

        body = bytearray(buf[header_end + 4 :])
        body_started = time.monotonic()

        if max_bytes > 0:
            limit = min(max_bytes, 64 * 1024 * 1024)
            while len(body) < limit:
                if max_seconds > 0 and time.monotonic() - body_started > max_seconds:
                    break
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    stream.settimeout(min(remaining, 2.0))
                    chunk = stream.recv(min(65536, limit - len(body)))
                except socket.timeout:
                    break
                if not chunk:
                    break
                body.extend(chunk)
                if max_seconds > 0 and time.monotonic() - body_started > max_seconds:
                    break

        elapsed = max(0.001, time.monotonic() - body_started)
        speed_mbs = (len(body) / 1_000_000.0) / elapsed if body else 0.0
        return ProbeResult(
            status_code=status,
            ttfb_ms=ttfb_ms,
            body_bytes=len(body),
            total_ms=(time.monotonic() - started) * 1000.0,
            speed_mbs=speed_mbs,
            url=url,
        )
    except (ProbeError, TimeoutError):
        raise
    except ssl.SSLError as exc:
        raise ProbeError(f"TLS 错误: {exc.reason or exc}") from exc
    except socket.timeout:
        raise ProbeError("连接超时") from None
    except ConnectionResetError:
        raise ProbeError("连接被重置") from None
    except OSError as exc:
        raise ProbeError(f"网络错误: {exc.strerror or exc}") from exc
    finally:
        for item in (stream, sock):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    pass


def is_acceptable_status(status: int) -> bool:
    return 200 <= status < 400
