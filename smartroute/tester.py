"""测速引擎：真实启动核心 -> 本地 SOCKS -> HTTPS 请求 -> 评分排序。"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import netprobe
from .configgen import ConfigError, build_core_config
from .models import Profile, TestOptions, TestResult
from .winutil import CREATE_NO_WINDOW


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _clean_error(text: str, max_len: int = 90) -> str:
    text = ANSI_RE.sub("", text or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("failed to ", "")
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _last_log_line(log_path: str) -> str:
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle.read().splitlines() if line.strip()]
        return lines[-1] if lines else ""
    except Exception:
        return ""


@dataclass
class _CoreContext:
    temp_dir: str
    process: subprocess.Popen
    port: int
    log_path: str
    log_handle: object


class TestEngine:
    def __init__(
        self,
        profiles: list[Profile],
        options: TestOptions,
        xray_exe: str,
        singbox_exe: str,
        on_phase: Callable[[str], None],
        on_progress: Callable[[int, int, str], None],
        on_result: Callable[[TestResult], None],
        on_log: Callable[[str], None],
        cancel_event: threading.Event,
    ):
        self.profiles = profiles
        self.options = options
        self.xray_exe = xray_exe
        self.singbox_exe = singbox_exe
        self.on_phase = on_phase
        self.on_progress = on_progress
        self.on_result = on_result
        self.on_log = on_log
        self.cancel = cancel_event
        self._procs_lock = threading.Lock()
        self._active_procs: set[subprocess.Popen] = set()
        self._results: dict[str, TestResult] = {}

    # ------------------------------------------------------------------ run
    def run(self) -> tuple[list[TestResult], Optional[TestResult]]:
        candidates = [p for p in self.profiles if p.is_supported and not p.is_info_row]
        results: list[TestResult] = []
        total = len(candidates)
        self.on_phase(f"正在测试延迟（0/{total}）")
        self.on_progress(0, total, "延迟测试")

        done = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, self.options.concurrency)
        ) as executor:
            futures = {executor.submit(self._test_latency, profile): profile for profile in candidates}
            for future in concurrent.futures.as_completed(futures):
                if self.cancel.is_set():
                    for pending in futures:
                        pending.cancel()
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - 防御性
                    profile = futures[future]
                    result = TestResult(profile=profile, error=_clean_error(str(exc)))
                done += 1
                self._results[result.profile.index_id] = result
                results.append(result)
                self.on_result(result)
                self.on_progress(done, total, "延迟测试")
                self.on_phase(f"正在测试延迟（{done}/{total}）：{result.display_name}")

        if self.cancel.is_set():
            self.on_phase("已停止")
            ordered = self._order_results(results)
            return ordered, None

        successes = [r for r in results if r.ok]
        self.on_log(f"延迟测试完成：成功 {len(successes)} / {len(results)}")

        speed_candidates: list[TestResult] = []
        if successes and self.options.speed_top_n > 0 and self.options.mode in ("balanced", "speed"):
            speed_candidates = sorted(successes, key=lambda r: r.latency_ms or 999999)[
                : max(1, self.options.speed_top_n)
            ]
            self.on_phase(f"正在测速（0/{len(speed_candidates)}）")
            self.on_progress(0, len(speed_candidates), "下载测速")
            for index, result in enumerate(speed_candidates, 1):
                if self.cancel.is_set():
                    break
                self.on_phase(
                    f"正在测速（{index}/{len(speed_candidates)}）：{result.display_name}"
                )
                self._test_speed(result)
                self.on_result(result)
                self.on_progress(index, len(speed_candidates), "下载测速")
            self.on_log("下载测速完成")

        if self.cancel.is_set():
            self.on_phase("已停止")
            return self._order_results(results), None

        ordered = self._order_results(results)
        winner = next((r for r in ordered if r.ok and not r.skipped), None)
        if winner:
            self.on_phase(
                f"测速完成，最优节点：{winner.display_name}（{winner.latency_text}）"
            )
        else:
            self.on_phase("测速完成，但没有可用节点")
        return ordered, winner

    def cancel_all(self) -> None:
        self.cancel.set()
        with self._procs_lock:
            procs = list(self._active_procs)
        for proc in procs:
            self._terminate(proc)

    # -------------------------------------------------------- test one node
    def _test_latency(self, profile: Profile) -> TestResult:
        result = TestResult(profile=profile)
        if self.cancel.is_set():
            result.error = "已取消"
            result.skipped = True
            return result

        force_insecure = profile.allow_insecure

        # Hysteria2 / TUIC 先用严格证书；失败后再用 allowInsecure 重试一次。
        if profile.is_hy2_like and not profile.allow_insecure and self.options.allow_insecure_hy2:
            strict = self._latency_once(profile, force_insecure=False)
            if strict.ok:
                return strict
            if self.cancel.is_set():
                strict.error = "已取消"
                strict.skipped = True
                return strict
            insecure = self._latency_once(profile, force_insecure=True)
            if insecure.ok:
                insecure.needs_insecure = True
                insecure.status = "可用 · 自签证书"
                return insecure
            strict.error = self._merge_error(strict.error, insecure.error)
            return strict

        return self._latency_once(profile, force_insecure=force_insecure)

    def _latency_once(self, profile: Profile, force_insecure: bool) -> TestResult:
        result = TestResult(profile=profile)
        ctx: Optional[_CoreContext] = None
        try:
            ctx = self._start_core(profile, force_insecure)
        except ConfigError as exc:
            result.error = _clean_error(str(exc))
            return result
        except Exception as exc:
            result.error = _clean_error(str(exc))
            return result

        try:
            latencies: list[int] = []
            errors: list[str] = []
            urls = [self.options.latency_url]
            if self.options.fallback_url and self.options.fallback_url != self.options.latency_url:
                urls.append(self.options.fallback_url)

            for attempt in range(2):
                if self.cancel.is_set():
                    result.error = "已取消"
                    result.skipped = True
                    break
                result.attempts += 1
                success = False
                # 每次尝试共用一份超时预算，避免主/备用地址各等一遍导致整体耗时翻倍。
                attempt_deadline = time.monotonic() + self.options.timeout
                for index, url in enumerate(urls):
                    remaining = attempt_deadline - time.monotonic()
                    if index > 0 and remaining < 0.6:
                        break
                    try:
                        probe = netprobe.probe_https(
                            ctx.port,
                            url,
                            timeout=max(0.5, remaining),
                        )
                        if not netprobe.is_acceptable_status(probe.status_code):
                            errors.append(f"HTTP {probe.status_code}")
                            continue
                        latencies.append(int(round(probe.ttfb_ms)))
                        result.successes += 1
                        result.used_url = url
                        success = True
                        break
                    except Exception as exc:
                        errors.append(_clean_error(str(exc)))
                if not success and attempt == 0:
                    time.sleep(0.15)
            if latencies:
                result.latency_ms = min(latencies)
                result.error = ""
            elif not result.skipped:
                result.error = errors[-1] if errors else "不可用"
            return result
        finally:
            self._stop_core(ctx)
            if ctx and not self.options.keep_debug:
                shutil.rmtree(ctx.temp_dir, ignore_errors=True)

    def _test_speed(self, result: TestResult) -> None:
        ctx: Optional[_CoreContext] = None
        try:
            ctx = self._start_core(result.profile, force_insecure=result.needs_insecure)
        except Exception as exc:
            self.on_log(f"测速启动失败 {result.display_name}: {exc}")
            return
        try:
            timeout = max(self.options.timeout, self.options.speed_max_seconds + 2)
            try:
                probe = netprobe.probe_https(
                    ctx.port,
                    self.options.speed_url,
                    timeout=timeout,
                    max_bytes=self.options.speed_max_bytes,
                    max_seconds=self.options.speed_max_seconds,
                )
                if netprobe.is_acceptable_status(probe.status_code) and probe.body_bytes > 100_000:
                    result.speed_mbs = round(probe.speed_mbs, 2)
                    self.on_log(
                        f"测速 {result.display_name}: {result.speed_mbs:.2f} MB/s "
                        f"({probe.body_bytes / 1_000_000:.1f} MB / {probe.total_ms / 1000:.1f}s)"
                    )
                else:
                    self.on_log(
                        f"测速无有效数据 {result.display_name}: HTTP {probe.status_code}, "
                        f"{probe.body_bytes} bytes"
                    )
            except Exception as exc:
                self.on_log(f"测速失败 {result.display_name}: {exc}")
        finally:
            self._stop_core(ctx)
            if ctx and not self.options.keep_debug:
                shutil.rmtree(ctx.temp_dir, ignore_errors=True)

    # ------------------------------------------------------------- core ops
    def _start_core(self, profile: Profile, force_insecure: bool) -> _CoreContext:
        kind, config = build_core_config(profile, 0, force_insecure=force_insecure)
        port = _free_port()
        config["inbounds"][0]["port" if kind == "xray" else "listen_port"] = port

        exe = self.xray_exe if kind == "xray" else self.singbox_exe
        if not exe or not os.path.isfile(exe):
            raise ConfigError(f"未找到 {kind} 核心程序")

        temp_dir = tempfile.mkdtemp(prefix="v2rayN-smartroute-")
        config_path = os.path.join(temp_dir, "config.json")
        log_path = os.path.join(temp_dir, "core.log")
        log_handle = None
        proc: Optional[subprocess.Popen] = None
        try:
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=1)
            log_handle = open(log_path, "wb")
            proc = subprocess.Popen(
                [exe, "run", "-c", config_path],
                cwd=temp_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_handle,
                creationflags=CREATE_NO_WINDOW,
            )
            with self._procs_lock:
                self._active_procs.add(proc)

            deadline = time.monotonic() + 4.0
            ready = False
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                        ready = True
                        break
                except OSError:
                    if self.cancel.is_set():
                        break
                    time.sleep(0.05)
            if not ready:
                message = _last_log_line(log_path)
                if proc.poll() is None:
                    message = message or "核心启动超时"
                raise ConfigError(_clean_error(message or "核心启动失败"))
            time.sleep(0.05)
            return _CoreContext(temp_dir, proc, port, log_path, log_handle)
        except Exception:
            if proc is not None:
                self._terminate(proc)
            if log_handle is not None:
                try:
                    log_handle.close()
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _stop_core(self, ctx: Optional[_CoreContext]) -> None:
        if not ctx:
            return
        self._terminate(ctx.process)
        try:
            ctx.log_handle.close()
        except Exception:
            pass

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        with self._procs_lock:
            self._active_procs.discard(proc)

    # -------------------------------------------------------------- scoring
    def _order_results(self, results: list[TestResult]) -> list[TestResult]:
        ok = [r for r in results if r.ok and not r.skipped]
        failed = [r for r in results if not r.ok or r.skipped]
        if not ok:
            failed.sort(key=lambda r: r.display_name)
            return failed

        best_latency = min((r.latency_ms or 999999) for r in ok)
        best_speed = max((r.speed_mbs or 0.0) for r in ok)

        for result in ok:
            latency = result.latency_ms or 999999
            latency_score = best_latency / latency * 100.0
            speed = result.speed_mbs or 0.0
            speed_score = (speed / best_speed * 100.0) if best_speed > 0 else 0.0
            if self.options.mode == "latency":
                score = latency_score
            elif self.options.mode == "speed":
                if speed > 0:
                    score = speed_score * 0.9 + latency_score * 0.1
                else:
                    score = latency_score * 0.2
            else:
                if speed > 0:
                    score = latency_score * 0.55 + speed_score * 0.45
                else:
                    score = latency_score * 0.55
            stability = 0.8 + 0.2 * result.success_rate
            score *= stability
            if result.needs_insecure:
                score *= 0.97
            result.score = round(score, 2)

        ok.sort(key=lambda r: (-r.score, r.latency_ms or 999999, -(r.speed_mbs or 0)))
        for index, result in enumerate(ok, 1):
            result.rank = index
            bits = ["可用"]
            if result.needs_insecure:
                bits.append("自签证书")
            if result.attempts > 1 and result.successes < result.attempts:
                bits.append(f"{result.successes}/{result.attempts} 次成功")
            result.status = " · ".join(bits)

        failed.sort(key=lambda r: (0 if not r.skipped else 1, r.display_name))
        for result in failed:
            result.rank = 0
        return ok + failed

    @staticmethod
    def _merge_error(first: str, second: str) -> str:
        first = first or ""
        second = second or ""
        if second and second not in first:
            return _clean_error(f"{first} / {second}" if first else second)
        return _clean_error(first)
