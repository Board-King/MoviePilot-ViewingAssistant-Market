"""Native MoviePilot form and saved-scope confirmation for automatic subscriptions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from .source_registry import SOURCE_SPECS
from .ui import _button, _caption, _card, _dict, _field, _node, _text


EXECUTION_DEFAULTS = {
    "fusion_execution_enabled": False,
    "fusion_execution_owner": "",
    "fusion_execution_sources": [],
    "fusion_execution_daily_limit": 1,
    "fusion_execution_preset": "conservative",
    "fusion_execution_legacy_isolated": False,
    "fusion_execution_storage_verified": False,
}

_SOURCE = "tmdb_popular_movie"
_SOURCE_LABEL = SOURCE_SPECS[_SOURCE]["label"]
_PRESETS = {"conservative": "严选", "balanced": "均衡"}
_CHOICE_PROPS = {"item-title": "title", "item-value": "value"}
_STATES = {
    "disabled": "已关闭", "ready": "等待授权", "armed": "已授权",
    "blocked": "暂缓执行", "paused": "已暂停", "unknown": "提交结果待核实", "busy": "正在处理",
}
_RESULTS = {
    "confirmed": "已确认订阅", "existing": "已有订阅", "unknown": "结果待核实",
    "queued": "已排队", "submitting": "正在提交", "cancelled": "已取消",
}
_REASONS = {
    "STORAGE_FAILED": "执行记录保存失败。请检查 MoviePilot 数据库和磁盘。",
    "NOT_CONFIGURED": "在设置中补全订阅用户、来源和每日上限。",
    "CONFIG_CHANGED": "授权范围已经变更，请核对当前设置后重新授权。",
    "CONFIG_UNAVAILABLE": "暂时无法读取执行配置，请检查 MoviePilot 的存储状态。",
    "SCOPE_CHANGED": "已授权的范围发生变化，请核对当前设置后重新授权。",
    "OWNER_REQUIRED": "请在设置中填写有效的 MoviePilot 订阅用户名。",
    "SOURCE_REQUIRED": "请选择要授权的电影来源并保存。",
    "LEGACY_NOT_ISOLATED": "请先停用该来源原有的自动订阅入口，并在设置中确认。",
    "PROFILE_REQUIRED": "请先验证当前 Emby 用户并更新观影偏好。",
    "INVALID_POLICY": "请检查已保存的预设和每日额度。",
    "INCOMPATIBLE": "无法验证订阅用户或 MoviePilot 版本。请核对账号和版本。",
    "QUOTA_REACHED": "额度已满，请查看待核实记录或等待次日额度。",
    "JOURNAL_FULL": "执行记录已达到容量上限，请检查 MoviePilot 存储与待核实记录。",
    "PAUSED": "后台执行已暂停。",
    "LIBRARY_UNVERIFIED": "未能核实完整媒体库或作品身份。请检查媒体库连接和作品资料。",
    "SUBSCRIPTION_UNVERIFIED": "无法核实现有或已完成的订阅。请检查 MoviePilot 订阅记录。",
    "SUBSCRIPTION_CONFLICT": "现有订阅与本次目标存在冲突。请在 MoviePilot 中核对作品和订阅用户。",
    "LIBRARY_PRESENT": "媒体库已有这部电影，本次跳过。",
    "PREEXISTING": "MoviePilot 已有对应订阅，本次跳过。",
    "SPACE_UNVERIFIED": "下载目录映射未确认。请核对 MoviePilot 与下载器的实际目录和挂载。",
    "SPACE_LOW": "电影下载目录可用空间低于 100 GiB，释放空间后再试。",
}
_LOCAL_TZ = timezone(timedelta(hours=8))


def get_execution_form():
    return [_card("自动订阅", [
        _node("VRow", content=[
            _field("VSwitch", "fusion_execution_enabled", "启用自动订阅", cols=12, color="primary")
        ]),
        _node("div", props={"show": "{{ fusion_execution_enabled }}"}, content=[
          _node("VRow", content=[
            _field("VTextField", "fusion_execution_owner", "MoviePilot 订阅用户名", autocomplete="off",
                   hint="用于创建订阅，与 Emby 用户分开设置。"),
            _field("VSelect", "fusion_execution_sources", "允许自动订阅的来源", multiple=True,
                   chips=True, clearable=True, items=[{"title": _SOURCE_LABEL, "value": _SOURCE}], **_CHOICE_PROPS),
            _field("VSelect", "fusion_execution_preset", "订阅预设", items=[
                {"title": "严选 · 较高偏好门槛", "value": "conservative"},
                {"title": "均衡 · 适度放宽偏好门槛", "value": "balanced"},
            ], **_CHOICE_PROPS),
            _field("VTextField", "fusion_execution_daily_limit", "每日上限（部）", type="number", min=1, max=5),
          ]),
          _node("div", "授权前核对", props={"class":"text-subtitle-2 mt-3 mb-1"}),
          _node("VRow", props={"dense":True}, content=[
            _field("VCheckbox", "fusion_execution_legacy_isolated", "同来源的旧自动订阅已停用，或原本没有", cols=12, density="compact"),
            _field("VCheckbox", "fusion_execution_storage_verified", "已核对 MP、下载器临时目录和最终电影目录指向同一存储，没有改写电影下载路径的规则", cols=12, density="compact"),
          ]),
          _caption("保存后核对范围并授权。"),
        ])
    ])]


def _counter(value):
    return str(value) if type(value) is int and 0 <= value <= 1_000_000 else "未知"


def _when(value):
    try:
        if type(value) in (float, int) and value > 0:
            moment = datetime.fromtimestamp(value, _LOCAL_TZ)
        elif isinstance(value, str) and 0 < len(value) <= 64:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None or moment.utcoffset() is None:
                return ""
            moment = moment.astimezone(_LOCAL_TZ)
        else:
            return ""
        return moment.strftime("%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return ""


def _setting(label, value):
    return _node("div", content=[
        _caption(label, ""), _node("strong", value, props={"class": "text-body-2 font-weight-medium text-break"}),
    ])


def _recent(rows):
    entries = []
    for row in rows[:5] if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        state = row.get("state")
        result = _RESULTS.get(state.lower(), "状态待核对") if isinstance(state, str) else "状态待核对"
        stamp = _when(row.get("updated_at"))
        entries.append(_node("div", props={"class": "d-flex flex-wrap justify-space-between ga-2 py-1"}, content=[
            _node("span", _text(row.get("title"), "未命名作品", limit=180), props={"class": "text-break"}),
            _caption(" · ".join(filter(None, [result, stamp])), ""),
        ]))
    if not entries:
        return []
    return [_node("details", props={"class": "mt-3"}, content=[
        _node("summary", "最近执行记录", props={"class": "text-caption text-primary cursor-pointer"}),
        _node("div", props={"class": "pt-2"}, content=entries),
    ])]


def render_execution(status, plugin_id="PtSubtitleFollowup"):
    status = _dict(status)
    enabled, armed = status.get("enabled") is True, status.get("armed") is True
    state = status.get("state")
    label = _STATES.get(state, "状态待核对") if isinstance(state, str) else "状态待核对"
    reason, unknown = status.get("last_reason"), status.get("unknown_count")
    if (not enabled and not armed and state == "disabled" and unknown == 0
            and reason in (None, "DISABLED", "REVOKED", "NOT_AUTHORIZED")):
        return [_node("div", props={"class": "d-flex align-center justify-space-between border rounded-lg mb-4 px-4 py-3"},
                      content=[_node("span", "自动订阅", props={"class": "text-subtitle-2"}), _caption("已关闭", "")])]
    sources = status.get("sources")
    source_label = _SOURCE_LABEL if sources == [_SOURCE] else "尚未选择" if sources == [] else "范围待核对"
    owner = status.get("owner")
    owner_valid = isinstance(owner, str) and bool(owner) and _text(owner, limit=128) == owner
    preset = status.get("preset")
    preset_label = _PRESETS.get(preset) if isinstance(preset, str) else None
    daily_limit = status.get("daily_limit")
    cap_valid = type(daily_limit) is int and 1 <= daily_limit <= 5
    scope = [_setting("订阅用户（MP）", owner if owner_valid else "待设置"),
             _setting("授权来源", source_label), _setting("订阅预设", preset_label or "待设置")]
    profile = _text(status.get("profile_name"), limit=100)
    if profile and status.get("can_authorize") is True:
        scope.append(_setting("Emby 偏好", profile))
    overview = [_node("div", props={"style":"display:grid;gap:10px 16px;"
                    "grid-template-columns:repeat(auto-fit,minmax(min(100%,130px),1fr))"}, content=scope)]
    if reason in ("LIBRARY_PRESENT", "PREEXISTING"):
        overview.append(_caption(_REASONS[reason]))
    elif isinstance(reason, str) and reason in _REASONS:
        overview.append(_node("VAlert", props={"type": "warning", "variant": "tonal", "density": "compact",
                                              "class": "mt-3", "text": _REASONS[reason]}))
    elif state == "blocked":
        overview.append(_caption("请核对已保存的授权范围与 MoviePilot 运行状态。"))
    elif reason in ("NO_ELIGIBLE", "HELD"):
        overview.append(_caption("本轮暂无符合预设的候选。" if reason == "NO_ELIGIBLE" else "本轮候选未通过新增前检查，已暂缓。"))
    if type(unknown) is int and 0 < unknown <= 1_000_000:
        overview.append(_node("VAlert", props={"type": "warning", "variant": "tonal", "density": "compact",
                                            "class": "mt-3", "text": f"{unknown} 笔提交结果待核实，额度继续保留。"}))
    digest = status.get("authorization_digest")
    valid_digest = isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None
    can_authorize = (enabled and not armed and status.get("can_authorize") is True and owner_valid
                     and sources == [_SOURCE] and status.get("media_types") == ["movie"]
                     and preset_label is not None and cap_valid and valid_digest)
    if armed:
        note = "撤销后停止新增，已有订阅保留。"
        action = _button("撤销自动订阅授权", plugin_id, "execution/revoke", "POST")
    else:
        note = "授权后将自动创建订阅，MoviePilot 可能随即搜索和下载。"
        action = _button("确认范围并授权", plugin_id, "execution/authorize", "POST", primary=True,
                         disabled=not can_authorize)
        if can_authorize and "events" in action:
            action["events"]["click"]["params"] = {"expected_digest": digest}
        else:
            action.pop("events", None)
    overview.append(_node("div", props={"class": "d-flex flex-wrap align-center ga-2 mt-3",
                                        "data-va-execution-review-action": True},
                          content=[_caption(note, ""), action]))
    if enabled or armed:
        overview.append(_node("details", props={"class":"mt-3"}, content=[
            _node("summary", "额度与磁盘规则", props={"class":"text-caption text-medium-emphasis cursor-pointer"}),
            _caption("额度按北京时间结算。更改授权范围需重新授权。", "mt-1"),
            _caption("每个可用电影下载目录须至少有 100 GiB 可用空间。Unraid 用户共享显示汇总空间。", "mt-1"),
        ]))
    overview.extend(_recent(status.get("recent")))
    card = _card("自动订阅", overview)
    heading = card["content"][0]
    heading["props"]["class"] += " d-flex flex-wrap align-center ga-2"
    tint = ("success" if state == "armed" else "warning" if state in ("blocked", "unknown", "paused") else "secondary")
    heading["content"] = [_node("VChip", label, props={"size":"small", "variant":"tonal", "color":tint})]
    if enabled or armed:
        heading["content"].append(_node("div", props={"class":"d-flex align-baseline ga-2 ms-auto",
                                                     "title":"今日已用 / 每日上限（北京时间）"}, content=[
            _caption("今日已用", ""),
            _node("strong", f"{_counter(status.get('used_today'))} / {daily_limit if cap_valid else '—'} 部",
                  props={"class":"text-body-1 font-weight-bold","style":"font-variant-numeric:tabular-nums"})
        ]))
    return [card]
