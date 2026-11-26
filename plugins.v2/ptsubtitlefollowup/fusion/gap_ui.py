"""MoviePilot form and dashboard for episode gap repair."""
import itertools
from datetime import datetime, timedelta, timezone

from .ui import _caption, _card, _field, _node


def gap_form():
    return _card("剧集补缺", [
        _node("VRow", content=[
            _field("VSwitch", "gap_enabled", "自动补齐追更中的缺集", cols=12, color="primary"),
        ]),
        _caption("适用于追更中的剧集订阅。发现缺集先找原组，等满 3 小时可借同规格备组，原组发布后逐集换回。"),
    ])


def _episodes(numbers):
    # Keep a long contiguous season readable while preserving actual holes.
    ordered = sorted({n for n in numbers if type(n) is int and n > 0})
    ranges = []
    for _, entries in itertools.groupby(enumerate(ordered), lambda item: item[1] - item[0]):
        block = [entry[1] for entry in entries]
        ranges.append(str(block[0]) if len(block) == 1 else f"{block[0]}–{block[-1]}")
    return "、".join(ranges)


_STATES = {
    "waiting": "等待原组", "downloading": "下载或整理中", "submitted": "已提交原组补集",
    "uncertain": "请核实下载与整理记录", "searching_backup": "寻找原组或同规格备组",
    "borrowing": "备组补集已提交", "borrowed": "等待原组发布", "restoring": "等待原组入库",
    "restored": "已核实换回原组", "blocked": "等待核实", "retry": "等待下次检查",
    "review": "请核实下载与整理结果",
}


def gap_panel(status):
    rows = []
    for entry in status.get("seasons", []):
        state = entry.get("state")
        title = str(entry.get("title") or "未命名订阅")
        if type(entry.get("season")) is int:
            title += f" · 第 {entry['season']} 季"
        details = [_node("strong", title), _caption(_STATES.get(state, "等待核实"))]
        missing = _episodes(entry.get("missing", []))
        if missing:
            details.append(_caption(f"缺第 {missing} 集"))
        group = entry.get("group")
        if group:
            details.append(_caption(f"首选组 {group}"))
        borrowed = entry.get("borrowed_groups") or {}
        for name in sorted(set(borrowed.values())):
            numbers = [int(ep) for ep in borrowed if borrowed[ep] == name]
            details.append(_caption(f"暂借 {name} · 第 {_episodes(numbers)} 集"))
        restored = _episodes(entry.get("restored", []))
        if restored:
            details.append(_caption(f"已换回 · 第 {restored} 集"))
        review = _episodes(entry.get("review_episodes", []))
        if review:
            details.append(_caption(f"待核实 · 第 {review} 集"))
        if state == "waiting" and entry.get("deadline"):
            until = datetime.fromtimestamp(entry["deadline"], timezone(timedelta(hours=8)))
            details.append(_caption(f"{until:%m-%d %H:%M} 起检查备组（北京时间）"))
        rows.append(_node("li", props={"class": "va-pick"}, content=[_node("div", content=details)]))
    content = [_node("ul", props={"class": "va-picks"}, content=rows)] if rows else []
    if status.get("status") == "error":
        content.append(_caption("本轮检查未完成，下轮会重试。"))
    elif not rows:
        content.append(_caption("尚无已登记的缺集。"))
    return _card("剧集补缺", content)
