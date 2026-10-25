"""quote0-monitor — ChatGPT(Codex) + GLM 用量本地推送器（Quote/0 墨水屏）。

合并 quote0-burnout 与 quote0-glm 的抓取/渲染/推送为一台 Windows 电脑上的
常驻小程序：机器开着 = 你在消耗额度 = 数据需要新鲜；关机后无消耗也无需刷新。

- 抓取：ChatGPT 走本机 ~/.codex/auth.json 的 token（只读，不刷新不消耗
  refresh token，与本机 codex CLI 互不干扰）；GLM 走开放平台 API Key。
- 渲染：直接复用两个项目现成的渲染器，屏幕视觉与现在完全一致。
- 推送：分别固定到两张循环卡片，ditherType=NONE，推送后立即刷新。
- 两个 provider 都可以在 config.json（enable_gpt / enable_glm）或界面
  复选框里单独开关——比如笔记本上不用 Codex，就只开 GLM。

用法：
  python monitor.py                     # 打开预览窗口，后台自动刷新推送
  python monitor.py --silent            # 启动即隐藏到托盘（开机自启用的是这个）
  python monitor.py --once              # 抓一次、推一次、打印结果即退出（自检用）
  python monitor.py --install-autostart # 安装开机自启（本用户，托盘静默模式）
  python monitor.py --uninstall-autostart

窗口关闭（X / 隐藏到托盘）= 缩到右下角托盘继续后台刷新；
真正的退出在托盘图标的右键菜单里。依赖 pip install pystray（未装时关窗即退出）。
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

__version__ = "1.9.0"

# 打包成 exe（PyInstaller --onefile）后，__file__ 指向临时解压目录且每次
# 变化——配置/日志/预览必须落在 exe 自己的目录；渲染器则从打包内部加载。
_FROZEN = getattr(sys, "frozen", False)
HERE = Path(sys.executable).resolve().parent if _FROZEN else Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
API_BASE = "https://dot.mindreset.tech"
LOCK_PORT = 47631          # 单实例锁：防止“开机自启的静默实例”与手动窗口双开
INTERVAL_CHOICES = [1, 2, 3, 5, 10, 15, 30, 60]

CONFIG_TEMPLATE = {
    "interval_minutes": 5,
    "enable_gpt": True,
    "enable_glm": True,
    "quote0_api_key": "在这里填 Dot. App 创建的 dot_app_... 推送密钥",
    "quote0_device_id": "在这里填设备序列号",
    "card_gpt": "ChatGPT 卡片的 taskKey（App 内容工坊加 Image API 卡后可查）",
    "card_glm": "GLM 卡片的 taskKey",
    "glm_api_key": "在这里填智谱开放平台 API Key",
    "glm_plan": "cn",
}


# ── 配置与模块装载 ────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        # 首次运行：生成模板并继续（界面模式随后自动弹配置窗口引导填写）
        save_config(CONFIG_TEMPLATE)
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg.setdefault("enable_gpt", True)
    cfg.setdefault("enable_glm", True)
    # 被复用的模块在 import 时读环境变量，先垫好
    os.environ["GLM_API_KEY"] = cfg.get("glm_api_key", "")
    os.environ["GLM_PLAN"] = cfg.get("glm_plan", "cn")
    if cfg.get("glm_org"):
        os.environ["GLM_ORG"] = cfg["glm_org"]
    if cfg.get("glm_project"):
        os.environ["GLM_PROJECT"] = cfg["glm_project"]
    # 可选：ChatGPT 账号覆盖。不填则用本机 codex 登录（~/.codex/auth.json），
    # 换账号时要么在新账号下 codex login，要么把 token 填到这里。
    if cfg.get("codex_access_token"):
        os.environ["CODEX_ACCESS_TOKEN"] = cfg["codex_access_token"]
        if cfg.get("codex_account_id"):
            os.environ["CODEX_ACCOUNT_ID"] = cfg["codex_account_id"]
    return cfg


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def _import_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclass 解析注解时要求模块已在注册表
    spec.loader.exec_module(mod)
    return mod


def _default_module_dirs() -> tuple[Path, Path]:
    """渲染器/provider 的内置位置：源码模式用项目内 bundle/，
    exe 模式用打包时带入的 bundle/——同一棵树，一套逻辑。"""
    base = Path(sys._MEIPASS if _FROZEN else HERE) / "bundle"
    return base / "burnout", base / "glm"


def load_modules(cfg: dict):
    default_burnout, default_glm = _default_module_dirs()
    burnout_dir = Path(cfg["burnout_dir"]) if cfg.get("burnout_dir") else default_burnout
    glm_dir = Path(cfg["glm_dir"]) if cfg.get("glm_dir") else default_glm
    if not (burnout_dir / "render.py").exists():
        burnout_dir = default_burnout     # 配置里的旧兄弟目录路径已随合并失效 → 回退
    if not (glm_dir / "render.py").exists():
        glm_dir = default_glm
    if not (burnout_dir / "render.py").exists() or not (glm_dir / "render.py").exists():
        sys.exit(f"找不到渲染器 bundle：{default_burnout} / {default_glm}")
    sys.path.insert(0, str(burnout_dir))
    from providers import codex as codex_provider
    return {
        "codex": codex_provider,
        "render_gpt": _import_from(burnout_dir / "render.py", "burnout_render"),
        "glm": _import_from(glm_dir / "glm.py", "glm_provider"),
        "render_glm": _import_from(glm_dir / "render.py", "glm_render"),
    }


# ── 抓取 / 渲染 / 推送 ───────────────────────────────────────────────────────

class Dashboard:
    def __init__(self, cfg: dict, mods: dict):
        self.cfg = cfg
        self.mods = mods
        self.dev = cfg["quote0_device_id"]
        self.key = cfg["quote0_api_key"]
        self.tmp = HERE / "tmp"
        self.tmp.mkdir(exist_ok=True)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    # (显示名, 结果键, 抓取, 渲染, 卡片key字段, 预览文件名) —— 只含已启用的
    def tasks(self) -> list[tuple]:
        tasks = []
        if self.cfg.get("enable_gpt", True):
            tasks.append(("ChatGPT", "gpt", self.fetch_gpt, self.render_gpt,
                          "card_gpt", "preview_gpt.png"))
        if self.cfg.get("enable_glm", True):
            tasks.append(("GLM", "glm", self.fetch_glm, self.render_glm,
                          "card_glm", "preview_glm.png"))
        return tasks

    def fetch_gpt(self) -> dict:
        raw = self.mods["codex"].get_usage()
        resets = self.mods["codex"].get_codex_reset_credits()
        return self.mods["codex"].build_codex_snapshot(
            raw, reset_credits=resets if resets.get("ok") else None)

    def fetch_glm(self) -> dict:
        sn = self.mods["glm"].build_snapshot(self.mods["glm"].get_usage())
        if sn.get("ok"):
            cards = self.fetch_glm_reset_cards()
            if cards is not None:
                sn["reset_cards"] = cards
        return sn

    def fetch_glm_reset_cards(self) -> dict | None:
        """GLM 重置卡可用张数（只读 list 接口；失败返回 None 不影响主流程）。"""
        r = requests.get(
            "https://open.bigmodel.cn/api/biz/customer-package-reset/list",
            params={"targetType": "PERSONAL"},
            headers={"Accept": "application/json",
                     "Authorization": os.environ.get("GLM_API_KEY", "")},
            timeout=15)
        body = r.json()
        if not body.get("success"):
            return None
        data = body.get("data") or {}
        return {
            "five_hour": sum(1 for c in data.get("fiveHourResets") or [] if c.get("available")),
            "week": sum(1 for c in data.get("weekResets") or [] if c.get("available")),
        }

    def render_gpt(self, sn: dict, ts: str) -> bytes:
        snap = {"layout": "auto", "updated_at": ts, "codex": sn,
                "claude": {"ok": False}, "agy": {"ok": False},
                "deepseek": {"ok": False}, "opencode": {"ok": False}}
        return self.mods["render_gpt"].render_image(snap)

    def render_glm(self, sn: dict, ts: str) -> bytes:
        return self.mods["render_glm"].render_image({"glm": sn, "updated_at": ts})

    def push_card(self, png: bytes, task_key: str) -> dict:
        r = requests.post(
            f"{API_BASE}/api/authV2/open/device/{self.dev}/image",
            headers=self._headers(),
            json={
                "taskType": "loop",
                "taskKey": task_key,
                "refreshNow": True,          # 循环卡片推送后立即刷新
                "image": base64.b64encode(png).decode(),
                "ditherType": "NONE",        # 图已是纯 1-bit，关服务端抖动防重影
                "border": 0,
            }, timeout=20)
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:120]}
        return {"ok": r.ok, "status": r.status_code,
                "message": body.get("message", "") if isinstance(body, dict) else str(body)}

    # 网络抖动重试：chatgpt.com 从本机时通时断，一次超时不代表本轮作废。
    # 认证类失败（no auth / 401 / 403 / no key）重试无意义，立即放弃。
    RETRY_DELAYS = (10, 25)          # 首次失败后隔 10s、25s 各再试一次（共 3 次）

    def _fetch_with_retry(self, name: str, fetch, log):
        sn = {"ok": False}
        for attempt, delay in enumerate((0, *self.RETRY_DELAYS)):
            if delay:
                time.sleep(delay)
            try:
                sn = fetch()
            except Exception as e:
                sn = {"ok": False, "raw_status": type(e).__name__}
            if sn.get("ok"):
                if attempt:
                    log(f"{name}: 第 {attempt + 1} 次尝试成功")
                return sn
            status = str(sn.get("raw_status") or sn.get("status") or "?")
            if attempt < len(self.RETRY_DELAYS) and not any(
                    k in status for k in ("auth", "401", "403", "no key")):
                log(f"{name}: 第 {attempt + 1} 次失败（{status}），{self.RETRY_DELAYS[attempt]}s 后重试")
            else:
                break
        return sn

    # 一次完整周期：抓两边 → 各自成功才渲染推送（失败侧保留屏上旧图）
    def cycle(self, log) -> dict:
        ts = datetime.now().strftime("%H:%M")
        result = {"ts": ts}
        for name, key, fetch, render, key_field, png_name in self.tasks():
            try:
                sn = self._fetch_with_retry(name, fetch, log)
                if not sn.get("ok"):
                    log(f"{ts} {name}: 拉取失败 ({sn.get('raw_status', sn.get('status', '?'))})，保留屏上旧图")
                    result[key] = {"ok": False}
                    continue
                png = render(sn, ts)
                (self.tmp / png_name).write_bytes(png)
                push = self.push_card(png, self.cfg[key_field])
                if push["ok"]:
                    log(f"{ts} {name}: 已推送  {self._summary(name, sn)}")
                    result[key] = {"ok": True, "sn": sn, "ts": ts}
                else:
                    log(f"{ts} {name}: 推送失败 HTTP {push['status']} {push['message']}")
            except Exception as e:
                log(f"{ts} {name}: 异常 {type(e).__name__}: {e}")
        return result

    @staticmethod
    def _summary(name: str, sn: dict) -> str:
        bits = []
        if sn.get("short_used_percent") is not None:
            bits.append(f"5h 余 {100 - sn['short_used_percent']}%")
        if sn.get("long_used_percent") is not None:
            bits.append(f"Wk 余 {100 - sn['long_used_percent']}%")
        if name == "GLM":
            mcp = sn.get("mcp")
            if mcp and mcp.get("remaining") is not None:
                bits.append(f"MCP {mcp['remaining']}")
        return "  ".join(bits)


# ── 后台循环 ─────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self, dash: Dashboard, interval_min: float, log):
        self.dash = dash
        self.interval = max(30, int(interval_min * 60))
        self.log = log
        self.auto = True
        self._wake = threading.Event()
        self.lock = threading.Lock()
        self.last = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def refresh_now(self):
        self._wake.set()

    def set_interval(self, minutes: int):
        with self.lock:
            self.interval = max(30, int(minutes * 60))
        self._wake.set()      # 让循环立刻按新间隔重新等待

    def _run(self):
        while True:
            with self.lock:
                running = self.auto
            if running:
                try:
                    self.last = self.dash.cycle(self.log)
                except Exception as e:
                    self.log(f"cycle 异常: {e}")
                if hasattr(self, "on_cycle"):
                    self.on_cycle(self.last)
            self._wake.wait(self.interval)
            self._wake.clear()


# ── 使用说明 ─────────────────────────────────────────────────────────────────

HELP_TEXT = """【四步完成配置】（推荐：主界面点「配置」，在窗口里填好并
点「测试并保存」；以下说明字段与之一一对应）

① Dot 推送密钥  quote0_api_key
   手机 Dot. App → More → API Key → Create API Key
   （或网页 dot.mindreset.tech/panel → API Keys 页创建）
   密钥只显示一次，注意保存；以 dot_app_ 开头

② 设备序列号  quote0_device_id
   Dot. App 的设备设置页可查（一串大写字母 + 数字）

③ 两张卡片  card_gpt / card_glm
   Dot. App → Content Studio → 在设备的播放列表里添加两张
   「Image API」卡片（必须两张：分别显示 ChatGPT 和 GLM，
   只加一张的话两块仪表盘会互相覆盖）
   然后打开「配置」窗口，点「获取卡片并自动填入」即可——
   卡片 key 在 App 界面看不到，官方获取途径是接口查询，
   程序已代劳，无需手动填任何 key

④ GLM 密钥  glm_api_key
   智谱开放平台 open.bigmodel.cn 控制台里的 API Key

【ChatGPT 卡还需要】
   本机安装 Codex CLI 并登录一次：命令行运行 codex，按提示登录
   （不想装 Codex 可临时填 codex_access_token 字段，约 10 天有效）

【常见问题】
   401 / 身份验证失败   → key 填错，或复制时带了引号
   推送 404            → 卡片 key 不对，或播放列表里没加 Image API 卡片
   屏上时间戳一直不变   → 刷新间隔未到；点「立即刷新」试试
   改了 config.json    → 托盘图标右键退出程序，再重新打开

【运行方式】
   双击 = 预览窗口；窗口的关闭按钮 = 最小化到托盘继续后台刷新
   托盘图标右键 = 显示窗口 / 立即刷新 / 退出（真正退出只在这里）
   开机自启：命令行运行  quote0-monitor.exe --install-autostart
"""


def probe_device(api_key: str, dev: str) -> tuple[bool, str, dict | None]:
    """「测试并保存」用的连通性探测：验证 Dot 密钥+设备号并顺带取卡片。

    返回 (是否成功, 给用户看的结果消息, loop/fixed 卡片 key 表)。"""
    for tt in ("loop", "fixed"):
        try:
            r = requests.get(
                f"{API_BASE}/api/authV2/open/device/{dev}/{tt}/list",
                headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        except requests.RequestException as e:
            return False, f"网络错误（{type(e).__name__}），检查网络后重试", None
        if r.status_code in (401, 403):
            return False, "密钥无效（401/403）：quote0_api_key 不对或已作废", None
        if r.status_code == 404:
            return False, "设备未找到（404）：quote0_device_id 不对", None
        if not r.ok:
            return False, f"服务异常 HTTP {r.status_code}，稍后再试", None
        try:
            body = r.json()
        except ValueError:
            return False, "服务返回异常数据，稍后再试", None
        if not isinstance(body, list):
            continue
        keys = [c.get("key") for c in body
                if isinstance(c, dict) and c.get("type") == "IMAGE_API"]
        if tt == "loop" and keys:      # 拿到循环卡片即可判定成功
            return True, "", {"loop": keys, "fixed": []}
    # loop 空/失败但接口都通——再补一次 fixed 的真实结果
    try:
        r = requests.get(
            f"{API_BASE}/api/authV2/open/device/{dev}/fixed/list",
            headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        fixed = [c.get("key") for c in r.json()
                 if isinstance(c, dict) and c.get("type") == "IMAGE_API"] if r.ok else []
    except Exception:
        fixed = []
    return True, "连接成功（循环列表暂无 Image API 卡片，去 App 添加两张后再来获取）", {"loop": [], "fixed": fixed}


def _fetch_image_cards(api_key: str, dev: str) -> dict[str, list[str]]:
    """loop/fixed 两个列表里的 IMAGE_API 卡片 key，失败返回空 dict。"""
    out = {}
    for tt in ("loop", "fixed"):
        try:
            r = requests.get(
                f"{API_BASE}/api/authV2/open/device/{dev}/{tt}/list",
                headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
            out[tt] = [c.get("key") for c in r.json()
                       if isinstance(c, dict) and c.get("type") == "IMAGE_API"]
        except Exception:
            out[tt] = []
    return out


def _card_unfilled(val: str) -> bool:
    """卡片字段还是占位符/空 = 未配置（真实 taskKey 是纯 ASCII 短串）。"""
    return (not val) or ("填" in val) or ("taskKey" in val) or (not val.isascii())


def auto_fill_cards(cfg: dict) -> str:
    """密钥+设备号已填而卡片未填时：查接口自动分配并写回 config.json。

    分配规则：循环列表优先，第 1 张给 ChatGPT、第 2 张给 GLM（哪张对应
    哪个仪表盘不影响功能，只决定两个显示位各显示谁）。"""
    key, dev = cfg.get("quote0_api_key", ""), cfg.get("quote0_device_id", "")
    if not key.startswith("dot_app_") or len(dev) < 6:
        return ("自动填入跳过：quote0_api_key / quote0_device_id 还不是有效值，"
                "填好这两个后再试。")
    cards = _fetch_image_cards(key, dev)
    keys = (cards.get("loop") or []) + (cards.get("fixed") or [])
    if not keys:
        return ("自动填入失败：设备上没有 Image API 卡片。\n"
                "请先在 Dot. App 的 Content Studio 里添加（建议两张）。")
    if len(keys) == 1:
        cfg["card_gpt"] = cfg["card_glm"] = keys[0]
        save_config(cfg)
        return ("注意：设备上只有 1 张 Image API 卡片，已把同一个 key 填入\n"
                f"card_gpt 和 card_glm（{keys[0]}）。\n"
                "两个仪表盘会在这张卡上轮流覆盖显示——建议再添加一张。")
    cfg["card_gpt"], cfg["card_glm"] = keys[0], keys[1]
    save_config(cfg)
    msg = [f"已自动填入并写入 config.json：",
           f"    card_gpt = {keys[0]}",
           f"    card_glm = {keys[1]}"]
    if len(keys) > 2:
        msg.append(f"（另有 {len(keys) - 2} 张多余卡片被忽略：{'、'.join(keys[2:])}）")
    return "\n".join(msg)


# ── 配置窗口 ─────────────────────────────────────────────────────────────────

def make_config_window(root, cfg: dict, log):
    """配置窗口：三项粘贴字段 + 卡片获取/绑定 + 测试并保存（即存即生效）。"""
    import tkinter as tk
    from tkinter import ttk

    win = tk.Toplevel(root)
    win.title("配置")
    # 不写死尺寸：内容自适应（Entry 长密钥较宽，固定小窗会裁掉右侧，
    # 高 DPI 缩放的机器更明显）；可拖拽调整
    win.resizable(True, True)
    win.grab_set()                      # 模态：配置期间不与主窗口交叉操作

    body = tk.Frame(win)
    body.pack(fill="both", expand=True, padx=14, pady=10)

    tk.Label(body, text="一、推送设置（从各官方 App 复制粘贴）",
             font=("Microsoft YaHei", 10, "bold")).pack(anchor="w")

    def looks_real(v: str) -> bool:
        """真实值=纯 ASCII 且不含占位符字样；模板占位符留空让用户粘贴。"""
        return bool(v) and v.isascii() and "填" not in v and "taskKey" not in v

    def field_row(parent, label, initial, validate=None, secret=False):
        row = tk.Frame(parent)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, width=16, anchor="w").pack(side="left")
        var = tk.StringVar(value=initial if looks_real(initial) else "")
        ent = tk.Entry(row, textvariable=var, width=44,
                       show="•" if secret else "")
        ent.pack(side="left", fill="x", expand=True)
        hint = tk.Label(row, text="", fg="#0a7d32", width=4)
        hint.pack(side="left")

        def check(*_):
            if validate:
                ok, _msg = validate(var.get().strip())
                hint.config(text="✓" if var.get().strip() else "",
                            fg="#0a7d32" if ok else "#c22")
                if var.get().strip() and not ok:
                    hint.config(text="✗")
        var.trace_add("write", check)
        check()
        return var

    def v_dot(v):   return (v.startswith("dot_app_") and len(v) > 12, "以 dot_app_ 开头")
    def v_dev(v):   return (len(v) >= 6 and v.isascii(), "设备序列号")
    def v_glm(v):   return (len(v) >= 20 and "填" not in v and v.isascii(), "智谱 API Key")

    var_key = field_row(body, "Quote/0 推送密钥", cfg.get("quote0_api_key", ""), v_dot, secret=True)
    var_dev = field_row(body, "设备序列号", cfg.get("quote0_device_id", ""), v_dev)
    var_glm = field_row(body, "GLM 密钥", cfg.get("glm_api_key", ""), v_glm, secret=True)

    tk.Label(body, text="\n二、卡片绑定（需先填好上面三项）",
             font=("Microsoft YaHei", 10, "bold")).pack(anchor="w")

    card_row = tk.Frame(body)
    card_row.pack(fill="x", pady=2)
    btn_fetch = tk.Button(card_row, text="获取卡片并自动填入")
    btn_fetch.pack(side="left")
    fetch_state = tk.Label(card_row, text="", fg="#666")
    fetch_state.pack(side="left", padx=8)

    combo_row = tk.Frame(body)
    combo_row.pack(fill="x", pady=2)
    tk.Label(combo_row, text="ChatGPT 卡片", width=14).pack(side="left")
    var_cg = tk.StringVar(value=cfg.get("card_gpt", ""))
    cb_g = ttk.Combobox(combo_row, textvariable=var_cg, width=30)
    cb_g.pack(side="left", padx=(0, 10))
    tk.Label(combo_row, text="GLM 卡片").pack(side="left")
    var_cl = tk.StringVar(value=cfg.get("card_glm", ""))
    cb_l = ttk.Combobox(combo_row, textvariable=var_cl, width=30)
    cb_l.pack(side="left")

    result = tk.Label(body, text="", fg="#0a7d32", wraplength=520, justify="left")
    result.pack(anchor="w", pady=(6, 0))

    tk.Label(body, text="高级字段（Codex token / 团队版 org·project）仍需手动编辑"
                        " config.json，见「使用说明」",
             fg="#999", font=("Microsoft YaHei", 8)).pack(anchor="w", pady=(4, 0))

    def do_fetch():
        key, dev = var_key.get().strip(), var_dev.get().strip()
        if not key.startswith("dot_app_") or len(dev) < 6:
            fetch_state.config(text="✗ 请先正确填写上面两项", fg="#c22")
            return
        fetch_state.config(text="查询中…", fg="#666")
        btn_fetch.config(state="disabled")

        def run():
            ok, msg, cards = probe_device(key, dev)
            keys = ((cards or {}).get("loop") or []) + ((cards or {}).get("fixed") or [])
            def done():
                btn_fetch.config(state="normal")
                if not ok:
                    fetch_state.config(text="✗ " + msg, fg="#c22")
                    return
                values = keys or []
                cb_g["values"] = values
                cb_l["values"] = values
                if len(values) >= 2:
                    var_cg.set(values[0]); var_cl.set(values[1])
                    fetch_state.config(text=f"✓ 找到 {len(values)} 张卡片，已自动分配（可下拉交换）", fg="#0a7d32")
                elif len(values) == 1:
                    var_cg.set(values[0]); var_cl.set(values[0])
                    fetch_state.config(text="⚠ 只有 1 张卡，两个仪表盘会轮流覆盖——建议再加一张", fg="#b80")
                else:
                    fetch_state.config(text="⚠ 连接成功但没有 Image API 卡片，去 App 添加后再获取", fg="#b80")
            if win.winfo_exists():
                win.after(0, done)
        threading.Thread(target=run, daemon=True).start()

    btn_fetch.config(command=do_fetch)

    def do_save():
        key, dev, glm = var_key.get().strip(), var_dev.get().strip(), var_glm.get().strip()
        if not (v_dot(key)[0] and v_dev(dev)[0]):
            result.config(text="✗ 推送密钥或设备序列号格式不对，请按提示修正", fg="#c22")
            return
        if not v_glm(glm)[0]:
            result.config(text="✗ GLM 密钥格式不对（是否复制完整？带了引号？）", fg="#c22")
            return
        result.config(text="测试中…", fg="#666")

        def run():
            ok, msg, cards = probe_device(key, dev)
            keys = ((cards or {}).get("loop") or []) + ((cards or {}).get("fixed") or [])
            def done():
                if not ok:
                    result.config(text="✗ " + msg, fg="#c22")
                    return
                cfg["quote0_api_key"], cfg["quote0_device_id"] = key, dev
                cfg["glm_api_key"] = glm
                cg, cl = var_cg.get().strip(), var_cl.get().strip()
                if cg: cfg["card_gpt"] = cg
                if cl: cfg["card_glm"] = cl
                save_config(cfg)          # 即存即生效：推送每轮都读当前配置
                log("配置已保存（配置窗口）")
                bits = ["✓ 连接成功，配置已保存并立即生效"]
                if not cg or not cl:
                    bits.append("（卡片未绑定：点「获取卡片并自动填入」）")
                result.config(text="  ".join(bits), fg="#0a7d32")
            if win.winfo_exists():
                win.after(0, done)
        threading.Thread(target=run, daemon=True).start()

    btns = tk.Frame(body)
    btns.pack(fill="x", pady=(10, 0))
    tk.Button(btns, text="测试并保存", command=do_save).pack(side="left")
    tk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=8)

    return win


# ── Tkinter 预览窗口 ─────────────────────────────────────────────────────────

def _fmt(name: str, sn: dict) -> str:
    if not sn or not sn.get("ok"):
        return "拉取失败/未配置"
    lines = []
    if sn.get("short_used_percent") is not None:
        lines.append(f"{sn.get('short_label', '5h'):4s} 已用 {sn['short_used_percent']}%   重置 {sn.get('short_reset', '?')}")
    if sn.get("long_used_percent") is not None:
        lines.append(f"{sn.get('long_label', 'Wk'):4s} 已用 {sn['long_used_percent']}%   重置 {sn.get('long_reset', '?')}")
    if name == "ChatGPT":
        lines.append(f"重置额度包 {sn.get('resets_available')}")
    else:
        mcp = sn.get("mcp")
        if mcp:
            lines.append(f"MCP 剩余 {mcp.get('remaining')}/{mcp.get('total')}  重置 {mcp.get('reset')}")
    return "\n".join(lines)


def run_ui(dash: Dashboard, cfg: dict, start_hidden: bool = False,
           auto_open_config: bool = False):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import Image, ImageTk

    SCALE = 1.2
    root = tk.Tk()
    root.title(f"Quote/0 用量监控 v{__version__}")
    log_file = dash.tmp / "monitor.log"

    log_lines: list[str] = []
    state = {"photo": {}, "panels": {}}

    def log(msg: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        log_lines.append(line)
        del log_lines[:-200]
        with open(log_file, "a", encoding="utf-8") as f:   # 托盘模式下也留痕
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        if "text" in state and state["text"].winfo_exists():
            t = state["text"]
            t.config(state="normal")
            t.delete("1.0", "end")
            t.insert("1.0", "\n".join(log_lines[-12:]))
            t.config(state="disabled")

    # 密钥/设备号已填而卡片未填 → 自动查接口填卡（官方获取 taskKey 的唯一途径）
    if _card_unfilled(cfg.get("card_gpt", "")) or _card_unfilled(cfg.get("card_glm", "")):
        log(auto_fill_cards(cfg))

    engine = Engine(dash, cfg.get("interval_minutes", 5), log)

    state["top"] = tk.Frame(root)
    state["top"].pack(padx=10, pady=8)

    def build_panels():
        for w in state["top"].winfo_children():
            w.destroy()
        state["panels"].clear()
        tasks = dash.tasks()
        if not tasks:
            tk.Label(state["top"], text="两个 provider 都被关闭了", fg="#a00").pack(padx=20, pady=20)
            return
        for col, (name, key, *_rest, png_name) in enumerate(
                [(t[0], t[1], t[5]) for t in tasks]):
            panel = tk.LabelFrame(state["top"], text=name)
            panel.grid(row=0, column=col, padx=8)
            img_label = tk.Label(panel)
            img_label.pack(padx=6, pady=6)
            info = tk.Label(panel, text="等待首次刷新…", justify="left",
                            font=("Consolas", 10), anchor="w")
            info.pack(fill="x", padx=6, pady=(0, 6))
            state["panels"][key] = (img_label, info, png_name)
            png = dash.tmp / png_name
            if png.exists():
                _show_image(state["panels"][key][0], png)

    def _show_image(label, png: Path):
        img = Image.open(png)
        img = img.resize((int(img.width * SCALE), int(img.height * SCALE)), Image.NEAREST)
        photo = ImageTk.PhotoImage(img)
        state["photo"][png.name] = photo          # 保持引用防 GC
        label.config(image=photo)

    def on_cycle(result):
        if not root.winfo_exists():
            return
        for key, (img_label, info, png_name) in list(state["panels"].items()):
            png = dash.tmp / png_name
            if png.exists():
                _show_image(img_label, png)
            sn = (result or {}).get(key) or {}
            info.config(text=_fmt("ChatGPT" if key == "gpt" else "GLM", sn.get("sn", sn)))

    engine.on_cycle = on_cycle

    text = tk.Text(root, height=8, width=86, font=("Consolas", 9),
                   state="disabled", bg="#f4f4f4")
    text.pack(padx=10, pady=4, fill="x")
    state["text"] = text

    bar = tk.Frame(root)
    bar.pack(pady=6, fill="x", padx=10)

    def tick():
        if root.winfo_exists():
            state["status"].config(
                text=f"自动刷新: {'开' if engine.auto else '停'}   间隔 {engine.interval // 60} 分钟")
            root.after(1000, tick)

    def show_help():
        win = tk.Toplevel(root)
        win.title("使用说明 — 配置指引")
        win.geometry("620x540")
        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        txt = tk.Text(frame, wrap="word", font=("Microsoft YaHei", 10),
                      padx=12, pady=10)
        sb = tk.Scrollbar(frame, command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", HELP_TEXT)
        txt.config(state="disabled")
        tk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, 8))

    def show_config():
        make_config_window(root, cfg, log)

    tk.Button(bar, text="立即刷新", command=engine.refresh_now).pack(side="left", padx=3)
    tk.Button(bar, text="暂停/继续",
              command=lambda: (setattr(engine, "auto", not engine.auto),
                               log(f"自动刷新已{'开启' if engine.auto else '暂停'}"))
              ).pack(side="left", padx=3)

    def on_interval(_evt=None):
        m = int(interval_var.get())
        engine.set_interval(m)
        cfg["interval_minutes"] = m
        save_config(cfg)
        log(f"刷新间隔已设为 {m} 分钟（已写入 config.json）")

    ttk.Label(bar, text="  间隔(分钟):").pack(side="left")
    interval_var = tk.StringVar(value=str(cfg.get("interval_minutes", 5)))
    cb = ttk.Combobox(bar, textvariable=interval_var, width=4, state="readonly",
                      values=[str(m) for m in INTERVAL_CHOICES])
    cb.pack(side="left", padx=2)
    cb.bind("<<ComboboxSelected>>", on_interval)

    def provider_toggle(field: str, name: str):
        def _toggle():
            cfg[field] = not cfg.get(field, True)
            save_config(cfg)
            build_panels()
            log(f"{name} 已{'启用' if cfg[field] else '停用'}（已写入 config.json）")
        return _toggle

    tk.Checkbutton(bar, text="ChatGPT", variable=tk.BooleanVar(value=cfg.get("enable_gpt", True)),
                   command=provider_toggle("enable_gpt", "ChatGPT")).pack(side="left", padx=(18, 2))
    tk.Checkbutton(bar, text="GLM", variable=tk.BooleanVar(value=cfg.get("enable_glm", True)),
                   command=provider_toggle("enable_glm", "GLM")).pack(side="left", padx=2)

    tk.Button(bar, text="使用说明", command=show_help).pack(side="right", padx=3)
    tk.Button(bar, text="配置", command=show_config).pack(side="right", padx=3)

    state["status"] = tk.Label(bar, text="")
    state["status"].pack(side="right", padx=6)

    # ── 托盘：关窗=隐藏；真正的退出只在托盘菜单里 ────────────────────────
    tray = {"icon": None}

    def _make_icon_image():
        from PIL import Image as PILImage, ImageDraw
        img = PILImage.new("L", (64, 64), 255)
        d = ImageDraw.Draw(img)
        d.rectangle([3, 3, 60, 60], outline=0, width=4)      # 屏幕外框
        d.rectangle([14, 26, 49, 49], fill=0)                 # 进度条
        d.rectangle([14, 12, 33, 20], fill=0)                 # 标题条
        return img.convert("RGBA")

    def show_window():
        root.deiconify()
        root.lift()

    def hide_window():
        root.withdraw()
        log("窗口已隐藏到托盘，后台继续刷新（托盘图标右键可退出）")
        if tray["icon"] is not None:
            try:
                tray["icon"].notify("Quote/0 监控仍在后台运行", "已最小化到托盘")
            except Exception:
                pass

    def real_quit():
        log("退出")
        if tray["icon"] is not None:
            try:
                tray["icon"].stop()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", hide_window)
    tk.Button(bar, text="隐藏到托盘", command=hide_window).pack(side="right", padx=3)
    tk.Button(bar, text="退出", command=real_quit).pack(side="right", padx=3)

    try:
        from pystray import Icon, Menu, MenuItem
        tray["icon"] = Icon(
            "quote0-monitor", _make_icon_image(), "Quote/0 用量监控",
            menu=Menu(
                MenuItem("显示窗口", lambda *_: root.after(0, show_window), default=True),
                MenuItem("立即刷新", lambda *_: root.after(0, engine.refresh_now)),
                Menu.SEPARATOR,
                MenuItem("退出", lambda *_: root.after(0, real_quit)),
            ))
        threading.Thread(target=tray["icon"].run, daemon=True).start()
    except Exception as e:      # 无 pystray 时退回旧行为：关窗即退出
        tray["icon"] = None
        root.protocol("WM_DELETE_WINDOW", real_quit)
        log(f"托盘不可用（{e}），关闭窗口将直接退出")

    if start_hidden:
        root.withdraw()

    build_panels()
    engine.start()
    tick()
    if auto_open_config:
        log("检测到首次运行（配置还是模板）——已打开配置窗口，填好并「测试并保存」即可开始")
        root.after(400, show_config)
    root.mainloop()


# ── 开机自启（当前用户启动文件夹的快捷方式，静默模式） ────────────────────────

def autostart_shortcut() -> Path:
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return startup / "quote0-monitor.lnk"


def install_autostart():
    if _FROZEN:
        target, args = sys.executable, "--silent"
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.exists():
            pythonw = Path(sys.executable)      # 非 CPython 时退回当前解释器
        target = str(pythonw)
        args = f'"{HERE / "monitor.py"}" --silent'
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
          "$s.TargetPath='{}';$s.Arguments='{}';$s.WorkingDirectory='{}';"
          "$s.Description='Quote/0 usage monitor (silent)';$s.Save()").format(
        autostart_shortcut(), target, args, HERE)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"创建快捷方式失败: {r.stderr.strip()}")
    print(f"开机自启已安装: {autostart_shortcut()}")
    print(f"  目标: {target} {args}")


def uninstall_autostart():
    lnk = autostart_shortcut()
    if lnk.exists():
        lnk.unlink()
        print(f"已移除开机自启: {lnk}")
    else:
        print("未找到开机自启快捷方式（本就没装）")


# ── 单实例锁 ─────────────────────────────────────────────────────────────────

def acquire_lock() -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        globals()["_lock_sock"] = s      # 持有引用，进程存活期间不释放
        return True
    except OSError:
        return False


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"quote0-monitor v{__version__}")
    ap.add_argument("--silent", action="store_true", help="无界面后台模式")
    ap.add_argument("--once", action="store_true", help="单次抓取推送后退出（自检）")
    ap.add_argument("--install-autostart", action="store_true", help="安装开机自启后退出")
    ap.add_argument("--uninstall-autostart", action="store_true", help="移除开机自启后退出")
    args = ap.parse_args()

    if args.install_autostart:
        install_autostart()
        return
    if args.uninstall_autostart:
        uninstall_autostart()
        return

    fresh = not CONFIG_PATH.exists()
    cfg = load_config()
    mods = load_modules(cfg)
    dash = Dashboard(cfg, mods)

    if args.once:
        if fresh:
            print(f"首次运行：已在 {HERE} 生成 config.json 模板。"
                  "请运行 python monitor.py（或双击 exe），在配置窗口里填写后自动保存。")
            sys.exit(1)
        if _card_unfilled(cfg.get("card_gpt", "")) or _card_unfilled(cfg.get("card_glm", "")):
            print(auto_fill_cards(cfg))
        result = dash.cycle(lambda m: print(m))
        ok = all((result.get(t[1]) or {}).get("ok") for t in dash.tasks())
        sys.exit(0 if ok else 1)

    if not acquire_lock():
        msg = "quote0-monitor 已在运行（看右下角托盘图标，双击可显示窗口）。"
        if args.silent:
            print(msg)
            sys.exit(0)
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showinfo("quote0-monitor", msg + "\n\n本次启动自动退出。")
            r.destroy()
        except Exception:
            print(msg)
        sys.exit(0)

    if args.silent:
        run_ui(dash, cfg, start_hidden=True, auto_open_config=fresh)
    else:
        run_ui(dash, cfg, auto_open_config=fresh)


if __name__ == "__main__":
    main()
