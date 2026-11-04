"""Small native Vuetify views for a read-only, single-user profile.

Pure JSON builders: no host imports, I/O, credentials, HTML, or browser scripts.
Page actions use the V2 PageRender events contract and saved configuration only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re


FORM_DEFAULTS = {
    "fusion_enabled": False, "fusion_server_ref": "", "fusion_user_id": "",
    "fusion_library_mode": "unselected", "fusion_library_ids": "",
    "fusion_interval_hours": 6,
}

_STATES = {
    "disabled": ("已暂停", "secondary"),
    "unconfigured": ("待完善配置", "warning"),
    "unverified": ("待验证绑定", "warning"),
    "validating": ("正在验证", "info"),
    "scope_required": ("待选择授权库", "warning"),
    "queued": ("等待快照", "info"),
    "running": ("正在更新画像", "info"),
    "ready": ("快照已就绪", "success"),
    "error": ("本次读取未完成", "warning"),
    "paused": ("已暂停", "secondary"),
}

_ERRORS = {
    "STORAGE_FAILED": "保存失败，请检查 MoviePilot 数据库和磁盘。",
    "AUTH_REQUIRED": "Emby 凭据已失效，请在 MoviePilot 更新密钥后验证绑定。",
    "FORBIDDEN": "Emby 拒绝读取，请检查该用户和连接密钥的媒体库权限。",
    "RATE_LIMITED": "Emby 暂时限流，请稍后重试。",
    "SERVER_MISMATCH": "服务器与原绑定不符，请核对 MoviePilot 的 Emby 地址。",
    "USER_MISMATCH": "用户与原绑定不符，请核对 Emby 用户 ID。",
    "USER_DISABLED": "绑定用户已停用，请在 Emby 检查账号后再验证。",
    "INVALID_SCOPE": "媒体库范围无效，请验证绑定并重新选择媒体库。",
    "LIBRARY_NOT_VISIBLE": "所选媒体库不在该用户的可见范围，请检查权限或重新选择。",
    "EMPTY": "授权库未返回影视条目，请检查用户和媒体库范围。",
    "EMPTY_SNAPSHOT": "授权库未返回影视条目，请检查用户和媒体库范围。",
    "INCOMPLETE": "读取未完成，请稍后重试。",
    "ITEM_LIMIT": "条目过多，请缩小为指定媒体库。",
    "PAGE_LIMIT": "已达读取上限，请缩小为指定媒体库。",
    "CANCELLED": "读取已取消，请核对当前绑定。",
    "DEADLINE_EXCEEDED": "读取超时，请检查 Emby 或缩小授权范围。",
    "NETWORK_ERROR": "无法连接 Emby，请检查 MoviePilot 的媒体服务器连接。",
    "BINDING_UNVERIFIED": "请核对服务器和用户 ID，再点「验证绑定」。",
    "SERVER_ERROR": "Emby 暂时不可用，请检查服务器状态。",
    "INVALID_RESPONSE": "Emby 响应异常，请检查服务器地址和反向代理。",
    "NOT_FOUND": "找不到用户或接口，请核对用户 ID 和 Emby 地址。",
    "PROFILE_FAILED": "偏好数据未通过校验，请稍后重试。",
    "PARTIAL_PAGE": "Emby 返回的数据不完整，请在媒体库扫描结束后重试。",
    "SNAPSHOT_CHANGED": "读取时媒体库发生变化，请在扫描结束后重试。",
    "DUPLICATE_ITEM": "读取到重复条目，请在媒体库扫描结束后重试。",
    "INVALID_CONFIG": "Emby 配置不完整，请在 MoviePilot 检查连接。",
    "INVALID_HOST": "Emby 地址无效，请在 MoviePilot 核对服务器地址。",
    "INVALID_CREDENTIAL": "Emby 凭据无效，请在 MoviePilot 核对密钥。",
    "RESPONSE_TOO_LARGE": "返回数据过大，请缩小为指定媒体库。",
    "UNSUPPORTED_ENCODING": "响应编码不受支持，请检查反向代理或使用 Emby 直连地址。",
    "USERDATA_UNAVAILABLE": "缺少该用户的观看数据，请检查用户权限和 Emby 连接。",
    "REDIRECT_BLOCKED": "连接被重定向，请核对代理或使用 Emby 直连地址。",
    "FAILED": "读取失败，请检查 Emby 连接和授权范围。",
}


def _text(value, default="", limit=180):
    if not isinstance(value, str):
        return default
    return "".join(char for char in value.strip() if char >= " " and char != "\x7f")[:limit] or default


def _dict(value):
    return value if isinstance(value, dict) else {}


def _number(value, default=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return max(0.0, float(value))


def _stamp(value):
    if _number(value) <= 0:
        return ""
    try:
        return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return ""


def _node(component, text=None, *, props=None, content=None, **extra):
    result = {"component": component}
    if props:
        result["props"] = props
    if text is not None:
        result["text"] = text
    if content is not None:
        result["content"] = content
    result.update(extra)
    return result


def _caption(text, classes="mt-2"):
    return _node("div", text, props={"class": f"text-caption text-medium-emphasis {classes}"})


def _chip(text, color="secondary"):
    return _node("VChip", text, props={"size": "small", "variant": "tonal", "color": color,
                                     "class": "mr-2 mb-2"})


def _card(title, children):
    return _node("VCard", props={"variant": "flat", "border": True, "class": "va-panel rounded-lg mb-4"}, content=[
        _node("VCardTitle", title, props={"class": "text-subtitle-1 pt-4 px-4"}),
        _node("VCardText", props={"class": "px-4 pb-4"}, content=children),
    ])


def _field(component, model, label, *, cols=6, **props):
    return _node("VCol", props={"cols": 12, "md": cols}, content=[
        _node(component, props={"model": model, "label": label, **props})])


def get_form_fields(server_options):
    """Render flat V2 model keys; server choices contain labels/references only."""
    options, seen = [], set()
    for option in server_options if isinstance(server_options, list) else []:
        if not isinstance(option, dict):
            continue
        reference = option.get("value")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 200 or reference in seen:
            continue
        seen.add(reference)
        options.append({"title": _text(option.get("title"), reference), "value": reference})
        if len(options) == 100:
            break
    fields = [
        _node("VRow", content=[
            _field("VSwitch", "fusion_enabled", "定期更新观影偏好", cols=12, color="primary"),
        ]),
        _node("VRow", content=[
            _field("VSelect", "fusion_server_ref", "Emby 服务器", items=options,
                   clearable=True, **{"item-title": "title", "item-value": "value"}),
            _field("VTextField", "fusion_user_id", "Emby 用户 ID", autocomplete="off",
                   hint="请输入 32 位 ID，不是用户名。"),
        ]),
        _node("VRow", content=[
            _field("VSelect", "fusion_library_mode", "授权媒体库", cols=12, items=[
                {"title": "未选择 · 不采集", "value": "unselected"},
                {"title": "仅指定媒体库", "value": "selected_ids"},
                {"title": "该用户全部可见影视库（明确授权）", "value": "all_visible"},
            ], **{"item-title": "title", "item-value": "value"}),
            _field("VTextarea", "fusion_library_ids", "指定媒体库 ID", cols=12, rows=2,
                   show="{{ fusion_library_mode === 'selected_ids' }}",
                   hint="从「绑定与媒体库」复制 ID，用换行或逗号分隔；留空不采集。"),
        ]),
        _caption("仅读取此用户在授权库内的收藏和已看记录。更换用户或范围后，旧偏好将隔离。"),
        _node("details", props={"class": "mt-4"}, content=[
            _node("summary", "更新设置", props={"class": "text-body-2 text-primary cursor-pointer"}),
            _node("VRow", props={"class": "pt-3"}, content=[
                _field("VTextField", "fusion_interval_hours", "更新间隔（小时）", type="number",
                       min=1, max=168, hint="1–168 小时"),
            ]),
        ]),
    ]
    if not options:
        fields.insert(1, _node("VAlert", props={"type": "info", "variant": "tonal", "density": "compact",
                                             "class": "mb-4",
                                             "text": "暂无可选的 Emby 连接，请先在 MoviePilot 配置并启用媒体服务器。"}))
    return [_card("观影偏好", fields)]


def _features(baseline, name, limit):
    entries = _dict(_dict(baseline.get("dimensions")).get(name)).get("features")
    if not isinstance(entries, list):
        return []
    clean = []
    for entry in entries[:500]:
        if not isinstance(entry, dict):
            continue
        label, support = _text(entry.get("label"), limit=128), _number(entry.get("support"))
        if label and support > 0:
            clean.append((label, support))
    return sorted(clean, key=lambda entry: (-entry[1], entry[0]))[:limit]


def _ranking(title, entries, *, cols=6, decades=False):
    rows = []
    strongest = entries[0][1]
    for label, support in entries:
        if decades and re.fullmatch(r"\d{4}s", label):
            label = label[:4] + " 年代"
        rows.append(_node("li", props={"class": "py-2", "style": "display:grid;align-items:center;"
                                "grid-template-columns:minmax(0,1fr) minmax(32px,.9fr);gap:12px"}, content=[
            _node("div", label, props={"class": "text-body-2 text-break"}),
            _node("div", props={"aria-hidden": "true", "class": "rounded-pill", "style":
                  "height:4px;background:rgba(var(--v-theme-primary),.09)"}, content=[
                _node("div", props={"class": "rounded-pill", "style": "height:100%;background:"
                      f"rgba(var(--v-theme-primary),.62);width:{support / strongest * 100:.1f}%"})]),
        ]))
    ranked = _node("ol", props={"aria-label":title + "，按偏好排序", "role":"list", "style":"list-style:none;margin:0;padding:0"},
                   content=rows)
    return _node("VCol", props={"cols": 12, "md": cols}, content=[_card(title, [ranked])])


def profile_summary(status):
    if status.get("enabled") is not True:
        return "观影偏好 · 未启用"
    if status.get("binding_verified") is not True or status.get("stale") is True:
        return "观影偏好"
    baseline = _dict(_dict(status.get("profile")).get("baseline"))
    genres = [label for label, _ in _features(baseline, "genres", 3)]
    return "观影偏好" + (" · " + " / ".join(genres) if genres else "")


def _scope_ready(scope):
    if scope.get("mode") == "all_visible":
        return True
    ids = scope.get("library_ids")
    return (scope.get("mode") == "selected_ids" and isinstance(ids, list) and 0 < len(ids) <= 100
            and all(isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item) for item in ids))


def _button(text, plugin_id, path, method, *, disabled=False, primary=False):
    # Unknown instance IDs must not become arbitrary API paths.
    valid = isinstance(plugin_id, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", plugin_id)
    node = _node("VBtn", text, props={"variant": "flat" if primary else "tonal", "color": "primary",
                                     "size": "small", "class": "mr-2 mb-2", "disabled": bool(disabled or not valid)})
    if valid:
        node["events"] = {"click": {"api": f"plugin/{plugin_id}/fusion/{path}", "method": method, "params": {}}}
    return node


def render_page(status, plugin_id="PtSubtitleFollowup"):
    """Only render allowlisted aggregates; never include arbitrary status fields."""
    status = _dict(status)
    state = status.get("status")
    state = state if isinstance(state, str) else ""
    label, color = _STATES.get(state, ("等待配置", "secondary"))
    enabled, busy = status.get("enabled") is True, state in {"running", "validating", "paused"}
    cooldown = _number(status.get("cooldown_remaining"))
    server = _text(status.get("server_ref"))
    user_id = _text(status.get("user_id"))
    binding_ready = bool(server and re.fullmatch(r"[a-fA-F0-9]{32}", user_id))
    scope = _dict(status.get("scope"))
    scope_ready = _scope_ready(scope)
    verified = status.get("binding_verified") is True
    user = _text(status.get("user_name"), "待验证用户") if binding_ready else "尚未绑定用户"
    ids = scope.get("library_ids") if isinstance(scope.get("library_ids"), list) else []
    scope_label = ("该用户全部可见影视库" if scope.get("mode") == "all_visible" else
                   f"指定 {len(ids)} 个媒体库" if scope_ready else "未选择授权库 · 不采集")
    profile = _dict(status.get("profile"))
    baseline = _dict(profile.get("baseline"))
    statistics = _dict(baseline.get("statistics"))
    main_actions = [
        _button("刷新画像", plugin_id, "refresh", "POST", primary=True,
                disabled=not (enabled and binding_ready and scope_ready and verified) or busy or cooldown > 0),
    ]
    read_status = _button("刷新状态", plugin_id, "status", "GET")
    needs_status = state == "error" or busy or cooldown > 0
    if needs_status:
        main_actions.append(read_status)
    overview = [_node("div", props={"class": "d-flex flex-wrap align-center justify-space-between ga-3"}, content=[
        _node("div", content=[_node("div", user, props={"class": "text-h6 text-break"}),
                              _caption(f"{server or '尚未选择服务器'} · {scope_label}", "mt-1")]),
        _node("div", props={"class":"d-flex flex-wrap"}, content=main_actions),
    ])]
    metrics = []
    for title, key in (("样本（作品 / 季）", "contributing_entities"),
                       ("收藏", "favorite_entities"), ("已看", "played_entities")):
        count = statistics.get(key)
        if type(count) is int and 0 <= count <= 1_000_000:
            metrics.append(_node("div", content=[
                _node("dt", title, props={"class":"text-caption text-medium-emphasis"}),
                _node("dd", str(count), props={"class":"text-h6 font-weight-bold","style":"margin:0;font-variant-numeric:tabular-nums"}),
            ]))
    success = _stamp(status.get("last_success"))
    if metrics:
        overview.append(_node("dl", props={"class":"d-flex flex-wrap ga-5 mt-3"}, content=metrics))
    if success:
        overview.append(_caption("北京时间 · 更新于 " + success, "mt-2"))
    stale = status.get("stale") is True
    if stale:
        overview.append(_caption("画像已过期 · 显示上次完整结果", "mt-2"))
    if state in {"queued", "running", "validating", "paused"}:
        overview.append(_chip(label, color))
    problem, severity, diagnostics = "", "info", []
    if state == "error":
        code = status.get("error_code")
        safe_code = code if isinstance(code, str) and code in _ERRORS else "FAILED"
        problem, severity = _ERRORS[safe_code], "warning"
        diagnostics.append("错误码：" + safe_code)
    elif not enabled:
        problem = "在设置中开启「定期更新观影偏好」。"
    elif not binding_ready:
        problem = "在设置中选择 Emby 服务器并填写用户 ID。"
    elif not verified:
        problem = "请验证绑定，确认当前 Emby 用户。"
    elif not scope_ready:
        problem = "请选择授权媒体库并保存，尚未读取观看记录。"
    elif stale and not busy:
        problem = "刷新画像可更新偏好。"
    if cooldown > 0:
        problem = (problem + " " if problem else "") + f"约 {math.ceil(cooldown / 60)} 分钟后可重试。"
    if problem:
        overview.append(_node("VAlert", props={"type":severity,"variant":"tonal","density":"compact",
                                            "class":"mt-3","text":problem}))
    until = _stamp(status.get("retry_until"))
    if until and (cooldown > 0 or state == "error"):
        diagnostics.append(f"{'可重试时间' if cooldown > 0 else '上次冷却截止'}：{until}（北京时间）")
    if diagnostics:
        overview.append(_node("details", props={"class":"mt-2"}, content=[
            _node("summary", "诊断信息", props={"class":"text-caption text-medium-emphasis cursor-pointer"}),
            _caption(" · ".join(diagnostics), "mt-1"),
        ]))
    views = status.get("visible_libraries")
    libraries = []
    for view in views[:100] if isinstance(views, list) else []:
        if not isinstance(view, dict):
            continue
        identifier = view.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            continue
        libraries.append(_node("div", props={"class": "py-2"}, content=[
            _node("div", _text(view.get("name"), "未命名媒体库"), props={"class": "text-body-2"}),
            _node("div", f"ID：{identifier}", props={"class": "text-caption text-medium-emphasis text-break"}),
        ]))
    binding_details = [
        _button("验证绑定", plugin_id, "validate", "POST",
                disabled=not (enabled and binding_ready) or busy or cooldown > 0),
    ]
    if not needs_status:
        binding_details.append(read_status)
    if libraries:
        binding_details.extend([
            _node("div", "可见媒体库", props={"class": "text-subtitle-2 mt-4"}),
            _caption("可见不等于已授权。按需复制 ID 到设置。", "mt-1"),
            *libraries,
        ])
    overview.append(_node("details", props={"class": "mt-4", "open": bool(enabled and not
                                                                            (binding_ready and verified and scope_ready))}, content=[
        _node("summary", "绑定与媒体库", props={"class": "text-caption text-primary cursor-pointer"}),
        _node("div", props={"class": "pt-3"}, content=binding_details),
    ]))
    page = [_card("观影偏好", overview)]
    genres, decades = _features(baseline, "genres", 6), _features(baseline, "decades", 4)
    people = [("导演倾向", _features(baseline, "directors", 5)),
              ("演员倾向", _features(baseline, "actors", 5))]
    rankings = []
    if genres:
        rankings.append(_ranking("题材倾向", genres, cols=8 if decades else 12))
    if decades:
        rankings.append(_ranking("年代倾向", decades, cols=4 if genres else 12, decades=True))
    if rankings:
        page.append(_node("VRow", content=rankings))
    people = [(title, entries) for title, entries in people if entries]
    if people:
        page.append(_node("VRow", content=[_ranking(title, entries, cols=6 if len(people) == 2 else 12)
                                            for title, entries in people]))
    languages = _features(baseline, "languages", 5)
    if languages:
        page.append(_card("语言", [_node("div", " · ".join(label for label, _ in languages),
                                         props={"class": "text-body-2 text-break"})]))
    if not rankings and not people and not languages and enabled and binding_ready and verified and scope_ready and not problem:
        page.append(_caption("偏好尚待形成。在 Emby 收藏喜欢的影片，再刷新画像。", "mb-4"))
    if not baseline:
        return page
    unknown_time = int(_number(statistics.get("unknown_time_entities")))
    limits = "收藏的权重高于已看；同一作品跨季封顶，较早记录降权。图中条形仅表示相对排序，不是喜欢概率。"
    if unknown_time:
        limits += f" {unknown_time} 条记录没有时间，也已降权。"
    evidence = [_caption(limits, ""),
                _caption("样本按作品或季去重，已收藏的不重复计入已看。按计划更新，不采集实时播放，也不推断完播或反感。", "mt-1")]
    page.append(_node("details", props={"class": "mb-4"}, content=[
        _node("summary", "画像依据", props={"class": "text-caption text-primary cursor-pointer"}),
        _node("div", props={"class": "pt-3"}, content=evidence),
    ]))
    return page
