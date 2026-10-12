# -*- coding: utf-8 -*-
"""一键环境配置 v0.2.7 —— 主程序（tkinter，零第三方依赖）。

路线驱动：选职业线路 → 勾选工具 → 选版本 → 一键安装 / 按路体检。
v0.2.7 调整：设置页「存储位置」可自定义数据目录（托管安装 / 下载缓存 / 状态 /
快照整体迁移）；关于页路径以 %LOCALAPPDATA% 通用形式展示。
"""
import os
import queue
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk, messagebox

from core import catalog as catalog_mod
from core import detect, install, pathman, state

APP_TITLE = "一键环境配置"
APP_VERSION = "v0.2.7"

AUTO_TYPES = {"winget", "zip", "exe", "msi"}

# 配色
PRIMARY = "#2F6FED"
PRIMARY_DEEP = "#1D4ED8"
PRIMARY_LIGHT = "#E8F0FE"
ACCENT = "#4D8DFF"
NAV_BG = "#101828"
NAV_CARD = "#182236"
NAV_CARD_HOVER = "#1F2E44"
NAV_CARD_ACTIVE = "#203458"
NAV_TEXT = "#EDF2FA"
NAV_SUB = "#7C8FB0"
NAV_FADED = "#4E6280"
BODY_BG = "#F2F5F9"
CARD_BG = "#FFFFFF"
TEXT = "#1F2329"
SUB = "#646A73"
OK_BG, OK_FG = "#E7F4EB", "#1F7A33"
MISS_BG, MISS_FG = "#FDF0E7", "#C2410C"
NA_BG, NA_FG = "#F1F3F5", "#8A9199"
GROUP_BG = "#EEF3FA"
ROW_CHECKED = "#EFF5FF"
ROW_SELECTED_BAR = PRIMARY
ROW_HOVER = "#F5F9FF"
BORDER = "#E4E9F0"


class Worker:
    """后台任务：子线程执行，queue 回传 (stage, payload)；支持取消。"""

    def __init__(self, app):
        self.app = app
        self.q = queue.Queue()
        self.busy = False
        self.cancel_evt = None

    def submit(self, title, fn, *args, **kwargs):
        if self.busy:
            self.app.log("有任务正在执行，请稍候")
            return False
        self.busy = True
        self.cancel_evt = threading.Event()
        self.app.set_busy(True, title)

        def runner():
            try:
                fn(*args, **kwargs)
                self.q.put(("done", None))
            except Exception as e:  # noqa: BLE001
                self.q.put(("error", str(e)))
            finally:
                self.q.put(("busy_off", None))

        threading.Thread(target=runner, daemon=True).start()
        self.app.root.after(80, self._poll)
        return True

    def cancel(self):
        """请求取消当前任务；正在下载/子进程安装会尽快中断。"""
        if self.busy and self.cancel_evt:
            self.cancel_evt.set()
            self.app.log("已请求停止，正在中断当前步骤…")

    def cancel_requested(self):
        return bool(self.busy and self.cancel_evt and self.cancel_evt.is_set())

    def _poll(self):
        try:
            while True:
                stage, payload = self.q.get_nowait()
                if stage == "log":
                    self.app.log(payload)
                elif stage == "status":
                    self.app.set_status(payload)
                elif stage == "progress":
                    self.app.set_progress(*payload)
                elif stage == "detect_done":
                    self.app.on_detect_done(payload)
                elif stage == "installed_done":
                    self.app.refresh_installed()
                    self.app.refresh_market()
                elif stage == "done":
                    self.app.set_busy(False, "")
                elif stage == "error":
                    self.app.set_busy(False, "")
                    self.app.log("【失败】" + payload)
                    messagebox.showerror(APP_TITLE, payload, parent=self.app.root)
                elif stage == "busy_off":
                    self.busy = False
                    self.cancel_evt = None
        except queue.Empty:
            pass
        if self.busy:
            self.app.root.after(80, self._poll)


class EnvRow:
    """自定义环境行：复选框 + 版本下拉框 + 状态徽章，勾选直观。"""

    def __init__(self, app, env):
        self.app = app
        self.env = env
        self.var = tk.BooleanVar(value=False)

        self.frame = tk.Frame(app.install_list_inner, bg=CARD_BG, height=42, cursor="hand2")
        self.frame.pack(fill="x", padx=0, pady=0)
        self.frame.pack_propagate(False)

        self.bar = tk.Frame(self.frame, bg=CARD_BG, width=3)
        self.bar.pack(side="left", fill="y")

        self.check = tk.Checkbutton(self.frame, variable=self.var, command=self._toggle,
                                    bg=CARD_BG, activebackground=CARD_BG,
                                    highlightthickness=0, cursor="hand2", bd=0)
        self.check.pack(side="left", padx=(6, 2))

        self.name = tk.Label(self.frame, text=env["name"], bg=CARD_BG, fg=TEXT,
                             font=("Microsoft YaHei UI", 10), anchor="w", width=14)
        self.name.pack(side="left", padx=(0, 6))

        # 版本：多版本用下拉框，否则“—”
        self.combo = None
        self.ver_label = None
        vs = app._versions_of(env)
        if vs and len(vs) >= 2:
            self.combo = ttk.Combobox(self.frame, state="readonly", width=7,
                                      font=("Microsoft YaHei UI", 9))
            self.combo["values"] = ["v" + v for v in vs]
            cur = app._shown_version(env)
            self.combo.set("v" + cur)
            self.combo.pack(side="left", padx=(0, 8))
            self.combo.bind("<<ComboboxSelected>>", self._on_version)
        else:
            self.ver_label = tk.Label(self.frame, text="—", bg=CARD_BG, fg=NA_FG,
                                      font=("Microsoft YaHei UI", 9), width=7, anchor="w")
            self.ver_label.pack(side="left", padx=(0, 8))

        self.status = tk.Label(self.frame, text="未检测", bg=NA_BG, fg=NA_FG,
                               font=("Microsoft YaHei UI", 9), width=8, padx=4, pady=2)
        self.status.pack(side="left", padx=(0, 8))

        self.mode = tk.Label(self.frame, text="—", bg=CARD_BG, fg=NA_FG,
                             font=("Microsoft YaHei UI", 9), width=7, anchor="w")
        self.mode.pack(side="left", padx=(0, 8))

        self.desc = tk.Label(self.frame, text=env["desc"], bg=CARD_BG, fg=SUB,
                             font=("Microsoft YaHei UI", 9), anchor="w")
        self.desc.pack(side="left", fill="x", expand=True)

        # 单击行 → 勾选/取消勾选（与复选框一致）；双击行 → 打开官方页
        for w in (self.frame, self.bar, self.name, self.status, self.mode, self.desc):
            w.bind("<Button-1>", lambda e: self._click_row())
            w.bind("<Double-Button-1>", lambda e: self._double())
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))

    def _click_row(self):
        self.var.set(not self.var.get())
        self._toggle()

    def _double(self):
        self.app.install_or_guide(self.env["id"])

    def _hover(self, on):
        bg = self._row_bg()
        if on and not self.var.get():
            bg = ROW_HOVER
        self._paint(bg)

    def _row_bg(self):
        return ROW_CHECKED if self.var.get() else CARD_BG

    def _paint(self, bg):
        checked = self.var.get()
        self.frame.configure(bg=bg)
        self.bar.configure(bg=ACCENT if checked else bg)
        self.check.configure(bg=bg, activebackground=bg)
        self.name.configure(bg=bg)
        if self.ver_label:
            self.ver_label.configure(bg=bg)
        self.status.configure(bg=bg if self.status.cget("fg") == NA_FG else self.status.cget("background"))
        self.mode.configure(bg=bg)
        self.desc.configure(bg=bg)
        # 状态徽章独立底色
        self._paint_status()

    def _paint_status(self):
        r = self.app.detect_cache.get(self.env["id"])
        if r:
            if r["installed"]:
                self.status.configure(text="已装 " + r["version"], bg=OK_BG, fg=OK_FG)
            else:
                self.status.configure(text="缺失", bg=MISS_BG, fg=MISS_FG)
        else:
            self.status.configure(text="未检测", bg=NA_BG, fg=NA_FG)

    def _toggle(self):
        if self.var.get():
            self.app._finish_check(self.env["id"])
            self._paint(ROW_CHECKED)
        else:
            self.app.checked.get(self.app.current_path_id, set()).discard(self.env["id"])
            self.app._update_stat()
            self._paint(self._row_bg())

    def _on_version(self, _=None):
        v = self.combo.get().lstrip("v")
        self.app.selected_version.setdefault(self.app.current_path_id, {})[self.env["id"]] = v
        if self.var.get():
            self.app._update_stat()

    def set_checked(self, checked):
        self.var.set(checked)
        self._paint(ROW_CHECKED if checked else self._row_bg())

    def refresh_status(self):
        self._paint_status()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.catalog = catalog_mod.load_catalog()
        self.envs = {e["id"]: e for e in self.catalog["environments"]}
        self.paths = self.catalog["paths"]
        self.path_index = {p["id"]: p for p in self.paths}
        self.current_path_id = self.paths[0]["id"]
        self.checked = {}            # path_id -> set(env_id)
        self.selected_version = {}   # path_id -> {env_id: version}
        self.detect_cache = {}       # env_id -> {installed, version, detail}
        self.worker = Worker(self)
        self.log_expanded = False
        self.env_rows = {}           # env_id -> EnvRow（当前路线）
        self.group_collapsed = {}    # cat -> bool

        root.title("%s %s" % (APP_TITLE, APP_VERSION))
        root.geometry("1200x780")
        root.minsize(1040, 700)
        root.configure(bg=BODY_BG)
        try:
            tkfont.nametofont("TkDefaultFont").configure(
                family="Microsoft YaHei UI", size=10)
        except Exception:
            pass

        self._build_ui()
        self.switch_path(self.current_path_id, init=True)
        self.log("已加载 %d 条开发路线、%d 项环境目录" % (len(self.paths), len(self.envs)))

    # ================= 骨架 =================

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=28,
                        font=("Microsoft YaHei UI", 10), background="white",
                        fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"),
                        background="#F7F9FC", foreground=TEXT)

        head = tk.Frame(self.root, bg=PRIMARY, height=58)
        head.pack(fill="x")
        head.pack_propagate(False)
        logo = tk.Canvas(head, width=26, height=26, bg=PRIMARY, highlightthickness=0)
        logo.pack(side="left", padx=(16, 8))
        logo.create_oval(1, 1, 25, 25, fill="#FFFFFF", outline="")
        logo.create_text(13, 13, text="装", font=("Microsoft YaHei UI", 11, "bold"),
                         fill=PRIMARY)
        tk.Label(head, text=APP_TITLE, bg=PRIMARY, fg="white",
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        tk.Label(head, text="选路线 → 勾工具 → 选版本 → 一键安装 · 按路体检",
                 bg=PRIMARY, fg="#DCE7FF", font=("Microsoft YaHei UI", 10)).pack(side="left", padx=10)
        tk.Label(head, text="%s · 免安装版" % APP_VERSION, bg=PRIMARY, fg="#BFD4FF",
                 font=("Microsoft YaHei UI", 9)).pack(side="right", padx=16)

        warn = tk.Frame(self.root, bg="#FFF1F0")
        warn.pack(fill="x")
        tk.Label(warn, text="⚠ 该软件是免安装版，运行配置期间请勿关闭本窗口，关闭后将不再继续执行配置！",
                 bg="#FFF1F0", fg="#C62828",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(pady=3)

        body = tk.Frame(self.root, bg=BODY_BG)
        body.pack(fill="both", expand=True)
        self._build_nav(body)
        self._build_content(body)
        self._build_logbar(self.root)

        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", side="bottom")
        self.status = tk.Label(self.root, text="就绪", anchor="w", bg="#E9F0FD", fg=TEXT,
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(fill="x", side="bottom")

    # ---------- 左栏：路线导航 ----------

    def _build_nav(self, parent):
        nav = tk.Frame(parent, bg=NAV_BG, width=318)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        tk.Label(nav, text="开发路线", bg=NAV_BG, fg="#D6E4FF",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(nav, text="每条路线 = 一组对应工具，可自选安装", bg=NAV_BG, fg=NAV_FADED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=18)

        scroll = tk.Canvas(nav, bg=NAV_BG, highlightthickness=0, width=300)
        scroll.pack(fill="both", expand=True, padx=8, pady=8)
        inner = tk.Frame(scroll, bg=NAV_BG)
        win = scroll.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: scroll.configure(scrollregion=scroll.bbox("all")))
        scroll.bind("<Configure>", lambda e: scroll.itemconfigure(win, width=e.width))

        self._nav_scroll = scroll
        scroll.bind("<Enter>", lambda e: scroll.bind_all("<MouseWheel>", self._on_nav_wheel))
        scroll.bind("<Leave>", lambda e: scroll.unbind_all("<MouseWheel>"))

        self.path_items = {}
        for idx, p in enumerate(self.paths):
            item = self._make_path_card(inner, idx, p)
            item.pack(fill="x", pady=3)
            self.path_items[p["id"]] = item

        tk.Label(nav, text="点击路线查看其工具清单", bg=NAV_BG, fg=NAV_FADED,
                 font=("Microsoft YaHei UI", 8)).pack(side="bottom", pady=8)

    def _on_nav_wheel(self, event):
        self._nav_scroll.yview_scroll(-1 * (event.delta // 120), "units")

    def _make_path_card(self, parent, idx, path):
        frame = tk.Frame(parent, bg=NAV_CARD, cursor="hand2", height=62)
        frame.pack_propagate(False)
        bar = tk.Frame(frame, bg=NAV_CARD, width=3)
        bar.pack(side="left", fill="y")
        badge = tk.Canvas(frame, width=28, height=28, bg=NAV_CARD, highlightthickness=0)
        badge.pack(side="left", padx=(8, 8), pady=17)
        oval = badge.create_oval(1, 1, 27, 27, fill="#24344D", outline="")
        numtxt = badge.create_text(14, 14, text="%02d" % (idx + 1),
                                   font=("Consolas", 9, "bold"), fill="#9FB3CF")
        text_col = tk.Frame(frame, bg=NAV_CARD)
        text_col.pack(side="left", fill="both", expand=True, pady=8)
        name = tk.Label(text_col, text=path["name"], bg=NAV_CARD, fg="#B9C8DF",
                        font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        name.pack(fill="x")
        desc = tk.Label(text_col, text=path["desc"][:24], bg=NAV_CARD, fg=NAV_SUB,
                        font=("Microsoft YaHei UI", 8), anchor="w")
        desc.pack(fill="x")
        count = tk.Label(frame, text="%d 项" % len(path["env_ids"]), bg="#1E3050", fg="#8CA3C9",
                         font=("Microsoft YaHei UI", 8), padx=7, pady=2)
        count.pack(side="right", padx=10)

        def on_enter(_=None):
            if self.current_path_id != path["id"]:
                self._paint_card(frame, idx, NAV_CARD_HOVER)

        def on_leave(_=None):
            if self.current_path_id != path["id"]:
                self._paint_card(frame, idx, NAV_CARD)

        def on_click(_=None):
            self.switch_path(path["id"])

        for w in (frame, bar, badge, text_col, name, desc, count):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)
        frame._data = {"bar": bar, "badge": badge, "oval": oval, "numtxt": numtxt,
                       "name": name, "desc": desc, "count": count, "idx": idx}
        return frame

    def _paint_card(self, frame, idx, bg):
        d = frame._data
        active = (bg == NAV_CARD_ACTIVE)
        frame.configure(bg=bg)
        d["bar"].configure(bg=ACCENT if active else bg)
        d["badge"].configure(bg=bg)
        d["badge"].itemconfigure(d["oval"], fill="#4D8DFF" if active else "#24344D")
        d["badge"].itemconfigure(d["numtxt"], fill="#FFFFFF" if active else "#9FB3CF")
        d["name"].configure(bg=bg, fg="#FFFFFF" if active else "#B9C8DF")
        d["desc"].configure(bg=bg, fg="#8CA3C9" if active else NAV_SUB)
        d["count"].configure(bg="#2E5EA8" if active else "#1E3050",
                             fg="#FFFFFF" if active else "#8CA3C9")

    # ---------- 右栏：功能区 ----------

    def _build_content(self, parent):
        content = tk.Frame(parent, bg=BODY_BG)
        content.pack(side="left", fill="both", expand=True)

        tabs = tk.Frame(content, bg="white", height=44)
        tabs.pack(fill="x")
        tabs.pack_propagate(False)
        self.tab_labels = {}
        for i, (key, icon, text) in enumerate([("install", "◉", "安装环境"),
                                               ("detect", "◎", "环境体检"),
                                               ("installed", "◈", "已安装"),
                                               ("settings", "⚙", "设置"),
                                               ("about", "ℹ", "关于")]):
            lab = tk.Label(tabs, text="  %s  %s  " % (icon, text), bg="white", fg=SUB,
                           padx=14, pady=8, font=("Microsoft YaHei UI", 10, "bold"),
                           cursor="hand2")
            lab.pack(side="left", padx=(2 if i else 10, 0))
            lab.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            lab.bind("<Enter>", lambda e, l=lab: l.configure(bg="#EFF4FD"))
            lab.bind("<Leave>", lambda e, l=lab: l.configure(
                bg=PRIMARY if self._current_tab == l._key else "white",
                fg="white" if self._current_tab == l._key else SUB))
            lab._key = key
            self.tab_labels[key] = lab
        self._current_tab = "install"

        self.tab_frames = {}
        for key in ("install", "detect", "installed", "settings", "about"):
            f = tk.Frame(content, bg=BODY_BG)
            self.tab_frames[key] = f
        self._build_install_tab(self.tab_frames["install"])
        self._build_detect_tab(self.tab_frames["detect"])
        self._build_installed_tab(self.tab_frames["installed"])
        self._build_settings_tab(self.tab_frames["settings"])
        self._build_about_tab(self.tab_frames["about"])
        self.show_tab("install")

    def show_tab(self, key):
        self._current_tab = key
        for k, f in self.tab_frames.items():
            f.pack_forget()
        self.tab_frames[key].pack(fill="both", expand=True, padx=14, pady=12)
        for k, lab in self.tab_labels.items():
            lab.configure(bg=PRIMARY if k == key else "white",
                          fg="white" if k == key else SUB)

    def _tool_button(self, parent, text, cmd, order, primary=False, width=None):
        bg = PRIMARY if primary else "white"
        fg = "white" if primary else TEXT
        b = tk.Label(parent, text=text, bg=bg, fg=fg, padx=14, pady=5, cursor="hand2",
                     font=("Microsoft YaHei UI", 9, "bold"), relief="flat",
                     highlightthickness=1,
                     highlightbackground=PRIMARY if primary else BORDER)
        if width:
            b.configure(width=width)
        b.pack(side="left", padx=(2 if order else 4, 0), pady=4)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(
            bg=PRIMARY_DEEP if primary else "#F0F6FF"))
        b.bind("<Leave>", lambda e: b.configure(
            bg=PRIMARY if primary else "white"))
        return b

    def _card(self, parent):
        return tk.Frame(parent, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)

    # ---------- ① 安装页 ----------

    def _build_install_tab(self, tab):
        self.page_install = tk.Frame(tab, bg=BODY_BG)
        self.page_install.pack(fill="both", expand=True)

        head = self._card(self.page_install)
        head.pack(fill="x", pady=(0, 8))
        left = tk.Frame(head, bg=CARD_BG)
        left.pack(side="left", padx=14, pady=10)
        self.install_title = tk.Label(left, text="", bg=CARD_BG, fg=TEXT,
                                      font=("Microsoft YaHei UI", 15, "bold"))
        self.install_title.pack(anchor="w")
        self.install_desc = tk.Label(left, text="", bg=CARD_BG, fg=SUB,
                                     font=("Microsoft YaHei UI", 9))
        self.install_desc.pack(anchor="w", pady=(2, 0))
        right = tk.Frame(head, bg=CARD_BG)
        right.pack(side="right", padx=14)
        self.install_stat = tk.Label(right, text="", bg=PRIMARY_LIGHT, fg=PRIMARY_DEEP,
                                     font=("Microsoft YaHei UI", 10, "bold"), padx=12, pady=5)
        self.install_stat.pack(side="left")
        self.install_installed = tk.Label(right, text="", bg=OK_BG, fg=OK_FG,
                                          font=("Microsoft YaHei UI", 10, "bold"),
                                          padx=12, pady=5)
        self.install_installed.pack(side="left", padx=(8, 0))

        bar = self._card(self.page_install)
        bar.pack(fill="x", pady=(0, 8))
        self._tool_button(bar, "检测勾选", self.detect_checked, 0)
        self._tool_button(bar, "一键安装勾选", self.install_checked, 1, primary=True)
        self._tool_button(bar, "安装 / 打开", self.install_or_guide_checked, 2)
        self._tool_button(bar, "切换版本", self.switch_checked, 3)
        self._tool_button(bar, "选核心", self.check_core, 4)
        self._tool_button(bar, "全选", self.check_all, 5)
        self._tool_button(bar, "清空", self.clear_checked, 6)
        self.stop_btn = tk.Label(bar, text="■ 停止", bg="#E0E4EA", fg="#98A1AF",
                                 padx=16, pady=5, cursor="arrow",
                                 font=("Microsoft YaHei UI", 9, "bold"))
        self.stop_btn.pack(side="left", padx=8)
        self.stop_btn.bind("<Button-1>", lambda e: self.stop_task())
        tk.Label(bar, text="☑ 勾选环境；自动通道直接安装，引导型打开官方页；版本下拉选择",
                 bg=CARD_BG, fg=SUB, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)

        # 自定义行列表（Canvas 滚动）
        holder = self._card(self.page_install)
        holder.pack(fill="both", expand=True)
        self.install_scroll = tk.Canvas(holder, bg=CARD_BG, highlightthickness=0)
        self.install_scroll.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=self.install_scroll.yview)
        vsb.pack(side="right", fill="y")
        self.install_scroll.configure(yscrollcommand=vsb.set)
        self.install_list_inner = tk.Frame(self.install_scroll, bg=CARD_BG)
        self._list_win = self.install_scroll.create_window(
            (0, 0), window=self.install_list_inner, anchor="nw")
        self.install_list_inner.bind(
            "<Configure>", lambda e: self.install_scroll.configure(
                scrollregion=self.install_scroll.bbox("all")))
        self.install_scroll.bind(
            "<Configure>", lambda e: self.install_scroll.itemconfigure(
                self._list_win, width=e.width))
        self.install_scroll.bind("<Enter>",
                                 lambda e: self.install_scroll.bind_all(
                                     "<MouseWheel>", self._on_list_wheel))
        self.install_scroll.bind("<Leave>",
                                 lambda e: self.install_scroll.unbind_all("<MouseWheel>"))

    def _on_list_wheel(self, event):
        self.install_scroll.yview_scroll(-1 * (event.delta // 120), "units")

    # ---------- 勾选 / 版本 / 刷新 ----------

    def _versions_of(self, e):
        inst = e.get("install", {}) or {}
        if inst.get("type") == "zip" and inst.get("versions"):
            return inst["versions"]
        return []

    def _shown_version(self, e):
        vs = self._versions_of(e)
        if not vs:
            return ""
        return self.selected_version.get(self.current_path_id, {}).get(e["id"]) or vs[0]

    def _finish_check(self, env_id):
        path = self.path_index[self.current_path_id]
        self.checked.setdefault(path["id"], set()).add(env_id)
        self._update_stat()

    def _update_stat(self):
        path = self.path_index[self.current_path_id]
        s = self.checked.get(path["id"], set())
        installed_n = sum(1 for eid in path["env_ids"]
                          if eid in self.detect_cache and self.detect_cache[eid]["installed"])
        self.install_stat.configure(text="已勾选 %d / %d 项" % (len(s), len(path["env_ids"])))
        self.install_installed.configure(text="已装 %d / %d 项" % (installed_n, len(path["env_ids"])))

    def check_core(self):
        path = self.path_index[self.current_path_id]
        core = set(path.get("core_ids") or [])
        self.checked[path["id"]] = core
        for eid, row in self.env_rows.items():
            row.set_checked(eid in core)
        self._update_stat()
        self.log("已按核心推荐勾选 %d 项（可继续增删）" % len(core))

    def check_all(self):
        path = self.path_index[self.current_path_id]
        self.checked[path["id"]] = set(path["env_ids"])
        for row in self.env_rows.values():
            row.set_checked(True)
        self._update_stat()

    def clear_checked(self):
        path = self.path_index[self.current_path_id]
        self.checked[path["id"]] = set()
        for row in self.env_rows.values():
            row.set_checked(False)
        self._update_stat()

    def refresh_market(self):
        # 重建当前路线的环境行
        for w in self.install_list_inner.winfo_children():
            w.destroy()
        self.env_rows = {}
        path = self.path_index[self.current_path_id]
        envs = [self.envs[eid] for eid in path["env_ids"] if eid in self.envs]
        groups = {}
        for e in envs:
            groups.setdefault(e["category"], []).append(e)
        s = self.checked.get(path["id"], set())
        for cat, items in groups.items():
            collapsed = self.group_collapsed.get(cat, False)
            g = tk.Frame(self.install_list_inner, bg=GROUP_BG, height=30, cursor="hand2")
            g.pack(fill="x")
            g.pack_propagate(False)
            mark = "▸" if collapsed else "▾"
            lab = tk.Label(g, text="%s  %s（%d）" % (mark, cat, len(items)), bg=GROUP_BG,
                           fg="#3C4A5E", font=("Microsoft YaHei UI", 10, "bold"),
                           anchor="w", cursor="hand2")
            lab.pack(fill="both", padx=10)
            lab.bind("<Button-1>", lambda e, c=cat: self._toggle_group(c))
            if collapsed:
                continue
            for e in items:
                row = EnvRow(self, e)
                row.set_checked(e["id"] in s)
                row.refresh_status()
                self.env_rows[e["id"]] = row
        self._update_stat()

    def _toggle_group(self, cat):
        self.group_collapsed[cat] = not self.group_collapsed.get(cat, False)
        self.refresh_market()

    # ---------- ② 体检页 ----------

    def _build_detect_tab(self, tab):
        self.page_detect = tk.Frame(tab, bg=BODY_BG)
        self.page_detect.pack(fill="both", expand=True)

        head = self._card(self.page_detect)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="按路线体检", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(side="left", padx=14, pady=8)
        tk.Label(head, text="只检测当前路线的工具，不扫描无关项目", bg=CARD_BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)

        bar = self._card(self.page_detect)
        bar.pack(fill="x", pady=(0, 8))
        self._tool_button(bar, "检测本路线", self.detect_current_path, 0, primary=True)
        self._tool_button(bar, "安装缺失", self.install_missing, 1)
        tk.Label(bar, text="缺失项可一键安装（自动通道静默装，引导型打开官方页）",
                 bg=CARD_BG, fg=SUB, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)

        cols = ("env", "status", "version", "detail")
        self.detect_tree = ttk.Treeview(self.page_detect, columns=cols, show="headings")
        heads = {"env": ("环境", 180), "status": ("结果", 100), "version": ("版本", 180),
                 "detail": ("详情", 360)}
        for c, (t, w) in heads.items():
            self.detect_tree.heading(c, text=t)
            self.detect_tree.column(c, width=w, anchor="w")
        self.detect_tree.pack(fill="both", expand=True)
        self.detect_tree.tag_configure("ok", background=OK_BG, foreground=OK_FG)
        self.detect_tree.tag_configure("miss", background=MISS_BG, foreground=MISS_FG)

    def refresh_detect_tree(self):
        self.detect_tree.delete(*self.detect_tree.get_children())
        path = self.path_index[self.current_path_id]
        for eid in path["env_ids"]:
            e = self.envs.get(eid)
            r = self.detect_cache.get(eid)
            if not e or not r:
                continue
            self.detect_tree.insert("", "end", iid=eid, values=(
                e["name"], "已安装" if r["installed"] else "缺失 / 待处理",
                r["version"], r["detail"] or "—"),
                tags=("ok" if r["installed"] else "miss",))

    # ---------- ③ 已安装页 ----------

    def _build_installed_tab(self, tab):
        self.page_installed = tk.Frame(tab, bg=BODY_BG)
        self.page_installed.pack(fill="both", expand=True)

        head = self._card(self.page_installed)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="已安装管理", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(side="left", padx=14, pady=8)
        tk.Label(head, text="本工具安装记录；卸载仅清理本工具内容", bg=CARD_BG, fg=SUB,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)

        bar = self._card(self.page_installed)
        bar.pack(fill="x", pady=(0, 8))
        self._tool_button(bar, "冒烟验证", self.verify_selected, 0)
        self._tool_button(bar, "干净卸载", self.uninstall_selected, 1)
        self._tool_button(bar, "刷新", self.refresh_installed, 2)

        cols = ("env", "version", "source", "dir", "path")
        self.inst_tree = ttk.Treeview(self.page_installed, columns=cols, show="headings")
        heads = {"env": ("环境", 170), "version": ("版本", 100), "source": ("来源", 80),
                 "dir": ("安装位置", 400), "path": ("PATH", 70)}
        for c, (t, w) in heads.items():
            self.inst_tree.heading(c, text=t)
            self.inst_tree.column(c, width=w, anchor="w")
        self.inst_tree.pack(fill="both", expand=True)

    def refresh_installed(self):
        self.inst_tree.delete(*self.inst_tree.get_children())
        user_path = [p.rstrip("\\").lower() for p in pathman.get_user_path()]
        for eid, e in self.envs.items():
            rec = state.get_record(eid)
            if not rec:
                continue
            on_path = "是" if any(p.rstrip("\\").lower() in user_path
                                  for p in rec.get("added_path", []) if p) else "—"
            self.inst_tree.insert("", "end", iid=eid, values=(
                e["name"], rec.get("version") or "—",
                {"managed": "托管", "winget": "winget", "exe": "exe", "msi": "msi"}.get(
                    rec.get("source"), rec.get("source")),
                rec.get("install_dir") or (rec.get("winget_ids") and "、".join(rec["winget_ids"])) or "系统安装",
                on_path))

    # ---------- ④ 设置页 ----------

    def _build_settings_tab(self, tab):
        self.page_settings = tk.Frame(tab, bg=BODY_BG)
        self.page_settings.pack(fill="both", expand=True)

        box1 = self._card(self.page_settings)
        box1.pack(fill="x", pady=(0, 10))
        tk.Label(box1, text="存储位置", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        tk.Label(box1, text="数据目录包含托管安装、下载缓存、状态与快照；可改为任意位置，保存后自动迁移已有数据。",
                 bg=CARD_BG, fg=SUB).pack(anchor="w", padx=14)
        row = tk.Frame(box1, bg=CARD_BG)
        row.pack(fill="x", padx=14, pady=6)
        self.data_root_var = tk.StringVar(value=state.get_data_root())
        ent = tk.Entry(row, textvariable=self.data_root_var, bg="white",
                       font=("Microsoft YaHei UI", 9))
        ent.pack(side="left", fill="x", expand=True)
        self._tool_button(row, "浏览…", self._browse_data_root, 0)
        self._tool_button(row, "保存并迁移", self._save_data_root, 1, primary=True)
        self.data_loc_labels = {}
        for label, val in (("托管安装目录", state.managed_root()),
                           ("下载缓存目录", state.downloads_dir())):
            r2 = tk.Frame(box1, bg=CARD_BG)
            r2.pack(fill="x", padx=14, pady=1)
            tk.Label(r2, text=label + "：", width=14, anchor="w", bg=CARD_BG,
                     fg=SUB).pack(side="left")
            lab = tk.Label(r2, text=val, bg=CARD_BG, fg=TEXT, anchor="w")
            lab.pack(side="left", fill="x", expand=True)
            self.data_loc_labels[label] = lab

        box2 = self._card(self.page_settings)
        box2.pack(fill="x", pady=(0, 10))
        tk.Label(box2, text="PATH 快照与回滚（安全网）", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        tk.Label(box2, text="每次安装 / 卸载 / 切换前自动快照用户 PATH，异常时可一键恢复",
                 bg=CARD_BG, fg=SUB).pack(anchor="w", padx=14)
        cols = ("time", "reason")
        self.snap_tree = ttk.Treeview(box2, columns=cols, show="headings", height=4)
        self.snap_tree.heading("time", text="时间")
        self.snap_tree.column("time", width=190, anchor="w")
        self.snap_tree.heading("reason", text="原因")
        self.snap_tree.column("reason", width=470, anchor="w")
        self.snap_tree.pack(fill="x", padx=14, pady=6)
        btns = tk.Frame(box2, bg=CARD_BG)
        btns.pack(fill="x", padx=14, pady=(0, 10))
        self._tool_button(btns, "刷新快照列表", self.refresh_snapshots, 0)
        self._tool_button(btns, "恢复所选快照", self.restore_snapshot, 1)
        self.refresh_snapshots()

        box3 = self._card(self.page_settings)
        box3.pack(fill="x")
        tk.Label(box3, text="安全与边界", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        for r in ("只操作当前用户的 PATH 与环境变量，不修改系统级变量；",
                  "只卸载本工具安装的托管环境与本工具添加的 PATH 条目；",
                  "不涉及硬件维修（显卡驱动等一律不做检测与安装）；",
                  "引导型环境（数据库 / 游戏引擎等）打开官方页，由官方安装器处理。"):
            tk.Label(box3, text="· " + r, bg=CARD_BG, fg=TEXT, anchor="w").pack(
                fill="x", padx=18, pady=1)
        tk.Frame(box3, bg=CARD_BG, height=10).pack()

    # ---------- ⑤ 关于页 ----------

    def _build_about_tab(self, tab):
        self.page_about = tk.Frame(tab, bg=BODY_BG)
        self.page_about.pack(fill="both", expand=True)

        box1 = self._card(self.page_about)
        box1.pack(fill="x", pady=(0, 10))
        head = tk.Frame(box1, bg=CARD_BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        logo = tk.Canvas(head, width=44, height=44, bg=CARD_BG, highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(1, 1, 43, 43, fill=PRIMARY, outline="")
        logo.create_text(22, 22, text="装", font=("Microsoft YaHei UI", 17, "bold"),
                         fill="white")
        col = tk.Frame(head, bg=CARD_BG)
        col.pack(side="left", padx=10)
        tk.Label(col, text=APP_TITLE, bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(col, text="%s · 免安装绿色版 · 零依赖" % APP_VERSION, bg=CARD_BG,
                 fg=SUB, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        tk.Label(box1, text="开发作者：y50008", bg=CARD_BG, fg=PRIMARY_DEEP,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=14, pady=(2, 10))

        box2 = self._card(self.page_about)
        box2.pack(fill="x", pady=(0, 10))
        tk.Label(box2, text="这个软件是干什么的", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        for line in (
            "开发环境装不上、装半天配不好环境变量？本工具把常用开发与游戏环境按职业路线整理好：",
            "选一条路线（如 Java 全栈、AI 应用与智能体、虚幻引擎、Unity、Python 脚本……），勾选要装的工具，",
            "多版本直接下拉选择，一键自动安装并配置 PATH / JAVA_HOME 等环境变量；",
            "环境体检按路线检测缺失项，缺什么直接装；winget 静默装主流软件，托管 zip 管理 JDK / Node 多版本，",
            "数据库、游戏引擎等大型软件自动打开官方下载页由官方安装器处理。",
            "只做环境配置，不涉及显卡驱动检测、电脑维修等板块。",
        ):
            tk.Label(box2, text="· " + line, bg=CARD_BG, fg=TEXT, anchor="w",
                     wraplength=820, justify="left").pack(fill="x", padx=18, pady=1)

        box3 = self._card(self.page_about)
        box3.pack(fill="x")
        tk.Label(box3, text="技术说明", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        for label, val in (("运行环境", "Windows；Python + tkinter 纯标准库，零第三方依赖"),
                           ("形态", "免安装绿色单文件 exe，双击即用"),
                           ("托管数据", r"%LOCALAPPDATA%\OneClickEnv\managed（按当前用户自动展开）"),
                           ("下载缓存", r"%LOCALAPPDATA%\OneClickEnv\downloads（按当前用户自动展开）")):
            row = tk.Frame(box3, bg=CARD_BG)
            row.pack(fill="x", padx=14, pady=1)
            tk.Label(row, text=label + "：", width=10, anchor="w", bg=CARD_BG,
                     fg=SUB).pack(side="left")
            tk.Label(row, text=val, bg=CARD_BG, fg=TEXT, anchor="w",
                     wraplength=700, justify="left").pack(side="left", fill="x", expand=True)
        tk.Frame(box3, bg=CARD_BG, height=12).pack()

    # ---------- 日志条（可折叠） ----------

    def _build_logbar(self, parent):
        bar = tk.Frame(parent, bg="white", highlightthickness=1, highlightbackground=BORDER)
        bar.pack(fill="x")
        self.log_bar = bar
        head = tk.Frame(bar, bg="white")
        head.pack(fill="x")
        self.log_btn = tk.Label(head, text="运行日志  ▾", bg="white", fg=SUB, cursor="hand2",
                                font=("Microsoft YaHei UI", 9, "bold"))
        self.log_btn.pack(side="left", padx=12, pady=3)
        self.log_btn.bind("<Button-1>", lambda e: self.toggle_log())
        self.log_text = tk.Text(bar, height=1, state="disabled", bg="#FAFBFC", fg=TEXT,
                                font=("Consolas", 9), relief="flat")
        self.log_text.pack(fill="x", padx=12, pady=(0, 4))
        self.log_tail = ""

    def toggle_log(self):
        self.log_expanded = not self.log_expanded
        self.log_btn.configure(text="运行日志  %s" % ("▴" if self.log_expanded else "▾"))
        self.log_text.configure(height=5 if self.log_expanded else 1)
        if not self.log_expanded:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", self.log_tail + "\n")
            self.log_text.configure(state="disabled")

    def log(self, msg):
        self.log_tail = msg
        self.log_text.configure(state="normal")
        if self.log_expanded:
            self.log_text.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
            self.log_text.see("end")
        else:
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.log_text.configure(state="disabled")

    # ================= 路线切换 =================

    def switch_path(self, path_id, init=False):
        if path_id not in self.path_index:
            return
        self.current_path_id = path_id
        for pid, item in self.path_items.items():
            self._paint_card(item, item._data["idx"],
                             NAV_CARD_ACTIVE if pid == path_id else NAV_CARD)
        p = self.path_index[path_id]
        self.install_title.configure(text=p["name"])
        self.install_desc.configure(text=p["desc"])
        self.refresh_market()
        self.refresh_detect_tree()
        if not init:
            self.log("已切换路线：%s（%d 项工具）" % (p["name"], len(p["env_ids"])))

    # ================= 检测 =================

    def _run_detect(self, envs):
        def progress_cb(done, total):
            self.worker.q.put(("progress", (done, total)))

        results = {}
        for i, e in enumerate(envs):
            if self.worker.cancel_requested():
                self.worker.q.put(("log", "已停止检测：跳过剩余 %d 项" % (len(envs) - i)))
                break
            self.worker.q.put(("status", "正在检测 %s（%d / %d）" % (e["name"], i + 1, len(envs))))
            try:
                results[e["id"]] = detect.detect_one(e)
            except Exception as ex:  # noqa: BLE001
                results[e["id"]] = {"installed": False, "version": "", "detail": str(ex)}
        self.detect_cache.update(results)
        self.worker.q.put(("detect_done", None))

    def detect_checked(self):
        path = self.path_index[self.current_path_id]
        s = self.checked.get(path["id"], set())
        if not s:
            messagebox.showinfo(APP_TITLE, "请先勾选要检测的环境", parent=self.root)
            return
        envs = [self.envs[eid] for eid in s if eid in self.envs]
        self.worker.submit("检测勾选环境（%d 项）" % len(envs), self._run_detect, envs)

    def detect_current_path(self):
        path = self.path_index[self.current_path_id]
        envs = [self.envs[eid] for eid in path["env_ids"] if eid in self.envs]
        self.worker.submit("检测本路线：%s（%d 项）" % (path["name"], len(envs)),
                           self._run_detect, envs)

    def on_detect_done(self, _=None):
        self.refresh_market()
        self.refresh_detect_tree()
        self.set_progress(0, 0)

    # ================= 安装 =================

    def _run_install_list(self, envs):
        def log_cb(m):
            self.worker.q.put(("log", m))

        def progress_cb(done, total):
            self.worker.q.put(("progress", (done, total)))

        cancel_evt = self.worker.cancel_evt
        total = len(envs)
        for i, e in enumerate(envs):
            if cancel_evt and cancel_evt.is_set():
                self.worker.q.put(("log", "已停止：跳过剩余 %d 项" % (total - i)))
                break
            self.worker.q.put(("status", "正在安装 %s（%d / %d）" % (e["name"], i + 1, total)))
            self.worker.q.put(("progress", (i, total)))
            r = self.detect_cache.get(e["id"])
            if r and r["installed"]:
                self.worker.q.put(("log", "跳过 %s：已安装 v%s" % (e["name"], r["version"])))
                continue
            itype = e.get("install", {}).get("type", "guide")
            if itype not in AUTO_TYPES:
                page = e.get("install", {}).get("page")
                if page:
                    self.worker.q.put(("log", "【引导】%s 打开官方下载页：%s" % (e["name"], page)))
                    webbrowser.open(page)
                continue
            self.worker.q.put(("log", "—— 安装：%s ——" % e["name"]))
            try:
                if itype == "zip":
                    vs = e.get("install", {}).get("versions") or []
                    ver = self.selected_version.get(self.current_path_id, {}).get(e["id"]) \
                        or (vs[0] if vs else "latest")
                    install.install_managed(e, ver, log_cb, progress_cb, cancel_evt=cancel_evt)
                elif itype == "winget":
                    install.install_winget(e, log_cb, cancel_evt=cancel_evt)
                elif itype == "exe":
                    install.install_exe(e, log_cb, progress_cb, cancel_evt=cancel_evt)
                elif itype == "msi":
                    install.install_msi(e, log_cb, progress_cb, cancel_evt=cancel_evt)
                if cancel_evt and cancel_evt.is_set():
                    self.worker.q.put(("log", "已停止：%s 安装中断" % e["name"]))
                    break
                ok, msg = install.verify(e, state.get_record(e["id"]))
                self.worker.q.put(("log", "验证：%s" % msg))
            except Exception as ex:  # noqa: BLE001
                if cancel_evt and cancel_evt.is_set():
                    self.worker.q.put(("log", "已停止：%s（%s）" % (e["name"], ex)))
                    break
                self.worker.q.put(("log", "安装失败 %s：%s" % (e["name"], ex)))
        self.worker.q.put(("progress", (total, total)))
        self.worker.q.put(("installed_done", None))

    def install_checked(self):
        path = self.path_index[self.current_path_id]
        s = self.checked.get(path["id"], set())
        if not s:
            messagebox.showinfo(APP_TITLE, "请先勾选要安装的环境", parent=self.root)
            return
        envs = [self.envs[eid] for eid in s if eid in self.envs]
        self.worker.submit("安装勾选环境（%d 项）" % len(envs), self._run_install_list, envs)

    def install_missing(self):
        path = self.path_index[self.current_path_id]
        missing = [self.envs[eid] for eid in path["env_ids"]
                   if eid in self.detect_cache and not self.detect_cache[eid]["installed"]
                   and eid in self.envs]
        if not missing:
            messagebox.showinfo(APP_TITLE, "本路线没有检测到缺失环境", parent=self.root)
            return
        self.worker.submit("安装缺失环境（%d 项）" % len(missing), self._run_install_list, missing)

    # ---------- 安装 / 打开（作用于勾选的环境） ----------

    def _checked_envs(self):
        path = self.path_index[self.current_path_id]
        return [self.envs[eid] for eid in self.checked.get(path["id"], set())
                if eid in self.envs]

    def install_or_guide(self, env_id):
        """单个环境：自动通道直接安装；引导型打开官方页；已装则提示版本。"""
        e = self.envs.get(env_id)
        if not e:
            return
        r = self.detect_cache.get(env_id)
        if r and r["installed"]:
            messagebox.showinfo(APP_TITLE, "%s 已安装 v%s，无需重复安装。"
                                % (e["name"], r["version"]), parent=self.root)
            return
        self.worker.submit("安装 / 打开 " + e["name"], self._run_install_list, [e])

    def install_or_guide_checked(self):
        """批量：自动通道后台安装并配置环境变量，引导型打开官方下载页。"""
        envs = self._checked_envs()
        if not envs:
            messagebox.showinfo(APP_TITLE, "请先勾选环境", parent=self.root)
            return
        self.worker.submit("安装 / 打开勾选环境（%d 项）" % len(envs),
                           self._run_install_list, envs)

    def switch_checked(self):
        switchable = [e for e in self._checked_envs()
                      if e.get("install", {}).get("type") == "zip"
                      and e.get("install", {}).get("switchable")]
        if not switchable:
            messagebox.showinfo(APP_TITLE, "请勾选支持版本切换的环境（JDK / Node.js）",
                                parent=self.root)
            return
        if len(switchable) == 1:
            self._prepare_switch(switchable[0]["id"])
        else:
            self._pick_env_dialog(switchable, "切换版本")

    def _pick_env_dialog(self, envs, title):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("440x400")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=CARD_BG)
        tk.Label(dlg, text="勾选了多个环境，请选择要操作的一个", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(pady=(16, 4))
        box = tk.Frame(dlg, bg=CARD_BG)
        box.pack(pady=8)
        var = tk.StringVar(value=envs[0]["id"])
        for e in envs:
            tk.Radiobutton(box, text=e["name"], variable=var, value=e["id"],
                           font=("Microsoft YaHei UI", 11), bg=CARD_BG,
                           activebackground=CARD_BG, selectcolor=PRIMARY_LIGHT,
                           indicatoron=False, padx=16, pady=3,
                           highlightthickness=1, highlightbackground=BORDER,
                           width=18, anchor="w").pack(pady=2)

        def go():
            env_id = var.get()
            dlg.destroy()
            self._prepare_switch(env_id)

        ttk.Button(dlg, text="确 定", command=go).pack(pady=8)

    def _prepare_switch(self, env_id):
        e = self.envs.get(env_id)
        if not e:
            return
        versions = detect.managed_installed_versions(e)
        if not versions:
            messagebox.showinfo(APP_TITLE, "尚未安装托管版本，请先安装后再切换", parent=self.root)
            return
        self._version_dialog(e, versions)

    def _version_dialog(self, entry, versions):
        dlg = tk.Toplevel(self.root)
        dlg.title("切换版本 - %s" % entry["name"])
        dlg.geometry("400x280")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(bg=CARD_BG)
        tk.Label(dlg, text="切换默认版本", bg=CARD_BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(18, 2))
        tk.Label(dlg, text="%s 当前已安装以下版本" % entry["name"],
                 bg=CARD_BG, fg=SUB, font=("Microsoft YaHei UI", 9)).pack()
        box = tk.Frame(dlg, bg=CARD_BG)
        box.pack(pady=12)
        var = tk.StringVar(value=versions[0])
        for v in versions:
            tk.Radiobutton(box, text="v" + v, variable=var, value=v,
                           font=("Microsoft YaHei UI", 11), bg=CARD_BG,
                           activebackground=CARD_BG, selectcolor=PRIMARY_LIGHT,
                           indicatoron=False, padx=18, pady=4,
                           highlightthickness=1, highlightbackground=BORDER,
                           width=8).pack(pady=3)

        def do_switch():
            dlg.destroy()
            self.worker.submit("切换版本", self._run_switch, entry, var.get())

        def _run_switch(entry_, version_):
            def log_cb(m):
                self.worker.q.put(("log", m))
            try:
                base = install.switch_version(entry_, version_, log_cb)
                self.worker.q.put(("log", "已切换 %s 默认版本为 v%s（%s）" % (entry_["name"], version_, base)))
                ok, msg = install.verify(entry_, state.get_record(entry_["id"]))
                self.worker.q.put(("log", "验证：%s" % msg))
            except Exception as e2:  # noqa: BLE001
                self.worker.q.put(("log", "切换失败：%s" % e2))
            self.worker.q.put(("installed_done", None))

        ttk.Button(dlg, text="切 换", command=do_switch).pack(pady=6)

    # ---------- 已安装操作 ----------

    def _selected_installed(self):
        sel = self.inst_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "请先在列表中选择一个环境", parent=self.root)
            return None
        return self.envs.get(sel[0])

    def verify_selected(self):
        e = self._selected_installed()
        if not e:
            return
        self.worker.submit("验证 " + e["name"], self._run_verify, e)

    def _run_verify(self, entry):
        ok, msg = install.verify(entry, state.get_record(entry["id"]))
        self.worker.q.put(("log", "验证 %s：%s" % (entry["name"], msg)))

    def uninstall_selected(self):
        e = self._selected_installed()
        if not e:
            return
        if not messagebox.askyesno(APP_TITLE, "确认卸载 %s？\n卸载前将自动备份 PATH，可一键回滚。"
                                   % e["name"], parent=self.root):
            return
        self.worker.submit("卸载 " + e["name"], self._run_uninstall, e)

    def _run_uninstall(self, entry):
        def log_cb(m):
            self.worker.q.put(("log", m))
        try:
            install.uninstall_env(entry, log_cb)
            self.worker.q.put(("log", "卸载完成：%s" % entry["name"]))
        except Exception as e2:  # noqa: BLE001
            self.worker.q.put(("log", "卸载失败：%s" % e2))
            messagebox.showerror(APP_TITLE, "卸载失败：%s" % e2, parent=self.root)
        self.worker.q.put(("installed_done", None))

    # ---------- 设置操作 ----------

    def _browse_data_root(self):
        cur = self.data_root_var.get()
        pick = filedialog.askdirectory(initialdir=cur if os.path.isdir(cur) else None,
                                       title="选择数据目录", parent=self.root)
        if pick:
            self.data_root_var.set(pick)

    def _save_data_root(self):
        if self.worker.busy:
            messagebox.showinfo(APP_TITLE, "有任务正在运行，请结束后再修改存储位置", parent=self.root)
            return
        new_root = self.data_root_var.get().strip()
        result = state.migrate_data_root(new_root)
        if result == "ok":
            self.data_root_var.set(state.get_data_root())
            self.data_loc_labels["托管安装目录"].configure(text=state.managed_root())
            self.data_loc_labels["下载缓存目录"].configure(text=state.downloads_dir())
            self.refresh_snapshots()
            self.refresh_installed()
            self.refresh_market()
            messagebox.showinfo(APP_TITLE, "数据目录已迁移到：\n%s" % state.get_data_root(),
                                parent=self.root)
            self.log("数据目录已迁移到 %s" % state.get_data_root())
        elif result == "same":
            messagebox.showinfo(APP_TITLE, "目录未变化", parent=self.root)
        elif result == "nested":
            messagebox.showerror(APP_TITLE, "新目录不能是当前数据目录的子目录或上级目录",
                                 parent=self.root)
        elif result == "target_not_empty":
            messagebox.showerror(APP_TITLE, "目标目录已存在且非空，请选择空目录或新目录",
                                 parent=self.root)
        elif result == "invalid":
            messagebox.showerror(APP_TITLE, "目录无效，请重新输入", parent=self.root)
        else:
            messagebox.showerror(APP_TITLE, "迁移失败：%s" % result, parent=self.root)

    def refresh_snapshots(self):
        self.snap_tree.delete(*self.snap_tree.get_children())
        for s in pathman.list_snapshots():
            self.snap_tree.insert("", "end", iid=s["id"], values=(s["time"], s["reason"]))

    def restore_snapshot(self):
        sel = self.snap_tree.selection()
        if not sel:
            messagebox.showinfo(APP_TITLE, "请先选择一个快照", parent=self.root)
            return
        if not messagebox.askyesno(APP_TITLE, "确认用该快照覆盖当前用户 PATH？\n此操作会替换全部用户 PATH 条目。",
                                   parent=self.root):
            return
        ok = pathman.restore_snapshot(sel[0])
        if ok:
            self.log("已恢复快照 %s" % sel[0])
            self.refresh_snapshots()
            self.refresh_installed()
        else:
            messagebox.showerror(APP_TITLE, "快照文件不存在", parent=self.root)

    # ================= 公共 =================

    def set_status(self, text):
        self.status.configure(text=text)

    def stop_task(self):
        if self.worker.busy:
            self.worker.cancel()

    def set_busy(self, busy: bool, title: str):
        self.status.configure(text=title if busy else "就绪")
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
            self.stop_btn.configure(bg="#E5484D", fg="white", cursor="hand2")
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.stop_btn.configure(bg="#E0E4EA", fg="#98A1AF", cursor="arrow")

    def set_progress(self, done, total):
        if total and total > 0:
            self.progress.configure(mode="determinate", maximum=total, value=done)


def main():
    try:
        root = tk.Tk()
        App(root)
        root.mainloop()
    except Exception as e:  # noqa: BLE001
        import traceback
        crash = os.path.join(state.data_root(), "crash.log")
        try:
            os.makedirs(os.path.dirname(crash), exist_ok=True)
            with open(crash, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        try:
            messagebox.showerror(APP_TITLE,
                                 "程序发生错误：%s\n\n详情已写入：%s" % (e, crash))
        except Exception:
            pass


if __name__ == "__main__":
    main()
