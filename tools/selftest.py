"""开发用自测：python tools/selftest.py [all|quick]

不启动 UI，直接跑测速引擎（不会修改 v2rayN 配置）。
"""

from __future__ import annotations

import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from smartroute import v2rayn  # noqa: E402
from smartroute.models import TestOptions  # noqa: E402
from smartroute.tester import TestEngine  # noqa: E402


def pick(profiles, mode: str):
    if mode == "all":
        return [p for p in profiles if p.is_supported and not p.is_info_row]
    selected = []
    for p in profiles:
        if p.is_info_row or not p.is_supported:
            continue
        if "06" in p.remarks and p.stream_security == "reality" and len(selected) < 1:
            selected.append(p)
        elif p.network == "ws" and "东京01" in p.remarks and len(selected) < 2:
            selected.append(p)
        elif p.config_type == 7 and p.port == 60000 and len(selected) < 3:
            selected.append(p)
    return selected


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "quick"
    info = v2rayn.detect_install()
    if not info:
        print("v2rayN not found")
        return 1
    profiles = v2rayn.read_profiles(info)
    selected = pick(profiles, mode)
    print(f"selected {len(selected)} profiles")
    for p in selected[:10]:
        print(" -", p.remarks, p.config_type, p.address, p.port)
    cancel = threading.Event()

    def phase(text):
        print("PHASE:", text, flush=True)

    def progress(done, total, phase_name):
        pass

    def result(item):
        print(
            "RESULT:",
            item.display_name,
            item.latency_text,
            item.speed_text,
            item.score,
            item.status_text,
            flush=True,
        )

    def log(text):
        print("LOG:", text, flush=True)

    opts = TestOptions(
        concurrency=6,
        speed_top_n=3,
        allow_insecure_hy2=True,
        keep_debug=True,
    )
    engine = TestEngine(
        selected,
        opts,
        info.xray_exe,
        info.singbox_exe,
        phase,
        progress,
        result,
        log,
        cancel,
    )
    results, winner = engine.run()
    print("WINNER:", winner.display_name if winner else None)
    for item in results:
        print(
            item.rank,
            item.display_name,
            item.latency_text,
            item.speed_text,
            item.score,
            item.status_text,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
