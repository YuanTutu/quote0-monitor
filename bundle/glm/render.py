"""渲染 296×152 纯黑白 PNG（Quote/0 电子墨水屏）。

单 provider（GLM）独占整屏，视觉语言沿用 quote0-burnout：
像素字体原生点阵、点阵进度条（条内填充 = 余量）、虚线分隔线。
严格 1-bit 黑白、不做灰阶抖动，推送时 ditherType=NONE，避免小字号残影。
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 296, 152
PAD = 10
BLACK, WHITE = 0, 255

_ASSETS = Path(__file__).parent / "assets" / "fonts"
PIXEL_FONT = _ASSETS / "Minecraftia-Regular.ttf"    # 8px — 时间戳/页脚
OP_FONT    = _ASSETS / "PixelOperator.ttf"          # 16px — 标题/行标签
VCR_FONT   = _ASSETS / "VCR_OSD_MONO_1.001.ttf"     # 21px — 大号百分比

LABEL_W = 36        # 行标签列宽（"5h"/"Wk"/"MCP"）
BAR_H = 16
ROW_PITCH = 30
ROWS_Y0 = 44
DIVIDER_TOP = 34
DIVIDER_BOTTOM = 132

_pixel_cache = None
_op_cache = None
_vcr_cache = None


def _pixel() -> ImageFont.FreeTypeFont:
    global _pixel_cache
    if _pixel_cache is None:
        _pixel_cache = ImageFont.truetype(str(PIXEL_FONT), 8)
    return _pixel_cache


def _op() -> ImageFont.FreeTypeFont:
    global _op_cache
    if _op_cache is None:
        _op_cache = ImageFont.truetype(str(OP_FONT), 16)
    return _op_cache


def _vcr() -> ImageFont.FreeTypeFont:
    global _vcr_cache
    if _vcr_cache is None:
        _vcr_cache = ImageFont.truetype(str(VCR_FONT), 21)
    return _vcr_cache


def _tsize(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _divider(draw, y, dash_len=6, gap_len=4):
    """zellux 风格虚线：6px 实线 / 4px 间隔。"""
    x = 0
    while x < W:
        draw.line([(x, y), (min(x + dash_len - 1, W), y)], fill=BLACK, width=1)
        x += dash_len + gap_len


def _bar_dots(draw, x, y, w, h, remaining_pct):
    """点阵进度条：外框 + 填充（=余量）+ 空区的点阵网格（4px 间距）。"""
    remaining_pct = max(0, min(100, remaining_pct if remaining_pct is not None else 0))
    draw.rectangle([x, y, x + w - 1, y + h - 1], outline=BLACK)
    filled = int((w - 2) * remaining_pct / 100)
    if filled > 0:
        draw.rectangle([x + 1, y + 1, x + filled, y + h - 2], fill=BLACK)
    dot_spacing = 4
    filled_x1 = x + 1 + filled
    margin = dot_spacing // 2
    for dy in range(y + 1 + margin, y + h - 1 - margin + 1, dot_spacing):
        for dx in range(x + 1 + margin, x + w - 1 - margin + 1, dot_spacing):
            if dx >= filled_x1:
                draw.point((dx, dy), fill=BLACK)


def _usage_rows(sn: dict) -> list[tuple[str, int | None, str | None]]:
    """(标签, 已用百分比, 重置倒计时) 行列表；缺数据的窗口自动省略。

    MCP 月度次数不上屏：变化慢（月窗口）且远离稀缺线，不配占主视觉；
    它的信息在需要时去控制台看一眼即可（版面按 变化速度×稀缺风险 分配）。"""
    rows = []
    if sn.get("short_used_percent") is not None:
        rows.append((sn.get("short_label", "5h"), sn["short_used_percent"],
                     sn.get("short_reset", "?")))
    if sn.get("long_used_percent") is not None:
        rows.append((sn.get("long_label", "Wk"), sn["long_used_percent"],
                     sn.get("long_reset", "?")))
    return rows


def _footer_left(sn: dict) -> str:
    """页脚详情行：窗口重置倒计时 + 重置卡张数。

    重置卡（reset_cards: {"five_hour": n, "week": m}）只显示数量不放到期
    时间——数量是常驻可操作信号，到期是低频信息，上屏只会挤。"""
    bits = []
    if sn.get("short_used_percent") is not None and sn.get("short_reset") not in (None, "?"):
        bits.append(f"5h {sn['short_reset']}")
    if sn.get("long_used_percent") is not None and sn.get("long_reset") not in (None, "?"):
        bits.append(f"Wk {sn['long_reset']}")
    rst = sn.get("reset_cards")
    if rst and (rst.get("five_hour") or rst.get("week")):
        bits.append(f"RESET {rst.get('five_hour', 0)}+{rst.get('week', 0)}")
    return "  ".join(bits)


def _status_label(sn: dict) -> str:
    if sn.get("_cached"):
        return "NET ERR"     # 网络停更：屏上数字是最后一次成功获取的快照
    if not sn.get("ok"):
        return "ERROR"
    return sn.get("status", "unknown").upper()


def render_image(snapshot: dict) -> bytes:
    """快照 → 296×152 1-bit PNG 字节。"""
    img = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)
    sn = snapshot.get("glm", snapshot)  # 允许直接传 provider 快照
    ts = snapshot.get("updated_at") or datetime.now().strftime("%H:%M")
    if snapshot.get("_cached"):
        ts += "*"   # 缓存兜底标记（沿用 quote0-burnout 惯例）

    op, small, vcr = _op(), _pixel(), _vcr()

    # ── 页眉：GLM 字标 + CODING PLAN [档位] + 刷新时间 ──────────────────
    draw.text((PAD, 6), "GLM", font=vcr, fill=BLACK)
    gw, _ = _tsize(draw, "GLM", vcr)
    plan = (sn.get("plan") or "").strip()
    title = f"CODING PLAN {plan.upper()}" if plan else "CODING PLAN"
    draw.text((PAD + gw + 6, 11), title, font=op, fill=BLACK)

    tsw, _ = _tsize(draw, ts, small)
    draw.text((W - PAD - tsw, 8), ts, font=small, fill=BLACK)

    _divider(draw, DIVIDER_TOP)

    # ── 数据行 / 错误态 ─────────────────────────────────────────────────
    if sn.get("ok"):
        rows = _usage_rows(sn)
        # 右侧大号百分比统一右对齐（按最宽的留位）
        notes = [f"{100 - (used if used is not None else 0)}%" for _, used, _ in rows]
        note_w = max((_tsize(draw, n, vcr)[0] for n in notes), default=0)
        note_x = W - PAD - note_w
        bar_x = PAD + LABEL_W
        bar_w = note_x - 6 - bar_x

        # 行锚点随行数自适应：≤2 行用 48/40（与 quote0-burnout 的 CHATGPT
        # 单面版式同构，双行铺满中段），≥3 行回到紧凑的 44/30
        if len(rows) >= 3:
            y, pitch = ROWS_Y0, ROW_PITCH
        else:
            y, pitch = 48, 40
        if not rows:
            draw.text((PAD, y), "no quota data", font=op, fill=BLACK)
        for label, used, _reset in rows:
            # VCR 21px 的字形墨迹在 bbox (4..19)，行锚点即条顶
            bar_y = y + 4
            _, lh = _tsize(draw, label, op)
            draw.text((PAD, bar_y + (BAR_H - lh) // 2), label, font=op, fill=BLACK)
            remaining = None if used is None else 100 - used
            note = f"{remaining:.0f}%"
            nw, nh = _tsize(draw, note, vcr)
            draw.text((W - PAD - nw, y + (BAR_H + 8 - nh) // 2), note, font=vcr, fill=BLACK)
            if used is not None and bar_w > 6:
                _bar_dots(draw, bar_x, bar_y, bar_w, BAR_H, remaining)
            y += pitch
    else:
        err = sn.get("raw_status", "error")
        draw.text((PAD, ROWS_Y0 + 4), err[:24], font=op, fill=BLACK)
        draw.text((PAD, ROWS_Y0 + 28), "check GLM_API_KEY / network", font=small, fill=BLACK)

    # ── 页脚：详情（左） + 状态（右） ───────────────────────────────────
    _divider(draw, DIVIDER_BOTTOM)
    left = _footer_left(sn) if sn.get("ok") else ""
    status = _status_label(sn)           # 停更态徽标 NET ERR（数字=快照）
    sw, _ = _tsize(draw, status, small)
    max_left_w = W - 2 * PAD - sw - 8
    while left and _tsize(draw, left, small)[0] > max_left_w:
        left = left[:-1]   # 像素字体没有省略号字形，直接截断
    draw.text((PAD, 139), left, font=small, fill=BLACK)
    draw.text((W - PAD - sw, 139), status, font=small, fill=BLACK)

    img = img.convert("1", dither=Image.Dither.NONE)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    # mock 数据自测：python render.py
    snap = {
        "glm": {
            "ok": True,
            "plan": "pro",
            "short_label": "5h", "short_used_percent": 44, "short_reset": "2h13m",
            "long_label": "Wk", "long_used_percent": 53, "long_reset": "3d4h",
            "mcp": {"total": 1000, "used": 72, "remaining": 928,
                    "used_percent": 7, "reset": "12d"},
            "status": "warn",
        },
        "updated_at": datetime.now().strftime("%H:%M"),
    }
    png = render_image(snap)
    out = Path(__file__).parent / "tmp" / "preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    print(f"Saved {out} ({len(png)} bytes)")
