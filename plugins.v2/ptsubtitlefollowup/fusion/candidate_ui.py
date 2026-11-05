"""Bounded native views for read-only discovery; no host imports or external media.

Only bounded source windows are shown. A candidate is never an
execution permission, and missing library/subscription checks remain explicit.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import math
import re
import struct

from .source_registry import SOURCE_SPECS
from .ui import _button, _caption, _card, _chip, _dict, _field, _node, _number, _text


CANDIDATE_DEFAULTS = {
    "fusion_discovery_enabled": False,
    "fusion_discovery_sources": ["tmdb_popular_movie"],
    "fusion_discovery_interval_hours": 6,
    "fusion_discovery_pages": 3,
}

_SOURCES = {identifier: spec["label"] for identifier, spec in SOURCE_SPECS.items()}
_PREVIEW_NOTE = "候选仅供预览。自动订阅需在独立设置中授权。"

# Official TMDB logo, identical to docs/market-preview/assets/tmdb-blue-short.svg.
# Embed the small image so viewing credits never needs a third-party browser request.
_TMDB_LOGO = (
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNzMuNDIgMzUuNTIiPjxkZWZz"
    "PjxsaW5lYXJHcmFkaWVudCBpZD0iYSIgeTE9IjE3Ljc2IiB4Mj0iMjczLjQyIiB5Mj0iMTcuNzYiIGdyYWRpZW50VW5pdHM9"
    "InVzZXJTcGFjZU9uVXNlIj48c3RvcCBvZmZzZXQ9IjAiIHN0b3AtY29sb3I9IiM5MGNlYTEiLz48c3RvcCBvZmZzZXQ9Ii41"
    "NiIgc3RvcC1jb2xvcj0iIzNjYmVjOSIvPjxzdG9wIG9mZnNldD0iMSIgc3RvcC1jb2xvcj0iIzAwYjNlNSIvPjwvbGluZWFy"
    "R3JhZGllbnQ+PC9kZWZzPjxnIGRhdGEtbmFtZT0iTGF5ZXIgMiI+PHBhdGggZD0iTTE5MS44NSAzNS4zN2g2My45YTE3LjY3"
    "IDE3LjY3IDAgMDAxNy42Ny0xNy42N0ExNy42NyAxNy42NyAwIDAwMjU1Ljc1IDBoLTYzLjlhMTcuNjcgMTcuNjcgMCAwMC0x"
    "Ny42NyAxNy43IDE3LjY3IDE3LjY3IDAgMDAxNy42NyAxNy42N3ptLTE4MS43NS4wNWg3LjhWNi45MkgyOFYwSDB2Ni45aDEw"
    "LjF6bTI4LjEgMEg0NlY4LjI1aC4xbDguOTUgMjcuMTVoNkw3MC4zIDguMjVoLjFWMzUuNGg3LjhWMEg2Ni40NWwtOC4yIDIz"
    "LjFoLS4xTDUwIDBIMzguMnpNODkuMTQuMTJoMTEuN2EzMy41NiAzMy41NiAwIDAxOC4wOCAxIDE4LjUyIDE4LjUyIDAgMDE2"
    "LjY3IDMuMDggMTUuMDkgMTUuMDkgMCAwMTQuNTMgNS41MiAxOC41IDE4LjUgMCAwMTEuNjcgOC4yNSAxNi45MSAxNi45MSAw"
    "IDAxLTEuNjIgNy41OCAxNi4zIDE2LjMgMCAwMS00LjM4IDUuNSAxOS4yNCAxOS4yNCAwIDAxLTYuMzUgMy4zNyAyNC41MyAy"
    "NC41MyAwIDAxLTcuNTUgMS4xNUg4OS4xNHptNy44IDI4LjJoNGEyMS42NiAyMS42NiAwIDAwNS0uNTVBMTAuNTggMTAuNTgg"
    "MCAwMDExMCAyNmE4LjczIDguNzMgMCAwMDIuNjgtMy4zNSAxMS45IDExLjkgMCAwMDEtNS4wOCA5Ljg3IDkuODcgMCAwMC0x"
    "LTQuNTIgOS4xNyA5LjE3IDAgMDAtMi42My0zLjE4QTExLjYxIDExLjYxIDAgMDAxMDYuMjIgOGExNy4wNiAxNy4wNiAwIDAw"
    "LTQuNjgtLjYzaC00LjZ6TTEzMy4wOS4xMmgxMy4yYTMyLjg3IDMyLjg3IDAgMDE0LjYzLjMzIDEyLjY2IDEyLjY2IDAgMDE0"
    "LjE3IDEuMyA3Ljk0IDcuOTQgMCAwMTMgMi43MiA4LjM0IDguMzQgMCAwMTEuMTUgNC42NSA3LjQ4IDcuNDggMCAwMS0xLjY3"
    "IDUgOS4xMyA5LjEzIDAgMDEtNC40MyAyLjgyVjE3YTEwLjI4IDEwLjI4IDAgMDEzLjE4IDEgOC41MSA4LjUxIDAgMDEyLjQ1"
    "IDEuODUgNy43OSA3Ljc5IDAgMDExLjU3IDIuNjIgOS4xNiA5LjE2IDAgMDEuNTUgMy4yIDguNTIgOC41MiAwIDAxLTEuMiA0"
    "LjY4IDkuMzIgOS4zMiAwIDAxLTMuMSAzIDEzLjM4IDEzLjM4IDAgMDEtNC4yNyAxLjY1IDIyLjUgMjIuNSAwIDAxLTQuNzMu"
    "NWgtMTQuNXptNy44IDE0LjE1aDUuNjVhNy42NSA3LjY1IDAgMDAxLjc4LS4yIDQuNzggNC43OCAwIDAwMS41Ny0uNjUgMy40"
    "MyAzLjQzIDAgMDAxLjEzLTEuMiAzLjYzIDMuNjMgMCAwMC40Mi0xLjhBMy4zIDMuMyAwIDAwMTUxIDguNmEzLjQyIDMuNDIg"
    "MCAwMC0xLjIzLTEuMTNBNi4wNyA2LjA3IDAgMDAxNDggNi45YTkuOSA5LjkgMCAwMC0xLjg1LS4xOGgtNS4zem0wIDE0LjY1"
    "aDdhOC4yNyA4LjI3IDAgMDAxLjgzLS4yIDQuNjcgNC42NyAwIDAwMS42Ny0uNyAzLjkzIDMuOTMgMCAwMDEuMjMtMS4zIDMu"
    "OCAzLjggMCAwMC40Ny0xLjk1IDMuMTYgMy4xNiAwIDAwLS42Mi0yIDQgNCAwIDAwLTEuNTgtMS4xOCA4LjIzIDguMjMgMCAw"
    "MC0yLS41NSAxNS4xMiAxNS4xMiAwIDAwLTIuMDUtLjE1aC01Ljl6IiBmaWxsPSJ1cmwoI2EpIiBkYXRhLW5hbWU9IkxheWVy"
    "IDEiLz48L2c+PC9zdmc+"
)


def source_credits():
    """Data attribution in settings, including plugin clones."""
    return _node("details", props={"class":"mt-2 px-2"}, content=[
        _node("summary", "关于与数据来源", props={"class":"text-caption text-medium-emphasis cursor-pointer"}),
        _node("a", props={"href":"https://www.themoviedb.org/", "target":"_blank", "rel":"noopener noreferrer",
                          "class":"d-inline-block mt-3"}, content=[
            _node("img", props={"src":"data:image/svg+xml;base64," + _TMDB_LOGO, "alt":"The Movie Database (TMDB)",
                                "width":92, "style":"height:auto;max-width:100%"})]),
        _caption("This product uses the TMDB API but is not endorsed or certified by TMDB.", "mt-1"),
    ])
_MAX_POSTER_BYTES = 40 * 1024
_STATES = {
    "disabled": ("未启用", "secondary"), "idle": ("等待下次更新", "secondary"),
    "queued": ("已排队", "info"), "running": ("正在更新", "info"),
    "error": ("更新未完成", "warning"), "paused": ("已暂停", "secondary"),
    "ready": ("已更新", "success"), "success": ("已更新", "success"),
    "cooldown": ("冷却中", "warning"), "auth_blocked": ("凭据待处理", "error"),
    "unavailable": ("来源不可用", "warning"),
}
_ERRORS = {
    "AUTH_REQUIRED": "{source} 拒绝访问。请检查来源服务状态与网络，冷却结束后手动更新。",
    "FORBIDDEN": "{source} 拒绝访问。请检查来源服务状态与网络，冷却结束后手动更新。",
    "RATE_LIMITED": "{source} 暂时限流，已进入冷却。请等待，不要连续刷新。",
    "NETWORK_ERROR": "无法连接{source}。请检查 MoviePilot 的网络与代理配置后重试。",
    "SERVER_ERROR": "{source} 暂时返回服务错误。已保留上次结果，等待下一轮重试。",
    "INVALID_CONFIG": "{source} 连接配置未通过校验。请检查 MoviePilot 的来源连接设置。",
    "INVALID_CREDENTIAL": "{source} 连接未通过校验。请检查来源服务状态与网络，冷却结束后手动更新。",
    "INVALID_PROXY": "代理配置无效。请检查 MoviePilot 的代理配置，修复后重试。",
    "INVALID_RESPONSE": "{source} 返回内容不符合预期。请检查网络与代理是否返回了非预期页面。",
    "PARTIAL_PAGE": "本次返回没有完整读完，未将部分结果当作完整更新。请等待重试。",
    "RESPONSE_TOO_LARGE": "接口返回内容超过处理上限。请检查代理是否返回了非预期页面。",
    "UNSUPPORTED_ENCODING": "接口返回了不支持的编码。请核对网络与代理设置。",
    "REDIRECT_BLOCKED": "接口返回非预期跳转。请核对 MoviePilot 的网络与代理设置。",
    "DEADLINE_EXCEEDED": "本次读取超过时限。请检查网络和服务器负载，稍后重试。",
    "STORAGE_FAILED": "候选未能写入存储。请检查 MoviePilot 数据库和磁盘状态，原结果会保留。",
    "DUPLICATE_ITEM": "本次返回中出现重复条目，未作为成功结果保存。请稍后重试。",
    "NOT_FOUND": "{source} 未找到请求的资源。请稍后重试；持续失败时请保留错误码供排查。",
    "HTTP_ERROR": "{source} 返回非预期 HTTP 状态。请检查网络与代理，稍后重试。",
    "CANCELLED": "本轮因停止或配置变化而取消。请确认当前设置，再更新候选。",
    "CLOSED": "读取连接已关闭。本轮未完成，请稍后重试。",
    "INVALID_LIMIT": "读取数量配置未通过校验。请检查更新设置；仍失败时保留错误码供排查。",
    "INVALID_SOURCE": "来源未通过校验。请重新选择当前支持的候选来源后保存。",
    "INVALID_CURSOR": "来源读取进度未通过校验。请手动更新候选，重新开始读取。",
    "INVALID_ENDPOINT": "请求接口未通过校验。请保留错误码供排查，不要在页面中填写第三方接口。",
    "EMPTY_UNEXPECTED": "{source} RSS 返回异常空内容，已保留旧结果。请等待 RSSHub 恢复后重试。",
    "UNSAFE_XML": "{source} RSS 包含不支持的 XML 声明或编码，已保留旧结果。请等待来源恢复后重试。",
    "INVALID_FEED": "{source} RSS 格式不符合预期，已保留旧结果。请等待来源恢复后重试。",
    "ITEM_LIMIT": "{source}返回项目超过处理上限，已保留旧结果。请稍后重试；持续失败时保留错误码供排查。",
    "SHARING_DISABLED": "MoviePilot 的分享功能尚未开启。请先在 MoviePilot 中管理分享开关，再手动更新候选；本插件不会代为开启。",
    "FOLLOWS_EMPTY": "MoviePilot 尚无可用的关注名单。请先在 MoviePilot 中关注分享用户，再手动更新候选。",
    "INVALID_FOLLOWS": "MoviePilot 的关注名单未通过校验。请在 MoviePilot 中检查关注设置，修复后手动更新候选。",
    "CONTEXT_UNAVAILABLE": "无法读取 MoviePilot 的分享设置或关注名单。请检查 MoviePilot 的运行状态，稍后手动更新候选。",
    "PROFILE_GENRES_REQUIRED": "等待可识别的偏好题材。请在观影偏好中核对账号和更新时间。",
    "FAILED": "本次更新未完成。请检查{source}连接，并保留错误码供排查。",
}


def get_candidate_form():
    """Flat saved configuration only; enabled means discovery, never subscribe."""
    return [_card("候选发现", [
        _node("VRow", content=[
            _field("VSwitch", "fusion_discovery_enabled", "定期更新候选", cols=12, color="primary"),
            _field("VSelect", "fusion_discovery_sources", "候选来源", cols=12,
                   multiple=True, chips=True, clearable=True,
                   items=[{"title": label, "value": key} for key, label in _SOURCES.items()],
                   **{"item-title": "title", "item-value": "value", "placeholder": "选择榜单或关注分享"}),
        ]),
        _node("details", props={"class": "mt-2"}, content=[
            _node("summary", "采集频率与范围", props={"class": "text-body-2 text-primary cursor-pointer"}),
            _node("VRow", props={"class": "pt-3"}, content=[
                _field("VTextField", "fusion_discovery_interval_hours", "更新间隔（小时）", type="number",
                       min=1, max=168),
                _field("VTextField", "fusion_discovery_pages", "每轮采集窗口（1–3）", type="number", min=1, max=3,
                       hint="榜单与关注分享按页读取；RSS 取一份快照。每个窗口上限 20 项。"),
            ]),
            _caption("关注分享沿用 MoviePilot 已有分享开关与关注名单。"),
        ]),
    ])]


_BEIJING = timezone(timedelta(hours=8))


def _when(value, missing):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed.astimezone(_BEIJING).strftime("%m-%d %H:%M")
        except (ValueError, OverflowError):
            pass
        return missing
    if _number(value) <= 0:
        return missing
    try:
        return datetime.fromtimestamp(value, _BEIJING).strftime("%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return missing


def _source_rows(status):
    raw = status.get("sources")
    result, seen = [], set()
    for entry in raw[:20] if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or identifier not in _SOURCES or identifier in seen:
            continue
        seen.add(identifier)
        result.append(entry)
    return result


def _window_kind(source):
    kind = _dict(source.get("observed_window")).get("kind")
    return kind if kind in ("snapshot", "numbered", "filtered_pages") else SOURCE_SPECS[source["id"]]["pagination"]


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _window_snapshot(source):
    """Return metrics with their actual units, never extrapolating to an entire list."""
    window = _dict(source.get("observed_window"))
    kind = _window_kind(source)
    received, matched, returned = (_count(window.get(key)) for key in ("received", "matched", "returned"))
    last = _count(source.get("last_count")) if source.get("last_success") else None
    if kind == "snapshot":
        metrics = [("读取", received)] if received is not None else [("采集", last)]
        if returned is not None and returned != received:
            metrics.append(("保留", returned))
        return metrics, "RSSHub · 单份快照"
    if kind == "filtered_pages":
        metrics = [("公共页读取", received), ("匹配", matched), ("纳入", returned)]
    else:
        metrics = [("本页返回", returned if returned is not None else last)]
    page, cap = _count(window.get("page")), _count(window.get("page_cap"))
    scope = []
    if page:
        scope.append(f"第 {page} 页")
    if cap:
        scope.append(f"每轮最多 {cap} 页")
    return metrics, " · ".join(scope) or ("公共分享窗口" if kind == "filtered_pages" else "榜单分页窗口")


def _error_reason(source_id, code):
    label = _SOURCES[source_id]
    transport = SOURCE_SPECS[source_id]["transport"]
    if transport == "tmdb" and code in {"AUTH_REQUIRED", "FORBIDDEN", "INVALID_CREDENTIAL"}:
        return f"{label}凭据无效或访问被拒。请检查 MoviePilot 的 TMDB 配置，冷却结束后手动更新。"
    return _ERRORS[code].format(source=label)


def _source_card(source):
    state = source.get("status")
    label, color = _STATES.get(state, ("等待更新", "secondary")) if isinstance(state, str) else ("等待更新", "secondary")
    blocked = source.get("authentication_blocked") is True
    cooldown = _number(source.get("cooldown_remaining"))
    metrics, window = _window_snapshot(source)
    if blocked:
        label, color = "待手动重试", "error"
    elif source.get("stale") is True and state not in ("unavailable", "error"):
        label, color = "结果已过期", "warning"
    if state == "error" and source.get("stale") is True:
        label = "更新未完成 · 已过期"
    content = [
        _node("div", props={"class": "d-flex flex-wrap align-center justify-space-between ga-2"}, content=[
            _node("strong", _SOURCES[source["id"]], props={"class": "text-subtitle-2"}),
            _node("span", label, props={"class": f"text-caption text-{color}"}),
        ]),
        _node("div", props={"class": "d-flex flex-wrap align-end ga-4 my-2"}, content=[
            _node("div", content=[
                _node("span", title, props={"class": "me-1 text-caption text-medium-emphasis"}),
                _node("strong", f"{value} 项" if value is not None else "—",
                      props={"class": "text-subtitle-1 font-weight-bold", "style": "font-variant-numeric:tabular-nums"}),
            ]) for title, value in metrics]),
        _caption(window, "mb-2"),
        _node("div", props={"class": "border-t pt-2", "style": "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px"}, content=[
            _node("div", content=[_caption(title, ""), _node("time", stamp, props={
                "class": "text-body-2", "title": "北京时间（UTC+8）", "style": "font-variant-numeric:tabular-nums"})])
            for title, stamp in (("上次成功", _when(source.get("last_success"), "尚无记录")),
                                 ("最早可手动重试" if blocked else "下次计划", _when(source.get("next_due"), "待定")))]),
    ]
    if source.get("stale") is True and "过期" not in label:
        content.append(_caption("保留的结果已过期", "mt-1 text-warning"))
    code = source.get("error_code")
    if blocked or state in ("error", "unavailable") or isinstance(code, str) and code:
        safe_code = code if isinstance(code, str) and code in _ERRORS else ("AUTH_REQUIRED" if blocked else "FAILED")
        reason = _error_reason(source["id"], safe_code)
        if source["id"] == "mp_followed_shares" and source.get("isolation_pending") is True and safe_code == "STORAGE_FAILED":
            reason = "关注变化已在当前实例隔离，但保存未完成；请检查数据库和磁盘，修复前暂停此来源采集。"
        content.append(_node("VAlert", props={"type": "warning", "variant": "tonal", "density": "compact",
                                              "class": "mt-3", "text": f"{reason} 错误码：{safe_code}"}))
    if cooldown > 0:
        content.append(_caption(f"约 {math.ceil(cooldown / 60)} 分钟后可重试。"))
    if blocked:
        content.append(_caption("已暂停自动重试，修复连接后请手动更新。"))
    return _node("article", props={"class": "va-source-card pa-3 rounded-lg", "aria-label": _SOURCES[source["id"]],
                 "style": "min-width:0;background:rgba(var(--v-theme-on-surface),.04)"}, content=content)


def _source_grid(sources):
    return _node("div", props={"class": "va-source-grid my-2", "style": "display:grid;gap:8px;"
                "grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))"},
                content=[_source_card(source) for source in sources])


_DIMENSIONS = {"genres": "题材", "decades": "年代", "directors": "导演", "actors": "演员",
               "languages": "语言", "keywords": "关键词"}


def _labels(values, limit=6, text_limit=220):
    result = []
    for value in values[:30] if isinstance(values, list) else []:
        clean = _text(value, limit=text_limit)
        if clean and clean not in result:
            result.append(clean)
        if len(result) >= limit:
            break
    return result


def _ranked_sources(candidate):
    rows = candidate.get("sources")
    found = {}
    for row in rows[:30] if isinstance(rows, list) else []:
        identifier = row.get("source_id") if isinstance(row, dict) else None
        if not isinstance(identifier, str) or identifier not in _SOURCES:
            continue
        rank = row.get("rank")
        if identifier not in found or found[identifier] is None:
            found[identifier] = rank if type(rank) is int and 1 <= rank <= 1_000_000 else None
    return [_SOURCES[key] + (f" · 第 {rank} 名" if rank else "") for key, rank in found.items()]


def _season(candidate):
    values = candidate.get("season_hints")
    seasons = sorted(set(value for value in values[:100] if type(value) is int and 0 <= value <= 999)) if isinstance(values, list) else []
    info = _dict(candidate.get("display_details"))
    display_season, total, episodes = (info.get(key) for key in ("season", "season_count", "episodes"))
    displayed = set(seasons)
    if type(display_season) is int and 0 <= display_season <= 999:
        displayed.add(display_season)
    parts = ["特别篇" if season == 0 else f"第 {season} 季" for season in sorted(displayed)[:4]]
    if len(displayed) > 4:
        parts.append(f"等 {len(displayed)} 个季号")
    if type(total) is int and 1 <= total <= 999:
        parts.append(f"整剧共 {total} 季")
    if type(episodes) is int and 1 <= episodes <= 10000:
        parts.append(f"{episodes} 集")
    # Display enrichment does not supply the target season for execution.
    if len(displayed) > 1:
        note = "有多个季号线索，待核对"
    elif candidate.get("season_unknown") is True or not seasons:
        note = "部分来源未标季号，待核对" if seasons else "来源未标季号，待核对"
    elif seasons[0] == 0:
        note = "特别篇 · 收录范围待核对"
    else:
        note = "收录范围待核对" if candidate.get("state") == "HOLD" else ""
    return " · ".join(parts), note


def _matched_groups(candidate):
    dimensions = _dict(candidate.get("contributions")).get("dimensions")
    groups = {}
    for entry in dimensions[:6] if isinstance(dimensions, list) else []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("dimension")
        matches = _labels(entry.get("matched_labels"), limit=3, text_limit=48)
        if isinstance(name, str) and name in _DIMENSIONS and matches:
            groups[name] = matches
    return groups


def _badge(label, accent=False):
    color = "primary" if accent else "on-surface"
    return _node("span", label, props={"style": "max-width:100%;padding:3px 9px;border-radius:8px;"
                 f"background:rgba(var(--v-theme-{color}),.08);overflow-wrap:anywhere;font-size:.82rem"})


def _score(candidate, key):
    value = candidate.get(key)
    if type(value) not in (int, float) or not 0 <= value <= 1:
        return None
    return f"{value * 100:.1f}".removesuffix(".0")


def _score_row(candidate, key, label):
    score = _score(candidate, key)
    if score is None:
        return None
    return _node("div", props={"class": "va-score", "style": "display:grid;grid-template-columns:auto minmax(32px,1fr) 4.5ch;"
                    "align-items:center;gap:12px;font-size:.84rem"}, content=[
        _node("span", label), _node("div", props={"aria-hidden": "true", "style": "height:6px;border-radius:8px;"
                    "overflow:hidden;background:rgba(var(--v-theme-on-surface),.09)"}, content=[
            _node("div", props={"style": f"height:100%;width:{score}%;border-radius:inherit;background:rgb(var(--v-theme-primary))"})]),
        _node("b", score, props={"style": "text-align:end;white-space:nowrap;font-variant-numeric:tabular-nums"})])


def _reason_panel(candidate, title, personalized, season_note):
    groups = _matched_groups(candidate) if personalized else {}
    parts = []
    def detail(label, children):
        return _node("section", props={"style": "display:grid;gap:8px"}, content=[
            _node("h3", label, props={"class": "text-subtitle-2 font-weight-bold"}), *children])
    if groups:
        parts.append(detail("与你相合", [
            _node("div", props={"style": "display:grid;grid-template-columns:3em minmax(0,1fr);gap:6px;align-items:baseline"},
                  content=[_node("span", _DIMENSIONS[key], props={"class": "text-caption text-medium-emphasis"}),
                           _node("div", props={"class": "d-flex flex-wrap ga-1"}, content=[_badge(v, True) for v in labels])])
            for key, labels in groups.items()]))
    missing = candidate.get("missing")
    missing = {v for v in missing[:30] if isinstance(v, str)} if isinstance(missing, list) else set()
    no_evidence = bool(missing & {"baseline_unavailable", "candidate_metadata_missing", "profile_support_missing"})
    if personalized and not no_evidence:
        bars = [bar for key, label in (("taste_match", "口味匹配"), ("confidence", "证据强度"), ("fusion_rank", "综合排序"))
                if (bar := _score_row(candidate, key, label)) is not None]
        if bars:
            parts.append(detail("排序信号", [*bars, _caption("满分 100，仅用于排序，不是喜欢概率。", "")]))
    sources = _ranked_sources(candidate)
    if sources:
        parts.append(detail("发现来源", [_node("div", props={"class": "d-flex flex-wrap ga-1"},
                                         content=[_badge(label) for label in sources])]))
    notes = [season_note] if season_note else []
    if candidate.get("intent_kind") == "explicit_want":
        notes.append("已标记想看，优先展示")
    if "metadata_conflict" in missing:
        notes.append("不同来源的作品资料有出入")
    if personalized and no_evidence:
        notes.append("暂无足够的个人偏好依据")
    if notes:
        parts.append(_caption("；".join(notes), ""))
    if not parts:
        parts.append(_caption("暂无可展示的推荐依据", ""))
    summary = []
    accessible = []
    match = _score(candidate, "taste_match") if personalized and groups and not no_evidence else None
    if match is not None:
        accessible.append(f"口味匹配 {match}/100")
        summary.append(_node("span", props={"class": "va-match", "title": accessible[-1]},
            content=[_node("span", props={"class": "va-match-track", "aria-hidden": "true"}, content=[
                _node("span", props={"style": f"width:{match}%"})]), _node("span", f"匹配 {match}")]))
    ratings = candidate.get("ratings")
    for rating in ratings if isinstance(ratings, list) else []:
        if not isinstance(rating, dict) or rating.get("provider") != "themoviedb" or rating.get("scale") != 10:
            continue
        value = rating.get("value")
        if type(value) in (int, float) and 0 <= value <= 10 and rating.get("count") != 0:
            accessible.append(f"TMDB 评分 {value:.1f}/10")
            summary.append(_node("span", props={"class": "va-rating", "title": accessible[-1]},
                content=[_node("span", "★", props={"aria-hidden": "true"}), _node("span", f"{value:.1f}")]))
            break
    accessible.append(f"查看{title}的推荐依据")
    return _node("details", props={"class": "mt-1", "data-va-reason": title}, content=[
        _node("summary", None if summary else "推荐依据", content=summary,
              props={"class": "text-caption text-medium-emphasis cursor-pointer", "aria-label": "，".join(accessible)}),
        _node("div", props={"style": "display:grid;gap:16px;padding-block:8px;min-width:0"}, content=parts)])


def _poster_src(encoded):
    if not isinstance(encoded, str) or not 0 < len(encoded) <= (_MAX_POSTER_BYTES + 2) // 3 * 4:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if (len(data) > _MAX_POSTER_BYTES or not data.startswith(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00")
            or not data.endswith(b"\xff\xd9")):
        return None
    # The thumbnailer emits baseline RGB JPEGs. Inspect the bounded header without decoding pixels.
    pos, frame = 2, None
    while pos + 4 <= len(data) - 2 and data[pos] == 0xFF:
        marker = data[pos + 1]
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        end = pos + 2 + length
        if marker not in (0xE0, 0xDB, 0xC4, 0xC0, 0xDA) or length < 2 or end > len(data) - 2:
            return None
        if marker == 0xC0:
            if frame or length != 17:
                return None
            depth, height, width, channels = struct.unpack(">BHHB", data[pos + 4:pos + 10])
            if depth != 8 or channels != 3 or not 2 <= width <= 360 or height * 2 != width * 3:
                return None
            frame = (width, height)
        if marker == 0xDA:
            if frame and length == 12 and end < len(data) - 2:
                return ("data:image/jpeg;base64," + encoded, *frame)
            return None
        pos = end
    return None


def _poster(candidate, index):
    poster = _poster_src(candidate.get("poster_jpeg"))
    if poster:
        src, width, height = poster
        visual = _node("img", props={"src": src, "alt": "", "width": width, "height": height,
                                     "loading": "lazy", "decoding": "async",
                                     "style": "display:block;width:100%;height:100%;object-fit:scale-down"})
    else:
        visual = _node("span", "剧" if candidate.get("media_type") == "tv" else "影",
                       props={"class": "text-h4 font-weight-light", "style": "color:rgba(var(--v-theme-primary),.52)"})
    return _node("div", props={"aria-hidden": "true", "class": "d-flex align-center justify-center rounded-lg",
                 "style": "position:relative;overflow:hidden;width:100%;aspect-ratio:2/3;"
                          "background:linear-gradient(145deg,rgba(var(--v-theme-primary),.18),rgba(var(--v-theme-primary),.05))"},
                 content=[visual, _node("span", f"{index:02d}", props={"style": "position:absolute;top:7px;left:7px;"
                                       "padding:1px 6px;border-radius:4px;background:rgba(18,19,28,.8);color:white;"
                                       "font-size:11px;line-height:18px;font-weight:650;font-variant-numeric:tabular-nums"})])


def _candidate_card(candidate, index, personalized):
    media_type = candidate.get("media_type")
    kind = "电影" if media_type == "movie" else "剧集" if media_type == "tv" else "类型未知"
    title = _text(candidate.get("title"), "片名未知", 180)
    year = candidate.get("year")
    year_label = str(year) if (type(year) is int and 1800 <= year <= 9999 or
                               isinstance(year, str) and len(year) == 4 and year.isascii() and year.isdigit()) else "年份未知"
    metadata = [kind, year_label]
    season_note = "收录范围待核对" if candidate.get("state") == "HOLD" else ""
    if media_type == "tv":
        season, season_note = _season(candidate)
        if season:
            metadata.append(season)
    identity = re.fullmatch(r"(themoviedb|douban):(movie|tv):([1-9][0-9]{0,18})", str(candidate.get("entity_key") or ""))
    cover_title = [_poster(candidate, index),
                   _node("div", title, props={"class": "text-subtitle-2 font-weight-bold mt-2",
                          "style": "overflow-wrap:anywhere;line-height:1.45"})]
    if identity and identity[2] == media_type:
        destination = (f"https://www.themoviedb.org/{media_type}/{identity[3]}" if identity[1] == "themoviedb"
                       else f"https://movie.douban.com/subject/{identity[3]}/")
        cover_title = [_node("a", props={"href": destination,"rel": "noopener noreferrer","target": "_blank",
                             "title":"查看作品资料","style":"display:block;text-decoration:none;color:inherit"},
                             content=cover_title)]
    body = [*cover_title, _caption(" · ".join(metadata), "mt-1"),
            _reason_panel(candidate, title, personalized, season_note)]
    return _node("div", props={"class": "va-candidate", "role": "listitem", "style": "min-width:0"},
                 content=body)


def render_candidates(status, plugin_id="PtSubtitleFollowup"):
    """Render a bounded read-only window, never an inferred subscription action."""
    status = _dict(status)
    state = status.get("status")
    label, color = _STATES.get(state, ("等待配置", "secondary")) if isinstance(state, str) else ("等待配置", "secondary")
    enabled = status.get("enabled") is True
    personalized = status.get("profile_available") is True
    sources = _source_rows(status)
    raw_candidates = status.get("candidates")
    candidates = [candidate for candidate in raw_candidates[:50] if isinstance(candidate, dict)] if isinstance(raw_candidates, list) else []
    requested_limit = status.get("display_limit", 20)
    limit = max(1, min(50, requested_limit)) if type(requested_limit) is int else 20
    candidates = candidates[:limit]
    count = int(_number(status.get("candidate_count"), len(candidates)))
    media_counts = _dict(status.get("media_counts"))
    # Older snapshots may contain only a small preview. A partial list cannot supply pool totals.
    if not media_counts and isinstance(raw_candidates, list) and len(raw_candidates) == count and count <= 50:
        media_counts = {kind: sum(isinstance(row, dict) and row.get("media_type") == kind for row in raw_candidates)
                        for kind in ("movie", "tv")}
    busy = isinstance(state, str) and state in {"running", "queued", "paused"}
    available = any(_number(source.get("cooldown_remaining")) <= 0 for source in sources)
    filtered_sources = [source for source in sources if _window_kind(source) == "filtered_pages"]
    mode_text = "通用候选 · 暂无可用画像" if filtered_sources else "通用榜单 · 暂无可用画像"
    metrics = []
    for title, value in (("候选总数", count), ("电影", _count(media_counts.get("movie"))),
                         ("剧集", _count(media_counts.get("tv"))), ("候选来源", len(sources))):
        primary = title == "候选总数"
        tone = "rgba(var(--v-theme-primary),.1)" if primary else "rgba(var(--v-theme-on-surface),.045)"
        metrics.append(_node("div", props={"class": "rounded-lg",
            "style": f"min-width:0;padding:10px clamp(4px,1.4vw,14px);background:{tone}"},
            content=[_caption(title, ""), _node("div", str(value) if value is not None else "—", props={
                "class": "text-h5 font-weight-bold" + (" text-primary" if primary else ""),
                "style": "font-variant-numeric:tabular-nums"})]))
    overview = [_node("div", props={"class": "va-metrics", "style": "display:grid;gap:clamp(4px,1vw,8px);"
                                   "grid-template-columns:repeat(4,minmax(0,1fr))"}, content=metrics)]
    if not personalized and enabled and sources:
        overview.insert(0, _caption(mode_text, "mb-2"))
    if state not in ("idle", "ready", "success"):
        overview.insert(0, _node("div", content=[_chip(label, color)]))
    if enabled and not sources:
        overview.append(_caption("尚未选择来源", "mt-2"))
    attention = [source for source in sources if source.get("authentication_blocked") is True
                 or source.get("stale") is True or _number(source.get("cooldown_remaining")) > 0
                 or source.get("status") in ("error", "unavailable") or bool(source.get("error_code"))]
    normal = [source for source in sources if source not in attention]
    if attention:
        overview.append(_source_grid(attention))
    timing = ["北京时间"]
    if personalized:
        timing.append(f"画像更新 {_when(status.get('profile_as_of'), '未知')}")
    context = [_node("div", props={"class": "d-flex flex-wrap align-center justify-space-between ga-2"}, content=[
        _caption(" · ".join(timing), ""),
        _button("刷新候选状态", plugin_id, "candidates/status", "GET"),
    ])]
    if normal:
        context.append(_source_grid(normal))
    notes = [_PREVIEW_NOTE, "仅显示本轮来源窗口，尚未检查媒体库和已有订阅。"]
    if any(str(source.get("id", "")).startswith("tmdb_personal_") for source in sources):
        notes.append("个人片源按已绑定账号的偏好题材查找电影和剧集。")
    if any(str(source.get("id", "")).startswith("tmdb_popular_") for source in sources):
        notes.append("TMDB 为热门榜的部分分页窗口。")
    if any(_window_kind(source) == "snapshot" for source in sources):
        notes.append("待播源来自第三方 RSSHub，不翻页、不推测整榜数量。")
    if filtered_sources:
        notes.append("关注分享在公共页内匹配，不保证覆盖全部关注历史。")
        if any(_count(_dict(source.get("observed_window")).get("matched")) == 0 for source in filtered_sources):
            notes.append("本页未匹配到关注分享，不代表关注历史为空。")
    if personalized:
        notes.append("匹配分用于相对排序，不是喜欢概率。")
    if not candidates:
        notes.append("当前为空不代表来源已被完整遍历。")
    context.append(_node("details", props={"class": "mt-2"}, content=[
        _node("summary", "数据口径", props={"class": "text-caption text-medium-emphasis cursor-pointer"}),
        _node("ul", props={"class": "ps-4 mt-1 text-caption text-medium-emphasis"}, content=[
            _node("li", text) for text in notes]),
    ]))
    overview.append(_node("details", props={"class": "mt-3"}, content=[
        _node("summary", "来源与排序", props={"class": "text-caption text-primary cursor-pointer"}),
        _node("div", props={"class": "pt-3"}, content=context),
    ]))
    card = _card("候选发现", overview)
    heading = card["content"][0]
    heading["props"]["class"] += " d-flex flex-wrap justify-space-between align-center ga-2 pb-3"
    update = _button("更新候选", plugin_id, "candidates/refresh", "POST", primary=True,
                     disabled=not enabled or busy or not available)
    update["props"]["data-va-primary"] = "refresh-candidates"
    heading["content"] = [update]
    page = [card]
    if candidates:
        shelf = _card(f"候选片单 · 前 {len(candidates)} 项", [
            _node("div", props={"class": "va-poster-grid", "role": "list",
                  "aria-label": "按排名排列的候选影视",
                  "style": "display:grid;align-items:start;gap:20px 14px;"
                           "grid-template-columns:repeat(auto-fill,minmax(min(42%,clamp(132px,13vw,172px)),1fr))"},
                  content=[_candidate_card(candidate, index, personalized)
                           for index, candidate in enumerate(candidates, 1)])])
        shelf["props"].update({"data-va-shelf": True, "data-total": count, "data-size": limit})
        page.append(shelf)
    else:
        empty = [_caption("等待来源更新", "")] if enabled and sources else []
        if (not enabled or not sources) and plugin_id == "PtSubtitleFollowup":
            empty.append(_node("VBtn", "选择候选来源", props={"data-va-settings": True, "variant": "tonal", "color": "primary", "class": "mt-3"}))
        page.append(_card("暂无候选", empty))
    return page


def browse_candidates(status, decorate, kind="all", period="all", page=1, *, size=None, now=None):
    """Filter the entire current pool first. A weekly discovery is its first local observation."""
    if (kind not in ("all", "movie", "tv") or period not in ("all", "week") or type(page) is not int or page < 1
            or (size is not None and (type(size) is not int or not 1 <= size <= 50))):
        return {"success": False}
    clock = (now or datetime.now(timezone.utc)).timestamp()
    def recently_seen(row):
        try:
            first = datetime.fromisoformat(row["first_seen"])
            return first.utcoffset() is not None and 0 <= clock - first.timestamp() < 7 * 86400
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return False
    rows = [row for row in status.get("candidates", []) if isinstance(row, dict)
            and (kind == "all" or row.get("media_type") == kind) and (period == "all" or recently_seen(row))]
    if size is None:
        limit = status.get("display_limit", 20)
        size = max(1, min(50, limit)) if type(limit) is int else 20
    pages = max(1, (len(rows) + size - 1) // size)
    actual = min(page, pages)
    start = (actual - 1) * size
    chunk = decorate({**status, "candidates": rows[start:start + size], "display_limit": size})
    return {"success": True, "page": actual, "pages": pages, "total": len(rows),
            "cards": [_candidate_card(row, start + n, status.get("profile_available") is True)
                      for n, row in enumerate(chunk["candidates"], 1)]}
