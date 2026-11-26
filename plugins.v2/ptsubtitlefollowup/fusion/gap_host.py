"""MoviePilot-backed evidence for same-season episode repair (SQLite V2/V3)."""
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace


_VIDEO = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".strm", ".wmv", ".m4v", ".mpg", ".mpeg"}
_SIDECAR = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".nfo", ".txt", ".jpg", ".jpeg", ".png", ".sfv"}
_SCOPE = ("name", "year", "username", "season", "include", "exclude", "quality",
          "resolution", "effect", "sites", "filter_groups", "filter", "save_path", "downloader",
          "custom_words", "keyword", "search_imdbid", "episode_group", "media_category",
          "media_category_id", "start_episode", "best_version", "best_version_full")
_MAX_RECEIPTS = 4096


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def release(meta):
    """Use MP's parsed group, resolution, source and video characteristics on both sides."""
    group = str(getattr(meta, "resource_team", "") or "").strip()
    pix = str(getattr(meta, "resource_pix", "") or "").strip()
    source = str(getattr(meta, "resource_type", "") or "").strip()
    codec = str(getattr(meta, "video_encode", "") or "").strip()
    if not group or not pix or not source or not codec or not re.fullmatch(r"[\w.-]{1,64}", group):
        raise ValueError("无法确认发布组或规格")
    values = [pix, source, codec, getattr(meta, "resource_effect", None), getattr(meta, "video_bit", None),
              getattr(meta, "audio_encode", None), getattr(meta, "fps", None), getattr(meta, "web_source", None)]
    # Preserve unmarked versus explicit HDR/DV/bit depth rather than assuming equivalents.
    return group.casefold(), " / ".join(str(v or "-").strip().casefold() for v in values)


def _episodes(text):
    """MP stores discrete E01E03 and continuous E01-E03 scopes separately."""
    if not isinstance(text, str) or not re.fullmatch(r"E\d{1,4}(?:(?:-?E)\d{1,4})*", text, re.I):
        return set()
    numbers = list(re.finditer(r"(-?)E(\d{1,4})", text.upper()))
    result, previous = set(), 0
    for match in numbers:
        number = int(match[2])
        if not 1 <= number <= 1000 or (match[1] and number < previous):
            return set()
        result.update(range(previous + 1, number + 1) if match[1] else [number])
        previous = number
    return result


def _media_id(row):
    source = getattr(row, "media_source", None)
    value = getattr(row, "media_id", None)
    if source in (None, "") and value is None:
        value = getattr(row, "tmdbid", None)
    elif source != "themoviedb":
        return None
    return str(value) if re.fullmatch(r"[1-9]\d{0,15}", str(value)) else None


def _names(meta):
    return {re.sub(r"[\W_]+", "", str(value)).casefold() for value in
            (getattr(meta, "name", None), getattr(meta, "cn_name", None),
             getattr(meta, "en_name", None)) if value}


class GapHost:
    def __init__(self):
        from version import APP_VERSION
        from ..host_compat import get_engine
        from app.chain.subscribe import SubscribeChain
        from app.chain.download import DownloadChain
        if APP_VERSION not in {"v2.15.6", "v3.2.9"}:
            raise ValueError("当前宿主版本未验证")
        self.v3 = APP_VERSION == "v3.2.9"
        if self.v3:
            from app.domain.metainfo import MetaInfo
        else:
            from app.core.metainfo import MetaInfo
        self.meta = MetaInfo
        self.engine = get_engine()
        if self.engine.dialect.name != "sqlite":
            raise ValueError("当前数据库未验证")
        self.chain = SubscribeChain()
        self.downloads = DownloadChain()
        self._contexts = {}
        self._subs = {}
        self._receipt_cache = {}

    def _read(self, sql, args=()):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.exec_driver_sql(sql, args).mappings()]

    def _subs_for_cycle(self, resume):
        from sqlalchemy import func, select
        from app.db.models.subscribe import Subscribe
        from app.db.models.subscribehistory import SubscribeHistory
        active_table, archive_table = Subscribe.__table__, SubscribeHistory.__table__
        with self.engine.connect() as conn:
            highwater = 0 if self.v3 else conn.execute(select(func.max(archive_table.c.id))).scalar() or 0
            rows = conn.execute(select(active_table).where(active_table.c.type == "电视剧")).mappings().all()
            active = [SimpleNamespace(**row) for row in rows]
            if not self.v3:
                for sub in active:
                    sub.gap_activity = f"{highwater}:{sub.date or ''}"
            # Transfer receipts identify a series/season, not an individual subscription.
            scopes = Counter((_media_id(sub), sub.season) for sub in active)
            result = [(sub, False) for sub in active
                      if sub.state in ("R", "N") and scopes[_media_id(sub), sub.season] == 1]
            for ref in resume:
                if not isinstance(ref, dict) or not ref.get("borrowed_intent") or not str(ref.get("media_id", "")).isdigit():
                    continue
                if any(_media_id(s) == ref["media_id"] and s.season == ref.get("season") for s in active):
                    continue
                query = select(archive_table).where(archive_table.c.type == "电视剧",
                         archive_table.c.media_source == "themoviedb", archive_table.c.media_id == ref["media_id"],
                         archive_table.c.season == ref.get("season"))
                if self.v3:
                    if not ref.get("activity"):
                        continue
                    query = query.where(archive_table.c.activity_key == ref["activity"])
                else:
                    # V2 records the creation time on the live subscription and its
                    # completion time in history. A previous run must never revive a deletion.
                    match = re.fullmatch(r"(\d{1,19}):(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)?", ref.get("activity") or "")
                    if not match:
                        continue
                    query = query.where(archive_table.c.id > int(match[1]))
                    if match[2]:
                        query = query.where(archive_table.c.date >= match[2])
                candidates = conn.execute(query.limit(2)).mappings().all()
                if len(candidates) != 1 or type(ref.get("sid")) is not int:
                    continue
                attrs = dict(candidates[0])
                attrs.update(id=ref["sid"], downloader=ref.get("downloader"), state="archived")
                if "category" in ref and "category_id" in ref:
                    attrs.update(media_category=ref["category"], media_category_id=ref["category_id"])
                if not self.v3:
                    attrs.update(date=match[2], gap_activity=ref["activity"])
                sub = SimpleNamespace(**attrs)
                if self._scope(sub) == ref.get("key"):
                    result.append((sub, True))
        return result

    @staticmethod
    def _scope(sub):
        return _digest([sub.id, getattr(sub, "activity_key", None) or getattr(sub, "date", None), _media_id(sub),
                        [getattr(sub, name, None) for name in _SCOPE]])

    def _identity_sql(self):
        query = "(media_source='themoviedb' AND media_id=?)"
        if not self.v3:
            query = "(" + query + " OR (media_source IS NULL AND media_id IS NULL AND tmdbid=?))"
        return query

    def _stored_names(self, storage, directory):
        if storage == "local":
            result = {}
            for entry in directory.iterdir():
                if entry.suffix.lower() in _VIDEO and entry.is_file():
                    st = entry.stat()
                    result[entry.name] = {"size": st.st_size, "modify_time": st.st_mtime, "modify_time_ns": st.st_mtime_ns}
            return result
        from app.chain.storage import StorageChain
        chain = StorageChain()
        folder = chain.get_file_item(storage, directory)
        if not folder:
            raise ValueError("无法核实目标目录")
        contents = chain.list_files(folder, recursion=False)
        if not isinstance(contents, list):
            raise ValueError("无法核实云端文件")
        return {str(item.name): {k: getattr(item, k, None) for k in ("size", "modify_time", "fileid")}
                for item in contents if item.type == "file"}

    def _current_quality(self, row, dest, current, quality):
        """Verify the transfer receipt against today's object, even for a renamed target."""
        try:
            recorded = row.get("dest_fileitem") or {}
            if isinstance(recorded, str):
                recorded = json.loads(recorded)
            if not isinstance(recorded, dict):
                return (None, None)
            checks = []
            for key in ("size", "fileid", "modify_time_ns", "modify_time"):
                expected, actual = recorded.get(key), current.get(key)
                if expected is not None and actual is not None:
                    same = (abs(float(expected) - float(actual)) < 1.0 if key == "modify_time"
                            else str(expected) == str(actual))
                    if not same:
                        return (None, None)
                    checks.append(key)
            meta = self.meta(dest.name)
            dest_group = getattr(meta, "resource_team", None)
            if dest_group and dest_group.casefold() != quality[0]:
                return (None, None)
            if ("fileid" in checks or "size" in checks and any(k in checks for k in ("modify_time", "modify_time_ns"))):
                return quality
            return (None, None)
        except (TypeError, ValueError):
            return (None, None)

    def _inventory(self, rows, sub, anchor=None):
        # A successful transfer receipt alone is not proof that a file still exists.
        names, slots, written = {}, {}, {}
        original = anchor
        aliases = _names(sub)
        for row in rows:
            eps = _episodes(row["episodes"])
            if not eps or not row["dest"] or not row["src"]:
                raise ValueError("整理记录缺少集号或文件信息")
            dest, storage = Path(row["dest"]), row["dest_storage"]
            if not dest.is_absolute() or not storage:
                raise ValueError("无法核实整理位置")
            if dest.suffix.lower() not in _VIDEO:
                continue
            src_meta = self.meta(Path(row["src"]).name)
            aliases.update(_names(src_meta))
            aliases.update(_names(self.meta(dest.name)))
            try:
                quality = release(src_meta)
            except ValueError:
                quality = (None, None)
            if original is None:
                original = quality
            location = (storage, dest.parent)
            if location not in names:
                names[location] = self._stored_names(*location)
            written.setdefault((row["downloader"], row["download_hash"]), set()).update(eps)
            if dest.name in names[location]:
                current_quality = self._current_quality(row, dest, names[location][dest.name], quality)
                for ep in eps:
                    versions = slots.setdefault(ep, {}).setdefault((storage, str(dest)), [])
                    if all(current_quality):
                        versions.append((current_quality, row["download_hash"]))
        if original is None or not all(original):
            raise ValueError("不能确定最初入库版本")
        # Newly added files outside transfer history must not be downloaded again.
        # Restrict this conservative check to season-labelled media in these same directories.
        for (storage, directory), entries in names.items():
            for name in entries:
                if Path(name).suffix.lower() not in _VIDEO:
                    continue
                meta = self.meta(name)
                if meta.begin_season == sub.season and aliases.intersection(_names(meta)):
                    for ep in meta.episode_list or []:
                        slots.setdefault(ep, {}).setdefault((storage, str(directory / name)), [])
        groups, tasks = {}, {}
        for ep, versions in slots.items():
            receipts = [item for matches in versions.values() for item in matches or [((None, None), None)]]
            qualities = {q for q, _ in receipts}
            only = next(iter(qualities))
            groups[ep] = only[0] if len(qualities) == 1 and only[1] == original[1] else None
            hashes = {task for _, task in receipts}
            tasks[ep] = next(iter(hashes)) if len(hashes) == 1 and groups[ep] else None
        return original, groups, tasks, written, aliases

    def _active(self, sub, args, written, aliases):
        # Cover current downloads AND completed tasks awaiting transfer. Removed files
        # are omitted. History without enough detail blocks the affected season.
        query = ("SELECT downloader,download_hash,episodes,seasons,torrent_name FROM downloadhistory "
                 "WHERE type='电视剧' AND " + self._identity_sql() + " ORDER BY id DESC LIMIT 4097")
        rows = self._read(query, args)
        if len(rows) > _MAX_RECEIPTS:
            raise ValueError("下载历史超出可核实范围")
        active = set()
        file_rows = self._read(
            "SELECT f.downloader,f.download_hash,f.filepath,f.state FROM downloadfiles f "
            "WHERE f.download_hash IN (SELECT download_hash FROM downloadhistory WHERE type='电视剧' AND " +
            self._identity_sql() + ") LIMIT 4097", args)
        if len(file_rows) > _MAX_RECEIPTS:
            raise ValueError("下载文件范围无法确认")
        by_job = {}
        for file in file_rows:
            by_job.setdefault((file["downloader"], file["download_hash"]), []).append(file)
        # Also check downloader tasks submitted outside MP or too recently for history.
        from app.schemas.types import TorrentStatus
        tasks = self.downloads.list_torrents(status=TorrentStatus.DOWNLOADING, include_all_tags=True)
        if not isinstance(tasks, list):
            raise ValueError("下载器状态无法核实")
        known = set()
        for row in rows:
            key = (row["downloader"], row["download_hash"])
            if not row["download_hash"]:
                continue
            known.add(key)
            if row["seasons"] and row["seasons"].upper() != f"S{sub.season:02}":
                # Multi-season records need per-file verification.
                if re.fullmatch(r"S\d+", row["seasons"], re.I):
                    continue
            receipt_eps = _episodes(row["episodes"])
            files = by_job.get(key, [])
            if files:
                selected = set()
                for file in files:
                    if file["state"] == 0 or not file["filepath"] or Path(file["filepath"]).suffix.lower() not in _VIDEO:
                        continue
                    meta = self.meta(Path(file["filepath"]).name)
                    if meta.begin_season not in (None, sub.season):
                        continue
                    selected.update(meta.episode_list or receipt_eps)
                    if not meta.episode_list and not receipt_eps:
                        raise ValueError("在途资源的集数不明")
                if receipt_eps:
                    selected &= receipt_eps
            else:
                selected = receipt_eps
                if not selected:
                    raise ValueError("在途资源的集数不明")
            active.update(selected - written.get(key, set()))
        for task in tasks:
            raw = getattr(task, "title", None) or getattr(task, "name", None)
            if not raw:
                continue
            meta = self.meta(raw)
            if not aliases.intersection(_names(meta)) or meta.begin_season not in (None, sub.season):
                continue
            if (getattr(task, "downloader", None), getattr(task, "hash", None)) in known:
                continue
            if not meta.episode_list:
                raise ValueError("有无法核对集号的同剧下载")
            active.update(meta.episode_list)
        return active

    def _snapshot(self, sub, archived, tracked=False, anchor=None):
        media_id = _media_id(sub)
        if (not media_id or not isinstance(sub.season, int) or sub.season <= 0 or sub.episode_group
                or sub.best_version or sub.best_version_full):
            return None
        args = (media_id,) if self.v3 else (media_id, int(media_id))
        query = ("SELECT src,dest,dest_storage,dest_fileitem,episodes,downloader,download_hash FROM transferhistory "
                 "WHERE status=1 AND type='电视剧' AND " + self._identity_sql() +
                 " AND seasons=? AND (episode_group IS NULL OR episode_group='') ORDER BY id LIMIT 4097")
        rows = self._read(query, (*args, f"S{sub.season:02}"))
        if not rows or len(rows) > _MAX_RECEIPTS:
            return None
        first = sub.start_episode or 1
        if not tracked:
            # Check SQLite first. A contiguous import history need not wake an array disk
            # or read a cloud directory just to discover that no episodes were skipped.
            received = set().union(*(_episodes(row["episodes"]) for row in rows))
            if not received or all(ep in received for ep in range(first, max(received))):
                return None
        original, groups, tasks, written, aliases = self._inventory(rows, sub, anchor)
        last = getattr(sub, "total_episode", None)
        groups = {ep: group for ep, group in groups.items() if ep >= first and (not last or ep <= last)}
        key = self._scope(sub)
        if not groups:
            return None
        has_gap = any(ep not in groups for ep in range(first, max(groups)))
        active = self._active(sub, args, written, aliases) if has_gap or any(g != original[0] for g in groups.values()) else set()
        self._subs[key] = sub
        return {"key":key, "title":sub.name, "season":sub.season, "first_episode":first,
                "subscription_id":sub.id, "media_id":media_id, "present":sorted(groups),
                "active":sorted(active), "episode_groups":groups, "group":original[0], "spec":original[1],
                "episode_tasks":{ep: tasks[ep] for ep in groups},
                "archived":archived, "resume":{"key":key, "sid":sub.id, "media_id":media_id,
                "season":sub.season, "activity":getattr(sub,"activity_key","") if self.v3 else sub.gap_activity,
                "downloader":sub.downloader, "category":getattr(sub,"media_category",None),
                "category_id":getattr(sub,"media_category_id",None)}}

    def snapshots(self, resume=()):
        self._subs.clear()
        self._contexts.clear()
        self._receipt_cache.clear()
        result = []
        refs = {ref.get("key"): ref for ref in resume if isinstance(ref, dict)}
        for sub, archived in self._subs_for_cycle(resume):
            try:
                ref = refs.get(self._scope(sub)) or {}
                anchor = (ref.get("original_group"), ref.get("original_spec"))
                snap = self._snapshot(sub, archived, bool(ref), anchor if all(anchor) else None)
            except (ValueError, OSError):
                continue
            if snap:
                result.append(snap)
        return result

    def search(self, snapshot, include_alternatives=False, allowed=lambda: True):
        from .gap_search import search_candidates
        sub = self._subs[snapshot["key"]]
        found = search_candidates(sub, snapshot, include_alternatives, allowed=allowed)
        for resource, context in found:
            self._contexts[(snapshot["key"], resource["token"])] = (dict(resource), context)
        return [resource for resource, _ in found]

    @contextmanager
    def _admit(self, sid):
        if self.v3:
            admission = self.chain._subscription_execution_admission
            lease = admission.try_acquire(subscription_id=sid, operation="episode-gap", ttl_seconds=300)
            try:
                yield lease is not None
            finally:
                if lease:
                    admission.release(lease)
        else:
            acquired = self.chain._rlock.acquire(blocking=False)
            try:
                yield acquired
            finally:
                if acquired:
                    self.chain._rlock.release()

    def _verified_content(self, context, resource, selected, season):
        """Confirm every video in the actual torrent belongs to this repair."""
        content, _, files = self.downloads.download_torrent(context.torrent_info)
        if not isinstance(content, bytes) or not content or not isinstance(files, list) or not 1 <= len(files) <= 4096:
            return None
        observed = set()
        for name in files:
            if not isinstance(name, str):
                return None
            path = Path(name.replace("\\", "/"))
            ext = path.suffix.lower()
            if ext in _SIDECAR:
                continue
            if ext not in _VIDEO or ext == ".strm":
                return None
            meta = self.meta(path.name)
            try:
                quality = release(meta)
            except ValueError:
                return None
            if (meta.season_list != [season] or not meta.episode_list
                    or not set(meta.episode_list) <= selected
                    or quality != (resource["group"], resource["spec"])
                    or not meta.name or meta.name.casefold() != (context.meta_info.name or "").casefold()):
                return None
            observed.update(meta.episode_list)
        return content if observed == selected else None

    def dispatch(self, snapshot, resource, episodes, mode, allowed):
        key = snapshot["key"]
        selected = set(episodes)
        pair = self._contexts.get((key, resource["token"]))
        if (not pair or not selected or pair[0] != resource or mode not in {"original","borrow","restore"}
                or resource["spec"] != snapshot["spec"] or not selected <= set(resource["episodes"])
                or (selected != set(resource["episodes"]) and resource.get("selectable") is not True)
                or ((mode == "borrow") == (resource["group"] == snapshot["group"]))):
            return "not_sent"
        if not allowed():
            return "not_sent"
        try:
            content = self._verified_content(pair[1], resource, selected, snapshot["season"])
        except Exception:
            return "not_sent"
        if content is None:
            return "unverified_resource"
        with self._admit(snapshot["subscription_id"]) as admitted:
            if not admitted or not allowed():
                return "not_sent"
            # Repeat scope, inventory, and in-flight checks under MP's own subscription
            # execution lock. A pause, deletion, changed scope or newly active job wins.
            for sub, archived in self._subs_for_cycle([{**snapshot["resume"], "borrowed_intent": mode == "restore"}]):
                if self._scope(sub) != key or (archived and mode != "restore"):
                    continue
                current = self._snapshot(sub, archived, tracked=True, anchor=(snapshot["group"], snapshot["spec"]))
                if not current or current["spec"] != resource["spec"] or current["group"] != snapshot["group"]:
                    return "not_sent"
                if selected & set(current["active"]):
                    return "not_sent"
                if min(selected) < current["first_episode"] or max(selected) > max(current["present"]):
                    return "not_sent"
                if mode == "restore":
                    if any(not current["episode_groups"].get(ep) or current["episode_groups"][ep] == snapshot["group"]
                           or current["episode_groups"][ep] != snapshot["episode_groups"].get(ep)
                           or not current["episode_tasks"].get(ep)
                           or current["episode_tasks"][ep] != snapshot["episode_tasks"].get(ep) for ep in selected):
                        return "not_sent"
                elif selected & set(current["present"]):
                    return "not_sent"
                if not allowed():
                    return "not_sent"
                try:
                    receipt = self.downloads.download_single(context=pair[1], episodes=selected,
                        torrent_content=content,
                        username=sub.username, save_path=sub.save_path, downloader=sub.downloader,
                        source=self.chain.get_subscribe_source_keyword(sub) if not archived else "ViewingAssistant",
                        custom_words=sub.custom_words)
                except Exception:
                    return "unknown"
                if not isinstance(receipt, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", receipt):
                    return "unknown"
                owners = self._read("SELECT DISTINCT downloader FROM downloadhistory WHERE download_hash=? LIMIT 2",
                                    (receipt,))
                downloader = owners[0]["downloader"] if len(owners) == 1 else sub.downloader
                return {"outcome": "accepted", "task": receipt, "downloader": downloader}
        return "not_sent"

    def _task_absent(self, downloader, task):
        # Chain.list_torrents discards connection errors. The original module's
        # read returns an explicit error flag on both qBittorrent and Transmission.
        if self.v3:
            from app.application.module import ModuleManager
        else:
            from app.core.module import ModuleManager
        clients = [mod.get_instance(downloader) for mod in ModuleManager().get_running_modules("list_torrents")
                   if type(mod).__name__ in {"QbittorrentModule", "TransmissionModule"}]
        clients = [client for client in clients if client is not None]
        if len(clients) != 1:
            return False
        tasks, failed = clients[0].get_torrents(ids=[task])
        return failed is False and isinstance(tasks, list) and not tasks

    def receipt_status(self, snapshot, task, downloader, ep):
        """Only explicit deleted-file records plus an absent native task allow retry."""
        if not isinstance(downloader, str) or not downloader or not task:
            return "unknown"
        key = downloader, task
        if key not in self._receipt_cache:
            self._receipt_cache[key] = self._read(
                "SELECT filepath,state FROM downloadfiles WHERE downloader=? AND download_hash=? LIMIT 4097", key)
        rows = self._receipt_cache[key]
        if len(rows) > _MAX_RECEIPTS:
            return "unknown"
        matches = []
        for row in rows:
            path = Path(row["filepath"] or "")
            if path.suffix.lower() in _VIDEO:
                meta = self.meta(path.name)
                if meta.begin_season in (None, snapshot["season"]) and ep in (meta.episode_list or []):
                    matches.append(row["state"])
        if matches and all(state == 0 for state in matches):
            if key + ("absent",) not in self._receipt_cache:
                self._receipt_cache[key + ("absent",)] = self._task_absent(downloader, task)
            if self._receipt_cache[key + ("absent",)]:
                return "discarded"
        if key + ("live",) not in self._receipt_cache:
            self._receipt_cache[key + ("live",)] = self._live_receipt(task, downloader)
        return self._receipt_cache[key + ("live",)]

    def _live_receipt(self, task, downloader):
        # A download-history row remains after MP declines an overwrite. Only
        # the actual downloader or current transfer queue proves that it is still busy.
        from app.chain.transfer import TransferChain
        jobs = TransferChain().jobview
        if task in jobs.get_all_torrent_hashes():
            return "transferred" if jobs.is_torrent_done(task) else "active"
        torrents = self.downloads.list_torrents(downloader=downloader, hashs=[task], include_all_tags=True)
        for torrent in torrents or []:
            if getattr(torrent, "hash", None) != task or getattr(torrent, "downloader", None) != downloader:
                continue
            if "已整理" in re.split(r"[,，]\s*", getattr(torrent, "tags", "") or ""):
                return "transferred"
            if getattr(torrent, "state", None) in {"downloading", "paused"}:
                return "active"
        return "unknown"
