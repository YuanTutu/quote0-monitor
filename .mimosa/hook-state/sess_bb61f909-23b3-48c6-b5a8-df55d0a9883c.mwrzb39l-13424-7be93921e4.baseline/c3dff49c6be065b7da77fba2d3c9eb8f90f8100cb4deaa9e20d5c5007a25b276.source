"""GLM（智谱 BigModel / Z.ai）Coding Plan 用量 provider。

数据源是社区从官方用量页面抓包发现的非公开端点（cc-switch issue #1588，
已在 2026-10 验证可达）：

  个人版   GET https://open.bigmodel.cn/api/monitor/usage/quota/limit
  国际版   GET https://api.z.ai/api/monitor/usage/quota/limit
  团队版   GET https://bigmodel.cn/api/monitor/usage/quota/limit?type=2
            （团队版另需 bigmodel-organization / bigmodel-project 请求头）

认证：Authorization 头直接放 API Key（社区脚本验证过的形式）；
401 时自动用 "Bearer <key>" 重试一次，兼容网关两种解析。

响应 data.limits[] 语义：
  TIME_LIMIT      MCP 月度用量：usage=总额度, currentValue=已用,
                  remaining=剩余（单位：次），percentage=已用百分比
  TOKENS_LIMIT    提示词额度，按 nextResetTime 升序：
                  第 1 个 = 5 小时窗口，第 2 个 = 每周窗口（新套餐才有）
  percentage 一律是"已用"百分比；level = 套餐档位（lite/pro/max…）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

GLM_API_KEY = os.environ.get("GLM_API_KEY", "")
GLM_PLAN    = os.environ.get("GLM_PLAN", "cn").strip().lower()   # cn | intl | team
GLM_ORG     = os.environ.get("GLM_ORG", "")
GLM_PROJECT = os.environ.get("GLM_PROJECT", "")

ENDPOINTS = {
    "cn":   "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
    "intl": "https://api.z.ai/api/monitor/usage/quota/limit",
    "team": "https://bigmodel.cn/api/monitor/usage/quota/limit?type=2",
}


def _extra_headers() -> dict:
    if GLM_PLAN != "team":
        return {}
    headers = {}
    if GLM_ORG:
        headers["bigmodel-organization"] = GLM_ORG
    if GLM_PROJECT:
        headers["bigmodel-project"] = GLM_PROJECT
    return headers


def _coerce_percent(value) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (ValueError, TypeError):
        return None


def _pct_status(pct: int | None) -> str:
    """已用百分比 → ok / warn / hot / unknown（阈值与 quote0-burnout 一致）。"""
    if pct is None:
        return "unknown"
    if pct >= 90:
        return "hot"
    if pct >= 70:
        return "warn"
    return "ok"


def _time_until_ms(val) -> str:
    """毫秒时间戳 → 紧凑倒计时（2h13m / 3d4h / 45m / now）。"""
    if val is None:
        return "?"
    try:
        dt = datetime.fromtimestamp(float(val) / 1000.0, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return "?"
    secs = int((dt - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d{h}h" if h > 0 else f"{d}d"
    if h > 0:
        return f"{h}h{m:02d}m" if m > 0 else f"{h}h"
    return f"{m}m"


def get_usage() -> dict:
    """拉取 GLM Coding Plan 用量。返回 {"ok": bool, ...}。"""
    if not GLM_API_KEY:
        return {"ok": False, "status": "no key"}

    url = ENDPOINTS.get(GLM_PLAN, ENDPOINTS["cn"])
    base_headers = {"Accept": "application/json", **_extra_headers()}

    # 社区验证的形式是裸 key；401 时再用 Bearer 前缀重试一次。
    last_status = "error"
    for auth_value in (GLM_API_KEY, f"Bearer {GLM_API_KEY}"):
        try:
            r = requests.get(
                url,
                headers={**base_headers, "Authorization": auth_value},
                timeout=20,
            )
        except requests.RequestException as e:
            return {"ok": False, "status": f"network: {type(e).__name__}"}
        if r.status_code == 401:
            last_status = "401 unauthorized"
            continue
        break

    if r.status_code != 200:
        return {"ok": False, "status": f"HTTP {r.status_code}"}
    try:
        body = r.json()
    except ValueError:
        return {"ok": False, "status": "bad json"}

    if not (body.get("success") or body.get("code") == 200):
        return {"ok": False, "status": str(body.get("msg") or "api error")[:60]}
    return {"ok": True, "data": body.get("data") or {}, "raw": body}


def build_snapshot(raw: dict) -> dict:
    """原始响应 → 渲染器用的结构化快照（字段名对齐 quote0-burnout 的 usage 形状）。"""
    if not raw.get("ok"):
        return {"ok": False, "status": "error",
                "raw_status": raw.get("status", "error")}

    data = raw.get("data", {})
    limits = data.get("limits") or []

    tokens = sorted(
        (l for l in limits if l.get("type") == "TOKENS_LIMIT"),
        key=lambda l: l.get("nextResetTime") or 0,
    )
    mcp_raw = next((l for l in limits if l.get("type") == "TIME_LIMIT"), None)

    def _pct(entry):
        return _coerce_percent(entry.get("percentage")) if entry else None

    def _reset(entry):
        return _time_until_ms(entry.get("nextResetTime")) if entry else "?"

    short = tokens[0] if tokens else None          # 5h 窗口
    long_ = tokens[1] if len(tokens) > 1 else None  # 每周窗口（老套餐没有）

    used_list = [p for p in (_pct(short), _pct(long_), _pct(mcp_raw)) if p is not None]
    worst = max(used_list) if used_list else None

    mcp = None
    if mcp_raw:
        mcp = {
            "total": mcp_raw.get("usage"),
            "used": mcp_raw.get("currentValue"),
            "remaining": mcp_raw.get("remaining"),
            "used_percent": _pct(mcp_raw),
            "reset": _reset(mcp_raw),
        }

    return {
        "ok": True,
        "plan": (data.get("level") or "").lower(),
        "short_label": "5h",
        "short_used_percent": _pct(short),
        "short_reset": _reset(short),
        "long_label": "Wk",
        "long_used_percent": _pct(long_),
        "long_reset": _reset(long_),
        "mcp": mcp,
        "status": _pct_status(worst),
        "raw_status": "",
    }
