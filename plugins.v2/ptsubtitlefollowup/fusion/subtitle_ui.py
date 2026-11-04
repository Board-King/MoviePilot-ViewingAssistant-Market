"""Project subtitle results for the board without including local paths or release URLs."""
from .ui import _caption, _card, _dict, _node, _number, _stamp, _text


LABELS = {"saved": "已补齐", "existing": "已有字幕", "waiting_transfer": "等入库",
          "waiting_subtitle": "等字幕", "retry": "待重试", "expired": "已到期", "unknown": "待核对"}


def _task_record(task):
    state = task.get("status")
    state = state if isinstance(state, str) and state in LABELS else "unknown"
    title = _text(task.get("title"), "未命名作品")
    marks = []
    for target in _dict(task.get("targets")).values():
        if not isinstance(target, dict):
            continue
        result = target.get("status")
        if result not in ("saved", "existing"):
            result = state if state not in ("saved", "existing") else "unknown"
        season, episode = target.get("season"), target.get("episode")
        suffix = f"S{season:02}E{episode:02}" if type(season) is int and type(episode) is int else ""
        name = _text(target.get("title"))
        # Common single-file records inherit the task title and note.
        marks.append([result, " · ".join(filter(None, [name if name != title else "", suffix])),
                      _text(target.get("message"), limit=240), _text(target.get("refresh"), limit=240)])
    if not marks:
        # Older task-level terminal records may lack file details. One marker represents the recorded result.
        marks.append([state, "", "", ""])
    attempts = task.get("attempts")
    return {"title": title, "checked": _stamp(task.get("updated")), "attempts": attempts if type(attempts) is int else 0,
            "note": _text(task.get("message"), limit=360), "marks": marks}


def render_subtitles(tasks, enabled, *, rich):
    recent = sorted((t for t in tasks if isinstance(t, dict)),
                    key=lambda t: _number(t.get("updated") or t.get("created")), reverse=True)
    records = [_task_record(t) for t in recent]
    if rich:
        body = [_node("div", subtitle_records=records, subtitle_enabled=bool(enabled))]
    else:
        # MoviePilot clones use its native renderer instead of the shared Vue component.
        body = []
        for record in records:
            marks = record["marks"]
            body.append(_node("details", props={"class": "mb-2"}, content=[
                _node("summary", record["title"], props={"class": "text-body-2 cursor-pointer"}),
                *[_caption(" · ".join(filter(None, [LABELS[state], name, message, refresh])), "mt-1")
                  for state, name, message, refresh in marks],
                _caption(record["note"], "mt-1"),
            ]))
        if not body:
            body = [_caption("暂无补查记录" if enabled else "未启用", "")]
    return _card("配套字幕", body)
