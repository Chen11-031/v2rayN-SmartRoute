"""v2rayN 智能选线 - 图形界面。"""

from __future__ import annotations

import logging
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from smartroute import __version__, resources, winutil
from smartroute.models import AppSettings, Profile, TestOptions, TestResult
from smartroute.settings import backup_dir, load_settings, log_dir, save_settings
from smartroute.tester import TestEngine
from smartroute.v2rayn import (
    InstallInfo,
    V2rayNController,
    detect_install,
    inspect_install,
    read_profiles,
)


BG = "#F3F5F7"
CARD = "#FFFFFF"
TEXT = "#111827"
MUTED = "#6B7280"
BORDER = "#E3E7ED"
ACCENT = "#2563EB"
ACCENT_DARK = "#1D4ED8"
ACCENT_SOFT = "#EFF4FF"
GREEN = "#047857"
GREEN_SOFT = "#ECFDF5"
AMBER = "#B45309"
AMBER_SOFT = "#FFFBEB"
RED = "#B91C1C"
RED_SOFT = "#FEF2F2"
HEADER = "#111827"
HEADER_MUTED = "#9CA3AF"

MODE_LABELS = {
    "balanced": "综合最优（推荐）",
    "latency": "延迟最低",
    "speed": "速度最快",
}
MODE_BY_LABEL = {value: key for key, value in MODE_LABELS.items()}

SYSPROXY_LABELS = {
    "keep": "保持 v2rayN 当前设置",
    "on": "开启 Windows 系统代理",
    "off": "关闭 Windows 系统代理",
}
SYSPROXY_BY_LABEL = {value: key for key, value in SYSPROXY_LABELS.items()}


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("smartroute")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    path = os.path.join(log_dir(), "smartroute.log")
    handler = RotatingFileHandler(
        path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


class SmartRouteApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.log = setup_logging()
        self.settings = load_settings()
        self.install: Optional[InstallInfo] = None
        self.profiles: list[Profile] = []
        self.results: list[TestResult] = []
        self.result_by_id: dict[str, TestResult] = {}
        self.winner: Optional[TestResult] = None
        self.engine: Optional[TestEngine] = None
        self.worker: Optional[threading.Thread] = None
        self.cancel = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.is_testing = False
        self.is_applying = False
        self.started_at = 0.0
        self.advanced_visible = bool(self.settings.advanced_open)
        self._busy_widgets: list[tk.Widget] = []

        self._build_ui()
        self._load_initial()
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.root.title("v2rayN 智能选线")
        self.root.configure(bg=BG)
        self.root.geometry(self.settings.window_geometry or "1080x800")
        self.root.minsize(1080, 720)
        icon = resources.asset_file("app.ico")
        if icon:
            try:
                self.root.iconbitmap(default=icon)
            except tk.TclError:
                pass

        self._setup_style()
        self._build_header()
        self._build_body()

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base_font = ("Microsoft YaHei UI", 10)
        small_font = ("Microsoft YaHei UI", 9)
        self.root.option_add("*Font", base_font)

        style.configure(
            "TEntry",
            fieldbackground=CARD,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=6,
        )
        style.configure(
            "TCombobox",
            fieldbackground=CARD,
            background=CARD,
            foreground=TEXT,
            arrowcolor=TEXT,
            padding=4,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", CARD)],
            selectbackground=[("readonly", CARD)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=CARD,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            padding=4,
        )
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            rowheight=30,
            borderwidth=0,
            font=base_font,
        )
        style.configure(
            "Treeview.Heading",
            background="#F8FAFC",
            foreground=MUTED,
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(6, 7),
        )
        style.map(
            "Treeview",
            background=[("selected", "#DBEAFE")],
            foreground=[("selected", "#111827")],
        )
        style.map("Treeview.Heading", background=[("active", "#F1F5F9")])
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#E5E7EB",
            background=ACCENT,
            bordercolor="#E5E7EB",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=8,
        )
        style.configure("TScrollbar", background="#CBD5E1", troughcolor=CARD, borderwidth=0)
        self.small_font = small_font

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=HEADER, height=96)
        header.pack(fill="x")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=HEADER)
        left.pack(side="left", fill="both", expand=True, padx=24, pady=17)
        tk.Label(
            left,
            text="v2rayN 智能选线",
            bg=HEADER,
            fg="white",
            font=("Microsoft YaHei UI", 19, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            left,
            text="实测每个节点 → 自动选最优 → 一键切换生效",
            bg=HEADER,
            fg=HEADER_MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        right = tk.Frame(header, bg=HEADER)
        right.pack(side="right", padx=24, pady=24)
        tk.Label(
            right,
            text=f"v{__version__}",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        ).pack()
        tk.Label(
            right,
            text="只在本地测速，不上传节点信息",
            bg=HEADER,
            fg=HEADER_MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(pady=(6, 0))

        self.header = header

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        self._build_path_card(body)
        self._build_options_card(body)
        self._build_action_bar(body)
        self._build_results_card(body)
        self._build_footer(body)

    def _card(self, parent) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
            highlightthickness=1,
            bd=0,
        )
        return frame

    def _build_path_card(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(1, weight=1)

        tk.Label(
            card,
            text="v2rayN 目录",
            bg=CARD,
            fg=TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).grid(row=0, column=0, padx=(14, 10), pady=12, sticky="w")

        self.path_var = tk.StringVar(value=self.settings.v2rayn_dir)
        self.path_entry = ttk.Entry(card, textvariable=self.path_var)
        self.path_entry.grid(row=0, column=1, sticky="ew", pady=12)
        self.path_entry.bind("<FocusOut>", lambda _event: self._validate_path(save=True))
        self.path_entry.bind("<Return>", lambda _event: self._validate_path(save=True))

        self.browse_btn = self._button(
            card, "浏览…", self._browse_path, kind="ghost", width=7
        )
        self.browse_btn.grid(row=0, column=2, padx=(8, 10), pady=12)

        self.path_status = tk.Label(
            card,
            text="",
            bg=CARD,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        self.path_status.grid(row=1, column=0, columnspan=3, padx=14, pady=(0, 10), sticky="w")

        self._busy_widgets.extend([self.path_entry, self.browse_btn])

    def _build_options_card(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for col in range(8):
            card.grid_columnconfigure(col, weight=1 if col in (1, 4) else 0)

        def label(text, row, col, padx=(14, 6), pady=8):
            tk.Label(
                card,
                text=text,
                bg=CARD,
                fg=MUTED,
                font=("Microsoft YaHei UI", 9),
            ).grid(row=row, column=col, padx=padx, pady=pady, sticky="w")

        self.mode_var = tk.StringVar(value=MODE_LABELS.get(self.settings.mode, MODE_LABELS["balanced"]))
        self.concurrency_var = tk.IntVar(value=self.settings.concurrency)
        self.speed_n_var = tk.IntVar(value=self.settings.speed_top_n)
        self.allow_insecure_var = tk.BooleanVar(value=self.settings.allow_insecure_hy2)
        self.sysproxy_var = tk.StringVar(
            value=SYSPROXY_LABELS.get(self.settings.sys_proxy_mode, SYSPROXY_LABELS["keep"])
        )
        self.only_test_var = tk.BooleanVar(value=self.settings.only_test)

        label("测速模式", 0, 0)
        self.mode_combo = ttk.Combobox(
            card,
            textvariable=self.mode_var,
            values=list(MODE_LABELS.values()),
            state="readonly",
            width=20,
        )
        self.mode_combo.grid(row=0, column=1, sticky="w", padx=(0, 14), pady=8)

        label("同时测试", 0, 2)
        self.concurrency_spin = ttk.Spinbox(
            card, from_=2, to=16, textvariable=self.concurrency_var, width=4
        )
        self.concurrency_spin.grid(row=0, column=3, sticky="w", padx=(0, 4), pady=8)
        tk.Label(card, text="个节点", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(
            row=0, column=4, sticky="w", padx=(0, 14), pady=8
        )

        label("下载测速取前", 0, 5, padx=(0, 6))
        self.speed_n_spin = ttk.Spinbox(
            card, from_=0, to=12, textvariable=self.speed_n_var, width=4
        )
        self.speed_n_spin.grid(row=0, column=6, sticky="w", padx=(0, 4), pady=8)
        tk.Label(card, text="名", bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).grid(
            row=0, column=7, sticky="w", padx=(0, 14), pady=8
        )

        label("重启后", 1, 0)
        self.sysproxy_combo = ttk.Combobox(
            card,
            textvariable=self.sysproxy_var,
            values=list(SYSPROXY_LABELS.values()),
            state="readonly",
            width=24,
        )
        self.sysproxy_combo.grid(row=1, column=1, sticky="w", padx=(0, 14), pady=8)

        self.insecure_check = self._checkbutton(
            card,
            "包含 Hysteria2 自签证书节点（启用时会改该节点 allowInsecure）",
            self.allow_insecure_var,
        )
        self.insecure_check.grid(row=1, column=2, columnspan=4, sticky="w", padx=(0, 8), pady=8)

        self.only_test_check = self._checkbutton(card, "只测速，不切换", self.only_test_var)
        self.only_test_check.grid(row=1, column=6, columnspan=2, sticky="w", pady=8)

        self.advanced_btn = self._button(
            card,
            "高级设置 ▾" if not self.advanced_visible else "高级设置 ▴",
            self._toggle_advanced,
            kind="ghost",
            width=12,
        )
        self.advanced_btn.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

        self.advanced_frame = tk.Frame(card, bg=CARD)
        self.advanced_frame.grid(row=3, column=0, columnspan=8, sticky="ew", padx=14, pady=(0, 12))
        self.advanced_frame.grid_columnconfigure(1, weight=1)
        self.advanced_frame.grid_columnconfigure(4, weight=1)

        self.latency_url_var = tk.StringVar(value=self.settings.latency_url)
        self.fallback_url_var = tk.StringVar(value=self.settings.fallback_url)
        self.speed_url_var = tk.StringVar(value=self.settings.speed_url)
        self.timeout_var = tk.DoubleVar(value=self.settings.timeout)

        tk.Label(
            self.advanced_frame, text="延迟测试 URL", bg=CARD, fg=MUTED, font=self.small_font
        ).grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(self.advanced_frame, textvariable=self.latency_url_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=3
        )
        tk.Label(
            self.advanced_frame, text="超时(秒)", bg=CARD, fg=MUTED, font=self.small_font
        ).grid(row=0, column=4, sticky="w", padx=(12, 6), pady=3)
        ttk.Spinbox(
            self.advanced_frame, from_=2, to=20, increment=1, textvariable=self.timeout_var, width=5
        ).grid(row=0, column=5, sticky="w", pady=3)

        tk.Label(
            self.advanced_frame, text="备用 URL", bg=CARD, fg=MUTED, font=self.small_font
        ).grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(self.advanced_frame, textvariable=self.fallback_url_var).grid(
            row=1, column=1, columnspan=5, sticky="ew", pady=3
        )

        tk.Label(
            self.advanced_frame, text="速度测试 URL", bg=CARD, fg=MUTED, font=self.small_font
        ).grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(self.advanced_frame, textvariable=self.speed_url_var).grid(
            row=2, column=1, columnspan=5, sticky="ew", pady=3
        )

        if not self.advanced_visible:
            self.advanced_frame.grid_remove()

        self._busy_widgets.extend(
            [
                self.mode_combo,
                self.concurrency_spin,
                self.speed_n_spin,
                self.sysproxy_combo,
                self.insecure_check,
                self.only_test_check,
                self.advanced_btn,
            ]
        )

    def _build_action_bar(self, parent) -> None:
        bar = tk.Frame(parent, bg=BG)
        bar.grid(row=2, column=0, sticky="ew", pady=(2, 10))
        bar.grid_columnconfigure(3, weight=1)

        self.start_btn = self._button(
            bar,
            "⚡  一键测速并启用最优节点",
            self._start_test,
            kind="accent",
            width=30,
            font=("Microsoft YaHei UI", 11, "bold"),
            padx=18,
            pady=10,
        )
        self.start_btn.grid(row=0, column=0, sticky="w")

        self.stop_btn = self._button(bar, "停止", self._stop_test, kind="ghost", width=7)
        self.stop_btn.grid(row=0, column=1, padx=(8, 16), sticky="w")
        self.stop_btn.configure(state=tk.DISABLED)

        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100, value=0)
        self.progress.grid(row=0, column=3, sticky="ew", padx=(8, 12))

        self.progress_label = tk.Label(
            bar, text="", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9), width=10, anchor="e"
        )
        self.progress_label.grid(row=0, column=4, sticky="e")

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(
            bar,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        self.status_label.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))

    def _build_results_card(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=3, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        top = tk.Frame(card, bg=CARD)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 8))
        top.grid_columnconfigure(1, weight=1)

        tk.Label(
            top,
            text="测速结果",
            bg=CARD,
            fg=TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.count_var = tk.StringVar(value="尚未测速")
        tk.Label(
            top, textvariable=self.count_var, bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.copy_btn = self._button(top, "复制结果", self._copy_results, kind="ghost", width=9)
        self.copy_btn.grid(row=0, column=2, padx=(8, 0))
        self.enable_btn = self._button(
            top, "启用选中节点", self._enable_selected, kind="secondary", width=13
        )
        self.enable_btn.grid(row=0, column=3, padx=(8, 0))
        self.enable_btn.configure(state=tk.DISABLED)

        columns = ("rank", "name", "protocol", "latency", "speed", "score", "status")
        self.tree = ttk.Treeview(
            card, columns=columns, show="headings", selectmode="browse", height=12
        )
        headings = {
            "rank": ("排名", 50, "center"),
            "name": ("节点", 340, "w"),
            "protocol": ("协议", 100, "center"),
            "latency": ("延迟", 90, "e"),
            "speed": ("下载速度", 100, "e"),
            "score": ("评分", 70, "e"),
            "status": ("状态", 230, "w"),
        }
        for key, (title, width, anchor) in headings.items():
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, minwidth=40, anchor=anchor, stretch=(key == "name"))
        self.tree.tag_configure("ok", foreground=TEXT)
        self.tree.tag_configure("top1", background=GREEN_SOFT, foreground="#065F46")
        self.tree.tag_configure("warn", foreground=AMBER)
        self.tree.tag_configure("fail", foreground="#9CA3AF")
        self.tree.tag_configure("insecure", foreground=AMBER, background=AMBER_SOFT)

        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 12))
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 10), pady=(0, 12))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        self._busy_widgets.append(self.enable_btn)

    def _build_footer(self, parent) -> None:
        footer = tk.Frame(parent, bg=BG)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        footer.grid_columnconfigure(0, weight=1)

        self.footer_var = tk.StringVar(value="提示：测速期间不会影响当前正在使用的节点。")
        self.footer_label = tk.Label(
            footer,
            textvariable=self.footer_var,
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        self.footer_label.grid(row=0, column=0, sticky="ew")

        self.log_btn = self._button(footer, "打开日志", self._open_log, kind="ghost", width=9)
        self.log_btn.grid(row=0, column=1, padx=(8, 0))
        self.backup_btn = self._button(footer, "备份目录", self._open_backup, kind="ghost", width=9)
        self.backup_btn.grid(row=0, column=2, padx=(8, 0))

    def _button(
        self,
        parent,
        text: str,
        command,
        kind: str = "ghost",
        width: Optional[int] = None,
        font: tuple = ("Microsoft YaHei UI", 9),
        padx: int = 10,
        pady: int = 5,
    ) -> tk.Button:
        colors = {
            "accent": (ACCENT, "white", ACCENT_DARK, ACCENT),
            "secondary": (ACCENT_SOFT, ACCENT_DARK, "#DBEAFE", ACCENT_SOFT),
            "ghost": (CARD, MUTED, "#F1F5F9", CARD),
        }
        bg, fg, active_bg, active_fg = colors[kind]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active_bg,
            activeforeground=active_fg if kind != "ghost" else TEXT,
            disabledforeground="#9CA3AF",
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=font,
            padx=padx,
            pady=pady,
            width=width,
        )
        return button

    def _checkbutton(self, parent, text: str, variable: tk.BooleanVar) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=CARD,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )

    # ------------------------------------------------------------ lifecycle
    def _load_initial(self) -> None:
        info = inspect_install(self.settings.v2rayn_dir) if self.settings.v2rayn_dir else None
        if not info:
            info = detect_install(self.settings.v2rayn_dir)
        if info:
            self._set_install(info)
        else:
            self._set_no_install()
        self._restore_advanced()

    def _restore_advanced(self) -> None:
        if self.advanced_visible:
            self.advanced_frame.grid()
            self.advanced_btn.configure(text="高级设置 ▴")

    def _set_install(self, info: InstallInfo) -> None:
        self.install = info
        self.path_var.set(info.root)
        version = f"V{info.version}" if info.version else "v2rayN"
        running = " · 正在运行" if V2rayNController(info).is_running() else ""
        self.path_status.configure(
            text=f"✓ 已找到 {version} · 本地端口 {info.local_port}{running}",
            fg=GREEN,
        )
        self.start_btn.configure(state=tk.NORMAL)
        self._refresh_profile_count()

    def _set_no_install(self) -> None:
        self.install = None
        self.path_status.configure(
            text="✗ 没有找到 v2rayN，请选择 v2rayN.exe 所在的文件夹",
            fg=RED,
        )
        self.start_btn.configure(state=tk.DISABLED)
        self.count_var.set("尚未找到节点库")

    def _browse_path(self) -> None:
        if self.is_testing or self.is_applying:
            return
        initial = self.install.root if self.install else os.path.expanduser("~")
        selected = filedialog.askdirectory(
            title="选择 v2rayN.exe 所在的文件夹", initialdir=initial, mustexist=True
        )
        if not selected:
            return
        self.path_var.set(selected)
        self._validate_path(save=True)

    def _validate_path(self, save: bool = False) -> None:
        path = self.path_var.get().strip().strip('"')
        info = inspect_install(path)
        if info:
            self._set_install(info)
            self.settings.v2rayn_dir = info.root
            if save:
                save_settings(self._collect_settings())
        else:
            self._set_no_install()
            if save:
                self.settings.v2rayn_dir = path

    def _refresh_profile_count(self) -> None:
        info = self.install
        if not info:
            return
        threading.Thread(target=self._profile_count_worker, args=(info,), daemon=True).start()

    def _profile_count_worker(self, info: InstallInfo) -> None:
        try:
            profiles = read_profiles(info)
        except Exception as exc:
            self.log.warning("读取节点失败: %s", exc)
            return
        supported = sum(1 for p in profiles if p.is_supported and not p.is_info_row)
        info_rows = sum(1 for p in profiles if p.is_info_row)
        self.events.put(("profile_count", supported, info_rows, len(profiles)))

    # ------------------------------------------------------------ test flow
    def _start_test(self) -> None:
        if self.is_testing or self.is_applying:
            return
        info = self.install
        if not info:
            messagebox.showerror("找不到 v2rayN", "请先选择 v2rayN.exe 所在的文件夹。")
            return

        try:
            settings = self._collect_settings()
            options = self._make_options(settings)
        except ValueError as exc:
            messagebox.showerror("设置有问题", str(exc))
            return

        try:
            self.profiles = read_profiles(info)
        except Exception as exc:
            messagebox.showerror("读取节点失败", str(exc))
            return

        candidates = [p for p in self.profiles if p.is_supported and not p.is_info_row]
        skipped = len([p for p in self.profiles if p.is_info_row])
        if not candidates:
            messagebox.showerror("没有可测节点", "节点库里没有可测试的节点。")
            return

        self.settings = settings
        self.settings.v2rayn_dir = info.root
        save_settings(self.settings)

        self._clear_results()
        self.cancel = threading.Event()
        self.engine = TestEngine(
            candidates,
            options,
            info.xray_exe,
            info.singbox_exe,
            on_phase=lambda text: self.events.put(("phase", text)),
            on_progress=lambda done, total, phase: self.events.put(
                ("progress", done, total, phase)
            ),
            on_result=lambda result: self.events.put(("result", result)),
            on_log=lambda text: self.log.info(text),
            cancel_event=self.cancel,
        )
        self.is_testing = True
        self._set_busy(True)
        self.started_at = time.monotonic()
        self.count_var.set(f"正在测试 {len(candidates)} 个节点" + (f"（跳过 {skipped} 条订阅信息）" if skipped else ""))
        self.progress.configure(mode="determinate", maximum=len(candidates), value=0)
        self.progress_label.configure(text=f"0/{len(candidates)}")
        self.status_var.set("正在准备测速…")
        self.footer_var.set("测速期间不会影响当前正在使用的节点。")
        self.footer_label.configure(fg=MUTED)
        self.log.info("开始测速: %d 个候选节点, mode=%s, concurrency=%d", len(candidates), options.mode, options.concurrency)

        self.worker = threading.Thread(
            target=self._run_test_worker,
            args=(info, candidates, options),
            daemon=True,
        )
        self.worker.start()

    def _run_test_worker(self, info: InstallInfo, candidates: list[Profile], options: TestOptions) -> None:
        try:
            assert self.engine is not None
            results, winner = self.engine.run()
            if self.cancel.is_set():
                self.events.put(("canceled",))
                return
            self.events.put(("done", results, winner))
            settings = self.settings
            if winner is None or settings.only_test:
                self.events.put(("finished", winner))
                return

            allow_insecure = bool(winner.needs_insecure and settings.allow_insecure_hy2)
            self.events.put(("apply_start", winner))
            controller = V2rayNController(info)
            core_note = controller.apply(
                winner,
                results,
                sys_proxy_mode=settings.sys_proxy_mode,
                allow_insecure=allow_insecure,
            )
            if core_note:
                self.log.info(core_note)
            self.log.info("已启用节点: %s", winner.display_name)
            self.events.put(("applied", winner, core_note))
        except Exception as exc:
            self.log.exception("测速/切换失败")
            self.events.put(("error", str(exc)))

    def _stop_test(self) -> None:
        if not self.is_testing:
            return
        self.status_var.set("正在停止测速…")
        self.stop_btn.configure(state=tk.DISABLED)
        if self.engine:
            self.engine.cancel_all()
        self.cancel.set()

    def _enable_selected(self) -> None:
        if self.is_testing or self.is_applying:
            return
        selection = self.tree.selection()
        if not selection:
            return
        result = self.result_by_id.get(selection[0])
        if not result or not result.ok:
            messagebox.showwarning("节点不可用", "这个节点没有测速成功，不能启用。")
            return
        self._confirm_and_apply(result)

    def _on_tree_select(self, _event=None) -> None:
        if self.is_testing or self.is_applying:
            self.enable_btn.configure(state=tk.DISABLED)
            return
        selection = self.tree.selection()
        if selection:
            result = self.result_by_id.get(selection[0])
            self.enable_btn.configure(state=tk.NORMAL if result and result.ok else tk.DISABLED)
        else:
            self.enable_btn.configure(state=tk.DISABLED)

    def _on_tree_double_click(self, _event=None) -> None:
        if not self.is_testing and not self.is_applying:
            self._enable_selected()

    def _confirm_and_apply(self, result: TestResult) -> None:
        info = self.install
        if not info:
            return
        name = result.display_name
        extra = ""
        allow_insecure = False
        if result.needs_insecure and not result.profile.allow_insecure:
            if not self.allow_insecure_var.get():
                messagebox.showwarning(
                    "证书不受信任",
                    "该 Hysteria2 节点需要允许不安全证书才能使用。\n"
                    "请先勾选“包含 Hysteria2 自签证书节点”，或换一个节点。",
                )
                return
            allow_insecure = True
            extra = "\n\n注意：该节点使用自签证书，启用后会把该节点的 allowInsecure 改为 true。"

        if not messagebox.askyesno(
            "确认切换",
            f"即将启用：\n{name}\n\nv2rayN 会关闭并自动重启，当前连接会短暂中断。是否继续？{extra}",
        ):
            return

        settings = self._collect_settings()
        self.settings = settings
        save_settings(settings)
        self.is_applying = True
        self._set_busy(True)
        self.stop_btn.configure(state=tk.DISABLED)
        self.started_at = 0.0
        self.status_var.set(f"正在重启 v2rayN 并启用 {name}…")
        self.footer_var.set("正在切换节点，请稍候…")
        self.footer_label.configure(fg=MUTED)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def worker():
            try:
                controller = V2rayNController(info)
                core_note = controller.apply(
                    result,
                    self.results,
                    sys_proxy_mode=settings.sys_proxy_mode,
                    allow_insecure=allow_insecure,
                )
                if core_note:
                    self.log.info(core_note)
                self.log.info("手动启用节点: %s", name)
                self.events.put(("applied", result, core_note))
            except Exception as exc:
                self.log.exception("手动切换失败")
                self.events.put(("error", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------- UI events
    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "phase":
                self.status_var.set(event[1])
            elif kind == "progress":
                done, total, _phase = event[1], event[2], event[3]
                self.progress.configure(mode="determinate", maximum=max(1, total), value=done)
                self.progress_label.configure(text=f"{done}/{total}")
            elif kind == "result":
                self._upsert_result(event[1])
            elif kind == "profile_count":
                supported, info_rows, total = event[1], event[2], event[3]
                extra = f"，跳过 {info_rows} 条订阅信息" if info_rows else ""
                self.count_var.set(f"节点库：{total} 条，可测 {supported} 个{extra}")
            elif kind == "done":
                self._finish_test(event[1], event[2])
            elif kind == "finished":
                self._set_idle()
                winner = event[1] if len(event) > 1 else None
                if winner:
                    elapsed = time.monotonic() - self.started_at
                    self.footer_var.set(
                        f"测速完成（{elapsed:.0f} 秒）。最优节点：{winner.display_name} · "
                        f"{winner.latency_text} · {winner.speed_text}"
                    )
                    self.footer_label.configure(fg=GREEN)
                    self.status_var.set("测速完成（只测速，未切换）")
                else:
                    self.footer_var.set("没有找到可用节点。")
                    self.footer_label.configure(fg=RED)
                    self.status_var.set("没有可用节点")
            elif kind == "apply_start":
                self.is_applying = True
                self.stop_btn.configure(state=tk.DISABLED)
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
                self.status_var.set(f"正在重启 v2rayN 并启用 {event[1].display_name}…")
                self.footer_var.set("正在切换节点，请稍候…")
                self.footer_label.configure(fg=MUTED)
            elif kind == "applied":
                self.is_applying = False
                self.progress.stop()
                self.progress.configure(mode="determinate", value=self.progress["maximum"])
                winner = event[1]
                elapsed = time.monotonic() - self.started_at if self.started_at else 0
                self._set_idle()
                self.winner = winner
                self.footer_var.set(
                    f"✓ 已启用：{winner.display_name} · {winner.latency_text} · {winner.speed_text}"
                )
                self.footer_label.configure(fg=GREEN)
                note = event[2] if len(event) > 2 else ""
                status = (
                    f"已切换并重启 v2rayN（测速耗时 {elapsed:.0f} 秒）"
                    if elapsed > 0
                    else "已切换并重启 v2rayN"
                )
                if note:
                    status += " · 已自动处理 sing-box 兼容核心"
                self.status_var.set(status)
                self.root.bell()
            elif kind == "canceled":
                self._set_idle()
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self.status_var.set("已停止测速")
                self.footer_var.set("已停止，当前节点未改变。")
                self.footer_label.configure(fg=MUTED)
            elif kind == "error":
                self.is_applying = False
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._set_idle()
                message = event[1]
                self.status_var.set("操作失败")
                self.footer_var.set(f"操作失败：{message}")
                self.footer_label.configure(fg=RED)
                messagebox.showerror("操作失败", message)
        self.root.after(100, self._drain_events)

    def _finish_test(self, results: list[TestResult], winner: Optional[TestResult]) -> None:
        self.results = results
        self.result_by_id = {r.profile.index_id: r for r in results}
        self._rebuild_tree(results)
        successes = sum(1 for r in results if r.ok)
        self.count_var.set(f"可用 {successes} / 测试 {len(results)}")
        self.enable_btn.configure(state=tk.DISABLED)
        if winner:
            self.tree.selection_set(winner.profile.index_id)
            self.tree.see(winner.profile.index_id)
            self.enable_btn.configure(state=tk.NORMAL)
            self.status_var.set(
                f"测速完成，最优：{winner.display_name}（{winner.latency_text}）"
            )
        else:
            self.status_var.set("测速完成，但没有可用节点")

    def _set_idle(self) -> None:
        self.is_testing = False
        self.is_applying = False
        self.progress.stop()
        self._set_busy(False)
        self.stop_btn.configure(state=tk.DISABLED)
        if self.install:
            self.start_btn.configure(state=tk.NORMAL)

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in self._busy_widgets:
            try:
                widget.configure(state=state)
            except Exception:
                pass
        self.start_btn.configure(
            state=tk.DISABLED if busy or not self.install else tk.NORMAL
        )
        self.stop_btn.configure(state=tk.NORMAL if busy and self.is_testing else tk.DISABLED)
        if not busy:
            selection = self.tree.selection()
            selected = self.result_by_id.get(selection[0]) if selection else None
            self.enable_btn.configure(
                state=tk.NORMAL if selected and selected.ok else tk.DISABLED
            )

    def _clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.result_by_id.clear()
        self.results = []
        self.winner = None
        self.enable_btn.configure(state=tk.DISABLED)

    def _upsert_result(self, result: TestResult) -> None:
        self.result_by_id[result.profile.index_id] = result
        tag = self._tag_for(result)
        values = (
            str(result.rank) if result.rank else "",
            result.display_name,
            result.profile.protocol_name,
            result.latency_text,
            result.speed_text,
            f"{result.score:.0f}" if result.score else "",
            result.status_text,
        )
        iid = result.profile.index_id
        if self.tree.exists(iid):
            self.tree.item(iid, values=values, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))

    def _rebuild_tree(self, results: list[TestResult]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for result in results:
            tag = self._tag_for(result)
            self.tree.insert(
                "",
                "end",
                iid=result.profile.index_id,
                values=(
                    str(result.rank) if result.rank else "",
                    result.display_name,
                    result.profile.protocol_name,
                    result.latency_text,
                    result.speed_text,
                    f"{result.score:.0f}" if result.score else "",
                    result.status_text,
                ),
                tags=(tag,),
            )

    @staticmethod
    def _tag_for(result: TestResult) -> str:
        if not result.ok:
            return "fail"
        if result.rank == 1:
            return "top1"
        if result.needs_insecure:
            return "insecure"
        if result.attempts > 1 and result.successes < result.attempts:
            return "warn"
        return "ok"

    def _copy_results(self) -> None:
        if not self.results:
            return
        lines = ["排名\t节点\t协议\t延迟\t下载速度\t评分\t状态"]
        for result in self.results:
            lines.append(
                "\t".join(
                    [
                        str(result.rank or ""),
                        result.display_name,
                        result.profile.protocol_name,
                        result.latency_text,
                        result.speed_text,
                        f"{result.score:.0f}" if result.score else "",
                        result.status_text,
                    ]
                )
            )
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.status_var.set("测速结果已复制到剪贴板")

    # ---------------------------------------------------------------- tools
    def _toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.grid()
            self.advanced_btn.configure(text="高级设置 ▴")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_btn.configure(text="高级设置 ▾")

    def _open_log(self) -> None:
        try:
            os.startfile(log_dir())
        except Exception as exc:
            messagebox.showerror("无法打开日志目录", str(exc))

    def _open_backup(self) -> None:
        try:
            os.startfile(backup_dir())
        except Exception as exc:
            messagebox.showerror("无法打开备份目录", str(exc))

    # -------------------------------------------------------------- settings
    def _collect_settings(self) -> AppSettings:
        try:
            concurrency = int(self.concurrency_var.get())
        except (tk.TclError, ValueError):
            concurrency = 8
        try:
            speed_n = int(self.speed_n_var.get())
        except (tk.TclError, ValueError):
            speed_n = 5
        try:
            timeout = float(self.timeout_var.get())
        except (tk.TclError, ValueError):
            timeout = 5.0
        return AppSettings(
            v2rayn_dir=self.path_var.get().strip(),
            mode=MODE_BY_LABEL.get(self.mode_var.get(), "balanced"),
            concurrency=max(1, min(16, concurrency)),
            speed_top_n=max(0, min(12, speed_n)),
            allow_insecure_hy2=bool(self.allow_insecure_var.get()),
            sys_proxy_mode=SYSPROXY_BY_LABEL.get(self.sysproxy_var.get(), "keep"),
            only_test=bool(self.only_test_var.get()),
            timeout=max(2.0, min(20.0, timeout)),
            latency_url=self.latency_url_var.get().strip(),
            fallback_url=self.fallback_url_var.get().strip(),
            speed_url=self.speed_url_var.get().strip(),
            advanced_open=self.advanced_visible,
            window_geometry=self.root.geometry(),
            extra=self.settings.extra,
        )

    def _make_options(self, settings: AppSettings) -> TestOptions:
        for name, url in (
            ("延迟测试 URL", settings.latency_url),
            ("备用 URL", settings.fallback_url),
            ("速度测试 URL", settings.speed_url),
        ):
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError(f"{name} 必须以 http:// 或 https:// 开头")
        return TestOptions(
            mode=settings.mode,
            concurrency=settings.concurrency,
            speed_top_n=settings.speed_top_n,
            timeout=settings.timeout,
            latency_url=settings.latency_url,
            fallback_url=settings.fallback_url,
            speed_url=settings.speed_url,
            allow_insecure_hy2=settings.allow_insecure_hy2,
        )

    # ---------------------------------------------------------------- close
    def _on_close(self) -> None:
        if self.is_applying:
            messagebox.showinfo("正在切换节点", "v2rayN 正在重启，请等切换完成后再关闭本工具。")
            return
        if self.is_testing:
            if not messagebox.askyesno("测速进行中", "测速正在进行，确定要停止并退出吗？"):
                return
            self.cancel.set()
            if self.engine:
                self.engine.cancel_all()
            deadline = time.monotonic() + 5
            while self.is_testing and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.05)
        try:
            settings = self._collect_settings()
            settings.window_geometry = self.root.geometry()
            save_settings(settings)
        except Exception:
            pass
        self.root.destroy()


def _write_check_report() -> int:
    """供打包后自检使用：把关键路径和版本写到日志目录。"""
    settings = load_settings()
    info = detect_install(settings.v2rayn_dir)
    report = {
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "app_dir": resources.app_dir(),
        "bundled_singbox": resources.bundled_singbox(),
        "asset_icon": resources.asset_file("app.ico"),
        "v2rayn": None,
        "singbox_version": None,
        "core_compatible": None,
    }
    if info:
        controller = V2rayNController(info)
        version = controller._read_singbox_version(info.singbox_exe)
        report["v2rayn"] = {
            "root": info.root,
            "version": info.version,
            "local_port": info.local_port,
            "singbox_exe": info.singbox_exe,
            "xray_exe": info.xray_exe,
        }
        report["singbox_version"] = (
            ".".join(str(part) for part in version) if version else None
        )
        report["core_compatible"] = bool(
            version
            and (1, 12, 0) <= version < (1, 13, 0)
        )
    path = os.path.join(log_dir(), "build-check.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return 0


def main() -> int:
    winutil.set_dpi_awareness()
    setup_logging()
    if "--check" in sys.argv:
        return _write_check_report()
    try:
        root = tk.Tk()
        app = SmartRouteApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.getLogger("smartroute").exception("程序启动或运行失败")
        try:
            messagebox.showerror(
                "v2rayN 智能选线",
                f"程序发生错误：\n{exc}\n\n日志目录：\n{log_dir()}",
            )
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
