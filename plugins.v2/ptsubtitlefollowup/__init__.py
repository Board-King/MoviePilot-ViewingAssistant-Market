"""PT original-release subtitle follow-up for MoviePilot V2.

Only download/transfer completion events and explicitly selected download records
enter this queue. This plugin never calls transfer, delete, AI or download APIs.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from contextlib import contextmanager

import requests

from pydantic import BaseModel

from app.core.config import settings
from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType

from .providers import MTeamProvider, ProviderError, mteam_torrent_id
from .subtitle_files import (SubtitleError, ExistingSubtitleError,
                             parse_download, select_candidate, save_sidecar)
from .fusion.service import SnapshotService, DEFAULTS as FUSION_DEFAULTS
from .fusion.ui import _caption, _card, _field, _node, _text, get_form_fields, profile_summary, render_page
from .fusion.discovery import CandidateService
from .fusion.follow_context import normalize_follow_context
from .fusion.candidate_ui import CANDIDATE_DEFAULTS, get_candidate_form, render_candidates, browse_candidates, source_credits
from .fusion.artwork import CandidateArtwork
from .fusion.execution import ExecutionService
from .fusion.execution_store import ExecutionStore
from .fusion.execution_space import host_movie_download_space
from .fusion.execution_ui import EXECUTION_DEFAULTS, get_execution_form, render_execution
from .fusion.subscription_host import HostSubscriptionAdapter


class SeedRequest(BaseModel):
    download_history_id: int
    page_url: str


class ExecutionApproval(BaseModel):
    expected_digest: str


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _support_image():
    # <img> and download links do not carry MoviePilot's API authentication.
    # Return only this reviewed original inside the on-demand authenticated response.
    path = Path(__file__).parent / "assets" / "wechat-appreciation.jpg"
    try:
        with path.open("rb") as stream:
            original = stream.read(512 * 1024 + 1)
    except OSError:
        return ""
    if hashlib.sha256(original).hexdigest() != "6d4dc8e46531420a80d547e932aad910d5b0daf33edb8f12093e41b8a58e0ae1":
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(original).decode("ascii")


def safe_page(url):
    torrent_id = mteam_torrent_id(url)
    return f"https://kp.m-team.cc/detail/{torrent_id}" if torrent_id else None


def mapped_target(storage, destination, mappings, local_roots):
    """Map only successful MP destinations; never invent a media/STRM file."""
    path = Path(destination)
    if not path.is_absolute() or ".." in path.parts:
        return None
    if storage == "local":
        for root in local_roots:
            try:
                path.relative_to(Path(root))
                return str(path), str(root)
            except ValueError:
                continue
        return None
    if storage not in {"CloudDrive储存", "u115", "115网盘Plus"}:
        return None
    candidates = []
    for line in mappings.splitlines():
        parts = [p.strip() for p in line.split("#")]
        if len(parts) < 2 or not all(Path(p).is_absolute() for p in parts[:2]):
            continue
        local, remote = Path(parts[0]), Path(parts[1])
        try:
            relative = path.relative_to(remote)
        except ValueError:
            continue
        candidates.append((len(remote.parts), str((local / relative).with_suffix(".strm")), str(local)))
    return max(candidates)[1:] if candidates else None


class PtSubtitleFollowup(_PluginBase):
    plugin_name = "观影助手"
    plugin_desc = "按你的 Emby 口味，发现下一部想看的片。"
    plugin_icon = "https://raw.githubusercontent.com/Board-King/MoviePilot-ViewingAssistant-Market/main/icons/viewing-assistant.png"
    plugin_version = "0.6.4"
    plugin_author = "Board-King"
    author_url = "https://github.com/Board-King"
    plugin_url = "https://github.com/Board-King/MoviePilot-ViewingAssistant-Market"
    plugin_config_prefix = "ptsubtitlefollowup_"
    plugin_order = 95
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._enabled = False
        self._tasks = {}
        self._fusion = SnapshotService(self.get_data, self.save_data, self._fusion_server)
        self._candidates = CandidateService(self.get_data, self.save_data, self._discovery_credentials,
                                            self._fusion.evaluation_context,
                                            source_context_supplier=self._discovery_source_context)
        self._artwork = CandidateArtwork(lambda: self.get_data_path(), self._discovery_credentials)
        # Clones may rank candidates but have no separate execution authority or ledger.
        self._execution = None
        if type(self).__name__ == "PtSubtitleFollowup" and not getattr(self, "is_clone", False):
            self._execution = ExecutionService(
                ExecutionStore(lambda: self.get_data_path() / "automatic-subscriptions"),
                self._candidates.status, lambda: HostSubscriptionAdapter(self._all_media_servers),
                current_config=self.get_config,
                storage_check=lambda: host_movie_download_space(storage_verified=True, systemconfig=self.systemconfig))

    def init_plugin(self, config=None):
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._hours = max(1, min(24, int(config.get("interval_hours") or 3)))
        self._days = max(1, min(14, int(config.get("retry_days") or 7)))
        self._notify = bool(config.get("notify", False))
        self._local_roots = [x.strip() for x in str(config.get("local_media_roots") or "").splitlines()
                             if x.strip() and Path(x.strip()).is_absolute()]
        self._stop.clear() if self._enabled else self._stop.set()
        with self._lock:
            stored = self.get_data("queue_v1") or {}
            self._tasks = stored.get("tasks", {}) if stored.get("schema") == 1 else {}
        self._fusion.configure(config)
        self._candidates.configure(config)
        self._artwork.configure()
        if self._execution:
            self._execution.configure(config)

    def get_render_mode(self):
        """Only the primary installation owns the Vue configuration and authorization flow."""
        if type(self).__name__ == "PtSubtitleFollowup" and not getattr(self, "is_clone", False):
            return "vue", "dist/assets"
        return "vuetify", None

    def get_state(self):
        return bool(self._enabled or self._fusion.enabled or self._candidates.enabled or
                    self._execution and self._execution.needs_tick)

    def get_service(self):
        services = []
        if self._enabled:
            services.append({"id": "PtSubtitleFollowupRetry", "name": "PT配套字幕延迟补查",
                             "trigger": "interval", "func": self.run_once, "kwargs": {"hours": self._hours}})
        if self._fusion.enabled:
            services.append({"id": "PtSubtitleFollowupSnapshot", "name": "观影助手用户快照调度",
                             "trigger": "interval", "func": self._fusion.tick, "kwargs": {"minutes": 1}})
        if self._candidates.enabled:
            services.append({"id": "PtSubtitleFollowupCandidates", "name": "观影助手候选发现",
                             "trigger": "interval", "func": self._update_candidates, "kwargs": {"minutes": 1}})
        if self._execution and self._execution.needs_tick:
            services.append({"id": "PtSubtitleFollowupExecution", "name": "观影助手自动订阅",
                             "trigger": "interval", "func": self._execution.tick, "kwargs": {"minutes": 5}})
        return services

    def stop_service(self):
        if self._execution:
            self._execution.stop()
        self._artwork.stop()
        self._stop.set()
        self._fusion.stop()
        self._candidates.stop()

    def _update_candidates(self):
        self._candidates.tick()
        try:
            self._artwork.refresh(self._candidates.status())
        except Exception:
            logger.warning("[观影助手] 海报缓存暂时不可用")

    def _all_media_servers(self):
        from app.schemas.types import SystemConfigKey
        return self.systemconfig.get(SystemConfigKey.MediaServers)

    def _discovery_credentials(self):
        # Only the explicitly configured host proxy is reused. settings.PROXY
        # otherwise falls back to process environment variables in this MP version.
        proxy_host = getattr(settings, "PROXY_HOST", None)
        proxies = getattr(settings, "PROXY", None) if isinstance(proxy_host, str) and proxy_host.strip() else None
        return {"api_key": getattr(settings, "TMDB_API_KEY", None), "proxies": copy.deepcopy(proxies)}

    def _discovery_source_context(self):
        # Read only when the opt-in followed source asks. Never enable sharing,
        # call subscription/follow helpers, or expose opaque follow IDs in status.
        try:
            enabled = getattr(settings, "SUBSCRIBE_STATISTIC_SHARE", False)
            if enabled is not True:
                return normalize_follow_context({"context_valid": True, "sharing_enabled": enabled})
            from app.schemas.types import SystemConfigKey
            followers = self.systemconfig.get(SystemConfigKey.FollowSubscribers)
            return normalize_follow_context({"context_valid": True, "sharing_enabled": True,
                                             "followed_uids": followers})
        except Exception:
            return normalize_follow_context(None)

    def _fusion_servers(self):
        # Borrow the already initialized host configuration object, never create
        # a diagnostic database/session or copy its credentials into plugin data.
        from app.schemas.types import SystemConfigKey
        rows = self.systemconfig.get(SystemConfigKey.MediaServers) or []
        return [row for row in rows if isinstance(row, dict) and row.get("enabled")
                and str(row.get("type", "")).lower() == "emby"]

    def _fusion_server(self, reference):
        servers = [row for row in self._fusion_servers() if row.get("name") == reference]
        if len(servers) != 1:
            raise ValueError("Configured server reference is unavailable or ambiguous")
        return copy.deepcopy(servers[0].get("config") or {})

    def _save(self):
        # Called under the queue lock. Credentials and signed URLs are never stored.
        self.save_data("queue_v1", {"schema": 1, "tasks": self._tasks})

    @contextmanager
    def _db(self):
        # Reuse the running host's synchronous SQLAlchemy Engine. Do not open
        # separate SQLite handles or change journal/connection configuration.
        from app.db import Engine

        connection = Engine.connect()

        class ReadConnection:
            def execute(self, sql, parameters=()):
                # Keep the existing SQL and positional parameters unchanged;
                # callers receive dictionary-like rows rather than tuple rows.
                return connection.exec_driver_sql(sql, parameters).mappings()

        try:
            yield ReadConnection()
        finally:
            connection.close()

    def _site(self, site_id):
        with self._db() as connection:
            row = connection.execute(
                "SELECT id,name,domain,url,ua,cookie,apikey,proxy FROM site WHERE id=?", (site_id,)
            ).fetchone()
        return dict(row) if row else None

    def _enqueue(self, download_hash, site_id, page_url, title, source_name, download_date, downloader=""):
        page = safe_page(page_url)
        if not page or not re.fullmatch(r"[a-fA-F0-9]{32,64}", str(download_hash or "")):
            return {"success": False, "message": "首版仅补查馒头原发布页，其他站点保留MP原生字幕流程"}
        key = str(download_hash).lower()
        now = time.time()
        with self._lock:
            if key in self._tasks:
                return {"success": True, "message": "任务已登记，不重复加入"}
            # Retain pending tasks; evict only the oldest completed records.
            terminal = sorted((v.get("updated", 0), k) for k, v in self._tasks.items()
                              if v.get("status") in {"saved", "existing", "expired"})
            while len(self._tasks) >= 500 and terminal:
                _, oldest = terminal.pop(0)
                self._tasks.pop(oldest, None)
            if len(self._tasks) >= 500:
                return {"success": False, "message": "补查队列已达上限；正常下载和上云不受影响"}
            self._tasks[key] = {
                "hash": key, "site_id": int(site_id), "page_url": page,
                "torrent_id": mteam_torrent_id(page), "title": str(title)[:240],
                "source_name": str(source_name)[:500], "download_date": str(download_date),
                "downloader": str(downloader), "created": now, "updated": now,
                "expires": now + self._days * 86400, "next_check": now,
                "status": "waiting_transfer", "attempts": 0, "targets": {},
                "revision": 0,
                "message": "等待正常入库后补查配套字幕", "notified": False,
            }
            self._save()
        return {"success": True, "message": "已加入原发布页字幕补查；不影响原有整理"}

    @eventmanager.register(EventType.DownloadAdded)
    def on_download_added(self, event: Event):
        if not self._enabled or self._stop.is_set():
            return
        try:
            data = event.event_data or {}
            context = data.get("context")
            torrent = value(context, "torrent_info")
            media = value(context, "media_info")
            if not torrent:
                return
            result = self._enqueue(
                data.get("hash"), value(torrent, "site"), value(torrent, "page_url"),
                value(media, "title") or value(torrent, "title", "新下载"),
                value(torrent, "title", ""), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 60)),
                data.get("downloader", ""),
            )
            if not result["success"]:
                logger.info("[PT字幕补全] " + result["message"])
        except Exception:
            logger.warning("[PT字幕补全] 新任务登记失败，原下载和整理继续；请查看插件状态")

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        if not self._enabled or self._stop.is_set():
            return
        data = event.event_data or {}
        key = str(data.get("download_hash") or "").lower()
        with self._lock:
            if key in self._tasks:
                self._tasks[key]["revision"] = self._tasks[key].get("revision", 0) + 1
                if self._tasks[key]["status"] in {"saved", "existing"}:
                    self._tasks[key]["status"] = "waiting_transfer"
                    self._tasks[key]["notified"] = False
                self._tasks[key]["next_check"] = min(self._tasks[key].get("next_check", 0), time.time())
                self._save()
        # No network or transfer operation runs inside the completion handler.

    def _sync_targets(self, task):
        cfg = self.get_config("P115StrmHelper") or {}
        with self._db() as connection:
            rows = connection.execute(
                "SELECT dest,dest_storage,type,seasons,episodes,title,year FROM transferhistory "
                "WHERE download_hash=? AND status=1 AND date>=? ORDER BY id DESC LIMIT 200",
                (task["hash"], task["download_date"]),
            ).fetchall()
        for row in rows:
            mapping = mapped_target(row["dest_storage"], row["dest"],
                                    cfg.get("transfer_monitor_paths") or "", self._local_roots)
            if not mapping:
                continue
            path, root = mapping
            if path in task["targets"]:
                continue
            season = re.fullmatch(r"S(\d+)", str(row["seasons"] or ""), re.I)
            episode = re.fullmatch(r"E(\d+)", str(row["episodes"] or ""), re.I)
            is_tv = row["type"] not in {"电影", "Movie", "movie"}
            if is_tv and not (season and episode):
                task["message"] = "季集信息不完整，保留待处理，未猜测匹配"
                continue
            task["targets"][path] = {"root": root, "status": "waiting", "title": row["title"],
                                      "season": int(season.group(1)) if season else None,
                                      "episode": int(episode.group(1)) if episode else None}

    @staticmethod
    def _has_chinese_sidecar(path):
        media = Path(path)
        if not media.is_file() or media.is_symlink():
            return False
        for candidate in media.parent.iterdir():
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if not candidate.name.startswith(media.stem + "."):
                continue
            suffix = candidate.name[len(media.stem):]
            if candidate.suffix.lower() in {".srt", ".ass", ".ssa", ".sup"} and (
                suffix.lower() in {".srt", ".ass", ".ssa", ".sup"}
                or re.search(r"(?:^|[. _\-\[])(?:zh|chi|chs|cht|zho|zhs|zht)(?:$|[. _\-\]])|中文|简|繁", suffix, re.I)
            ):
                # An unlabelled existing subtitle is preserved conservatively as well.
                return True
        return False

    @staticmethod
    def _cached_duration(path):
        p = Path(path)
        cached = p.with_name(p.stem + "-mediainfo.json")
        try:
            if not cached.is_file() or cached.stat().st_size > 2 * 1024 * 1024:
                return None
            data = json.loads(cached.read_text(encoding="utf-8"))
            first = data[0] if isinstance(data, list) and data else data
            ticks = first.get("MediaSourceInfo", {}).get("RunTimeTicks")
            return float(ticks) / 10_000_000 if ticks else None
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def _refresh_emby(self, media_path):
        """Refresh one uniquely identified item; fallback is only its mapped path."""
        cfg = self.get_config("P115StrmHelper") or {}
        mapped = []
        for line in str(cfg.get("transfer_mp_mediaserver_paths") or "").splitlines():
            pair = [x.strip() for x in line.split("#")]
            if len(pair) != 2 or not all(Path(x).is_absolute() for x in pair):
                continue
            try:
                relative = Path(media_path).relative_to(Path(pair[1]))
                mapped.append((len(Path(pair[1]).parts), str(Path(pair[0]) / relative)))
            except ValueError:
                continue
        if not mapped:
            return "未配置媒体服务器路径映射，由媒体服务器常规监控发现字幕"
        server_path = max(mapped)[1]
        selected = cfg.get("transfer_monitor_mediaservers") or []
        servers = self.systemconfig.get("MediaServers") or []
        results = []
        exact_refreshes = 0
        for server in servers:
            if server.get("type") != "emby" or not server.get("enabled") or server.get("name") not in selected:
                continue
            conf = server.get("config") or {}
            host = str(conf.get("host") or "").rstrip("/")
            if not host or not conf.get("apikey"):
                continue
            base = host + ("" if host.endswith("/emby") else "/emby")
            headers = {"X-Emby-Token": conf["apikey"]}
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    item_id = None
                    try:
                        with session.get(base + "/Items", headers=headers,
                                         params={"Path": server_path, "Recursive": "true", "Fields": "Path",
                                                 "Limit": 2, "EnableImages": "false", "EnableUserData": "false"},
                                         timeout=(5, 10), allow_redirects=False, stream=True) as response:
                            if response.status_code == 200:
                                chunks, size = [], 0
                                for chunk in response.iter_content(16384):
                                    size += len(chunk)
                                    if size > 256 * 1024:
                                        raise ValueError("Item lookup response exceeds limit")
                                    chunks.append(chunk)
                                data = json.loads(b"".join(chunks))
                                items = data.get("Items") if isinstance(data, dict) else None
                                if (isinstance(items, list) and len(items) == 1
                                        and data.get("TotalRecordCount", 1) == 1
                                        and isinstance(items[0], dict) and items[0].get("Path") == server_path):
                                    candidate_id = items[0].get("Id")
                                    if (not isinstance(candidate_id, bool)
                                            and isinstance(candidate_id, (str, int))
                                            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(candidate_id))):
                                        item_id = str(candidate_id)
                    except (requests.RequestException, ValueError, TypeError):
                        # Do not expose URLs, API keys or response text. A failed
                        # lookup is allowed to notify only the already mapped path.
                        item_id = None
                    if item_id:
                        with session.post(base + f"/Items/{item_id}/Refresh", headers=headers,
                                          params={"Recursive": "false", "MetadataRefreshMode": "Default",
                                                  "ImageRefreshMode": "Default", "ReplaceAllMetadata": "false",
                                                  "ReplaceAllImages": "false"}, json={},
                                          timeout=(5, 10), allow_redirects=False) as response:
                            success = response.status_code in {200, 202, 204}
                            results.append(success)
                            exact_refreshes += int(success)
                    else:
                        with session.post(base + "/Library/Media/Updated", headers=headers,
                                          json={"Updates": [{"Path": server_path, "UpdateType": "Created"}]},
                                          timeout=(5, 10), allow_redirects=False) as response:
                            results.append(response.status_code in {200, 202, 204})
            except requests.RequestException:
                results.append(False)
        if results and all(results):
            return "已请求Emby单片刷新" if exact_refreshes == len(results) else "已通知Emby单路径更新；若字幕未显示，请刷新该影片"
        return "字幕已保存；Emby刷新未确认，请在Emby刷新该影片"

    def _check_task(self, task):
        self._sync_targets(task)
        targets = task["targets"]
        if not targets:
            task.update(status="waiting_transfer", message="等待原流程入库及STRM生成")
            return
        pending = []
        for path, target in targets.items():
            if target["status"] in {"saved", "existing"}:
                continue
            if not Path(path).is_file():
                target["message"] = "本地媒体/STRM尚未出现"
                continue
            if self._has_chinese_sidecar(path):
                target.update(status="existing", message="已有字幕，保留原文件")
                continue
            pending.append((path, target))
        if not pending:
            task.update(status="existing" if all(t["status"] == "existing" for t in targets.values()) else
                        "saved" if all(t["status"] in {"saved", "existing"} for t in targets.values()) else "waiting_transfer",
                        message="已有字幕或已保存；原文件未被覆盖")
            return
        site = self._site(task["site_id"])
        if not site:
            raise ProviderError("原下载站点已不存在，请检查MP站点设置")
        if str(site.get("domain", "")).strip().lower() != "m-team.cc":
            raise ProviderError("站点身份与原发布页不一致，未发送凭据")
        provider = MTeamProvider(site, settings.PROXY if site.get("proxy") else None)
        try:
            remote = provider.list_subtitles(task["torrent_id"])
            task["message"] = "发布页暂未提供可用中文字幕，下个周期再查"
            cache = {}
            # Rotate previously attempted targets rather than permanently
            # starving episode 21+ behind unavailable subtitles for episodes 1–20.
            ordered_pending = sorted(pending, key=lambda row: (row[1].get("last_attempt", 0), row[0]))
            download_attempts = set()
            for path, target in ordered_pending[:20]:
                if self._stop.is_set():
                    return
                target["last_attempt"] = time.time()
                cursor = int(target.get("candidate_cursor", 0)) % len(remote) if remote else 0
                candidates_to_try = [(index % len(remote), remote[index % len(remote)])
                                     for index in range(cursor, cursor + min(3, len(remote)))] if remote else []
                for index, item in candidates_to_try:
                    if self._stop.is_set():
                        return
                    if item.identifier not in cache and len(download_attempts) >= 3:
                        # Leave this candidate pending. Other targets may reuse
                        # already downloaded payloads without extra requests.
                        break
                    target["candidate_cursor"] = (index + 1) % len(remote)
                    try:
                        if item.identifier not in cache:
                            download_attempts.add(item.identifier)
                            cache[item.identifier] = provider.download(item)
                        candidates = parse_download(item.filename, cache[item.identifier],
                                                    video_duration=self._cached_duration(path),
                                                    season=target.get("season"), episode=target.get("episode"))
                        candidate = select_candidate(candidates, season=target.get("season"), episode=target.get("episode"))
                        if self._stop.is_set():
                            return
                        saved = save_sidecar(candidate, path, [target["root"]])
                        target.update(status="saved", message="已保存PT配套中文字幕", subtitle=str(saved),
                                      source_subtitle_id=item.identifier, sha256=candidate.sha256)
                        target["refresh"] = self._refresh_emby(path)
                        task["message"] = "已保存PT配套中文字幕到本地"
                        logger.info("[PT字幕补全] 配套字幕已保存：%s", Path(saved).name)
                        break
                    except ExistingSubtitleError:
                        target.update(status="existing", message="已有字幕，未覆盖")
                        break
                    except SubtitleError:
                        target["message"] = "候选未通过格式/中文/季集检查，未写入"
            done = all(t["status"] in {"saved", "existing"} for t in targets.values())
            task["status"] = "saved" if done and any(t["status"] == "saved" for t in targets.values()) else "existing" if done else "waiting_subtitle"
        finally:
            provider.close()

    def run_once(self, force: bool = False):
        if not self._enabled or self._stop.is_set() or not self._run_lock.acquire(blocking=False):
            return {"success": True, "message": "插件未启用或已有补查正在运行"}
        checked = 0
        try:
            with self._lock:
                due = [(v.get("next_check", 0), k) for k, v in self._tasks.items()
                       if v["status"] not in {"saved", "existing", "expired"}
                       and (force or v.get("next_check", 0) <= time.time())]
                keys = [k for _, k in sorted(due)[:10]]
            for key in keys:
                if self._stop.is_set():
                    break
                with self._lock:
                    task = copy.deepcopy(self._tasks[key])
                if task["expires"] <= time.time():
                    task.update(status="expired", message="补查期限已到，仍无可用配套字幕；正常上云未受影响")
                else:
                    try:
                        self._check_task(task)
                    except ProviderError as error:
                        task.update(status="retry", message=str(error))
                    except Exception:
                        task.update(status="retry", message="本次补查未完成，稍后重试；未改变原整理流程")
                        logger.warning("[PT字幕补全] 补查异常，敏感请求信息已隐藏")
                    task["attempts"] += 1
                task["updated"] = time.time()
                task["next_check"] = time.time() + self._hours * 3600
                if task["status"] in {"saved", "expired"} and not task.get("notified"):
                    # The page is always updated. External notifications are opt-in.
                    if self._notify:
                        try:
                            self.post_message(mtype=NotificationType.Plugin, title="【PT配套字幕补全】" + task["title"],
                                              text=task["message"])
                        except Exception:
                            logger.warning("[PT字幕补全] 通知未送达，任务结果仍保留在插件页面")
                    task["notified"] = True
                with self._lock:
                    current_revision = self._tasks[key].get("revision", 0)
                    if current_revision != task.get("revision", 0) and task["status"] != "expired":
                        task.update(revision=current_revision, status="waiting_transfer",
                                    next_check=time.time(), notified=False,
                                    message="检查期间有新入库文件，下个周期继续补查")
                    self._tasks[key] = task
                    self._save()
                checked += 1
            return {"success": True, "message": f"已检查 {checked} 个已登记下载任务"}
        finally:
            self._run_lock.release()

    def seed(self, request: SeedRequest):
        if not self._enabled:
            return {"success": False, "message": "请先启用插件"}
        if not safe_page(request.page_url):
            return {"success": False, "message": "请输入馒头原种子详情页地址"}
        with self._db() as connection:
            record = connection.execute("SELECT * FROM downloadhistory WHERE id=?", (request.download_history_id,)).fetchone()
            if not record:
                return {"success": False, "message": "未找到选定下载记录"}
            site = connection.execute("SELECT id,domain FROM site WHERE name=?", (record["torrent_site"],)).fetchone()
        if not site or str(site["domain"]).strip().lower() != "m-team.cc":
            return {"success": False, "message": "选定下载记录不属于馒头站点"}
        return self._enqueue(record["download_hash"], site["id"], request.page_url, record["title"],
                             record["torrent_name"], record["date"], record["downloader"])

    def status(self):
        with self._lock:
            return {"enabled": self._enabled, "schema": 1, "tasks": copy.deepcopy(list(self._tasks.values()))}

    def _support(self):
        return {"image": _support_image()}

    def _browse_candidates(self, kind: str = "all", period: str = "all", page: int = 1):
        return browse_candidates(self._candidates.status(), self._artwork.decorate, kind, period, page)

    def get_api(self):
        from fastapi import Depends, Header, HTTPException
        from app.db.user_oper import get_current_active_superuser
        fusion_routes = [
            {"path": "/fusion/status", "endpoint": self._fusion.status, "methods": ["GET"], "summary": "观影画像状态"},
            {"path": "/fusion/validate", "endpoint": self._fusion.validate, "methods": ["POST"], "summary": "验证当前用户绑定"},
            {"path": "/fusion/refresh", "endpoint": self._fusion.request_refresh, "methods": ["POST"], "summary": "排队刷新授权画像"},
            {"path": "/fusion/candidates/status", "endpoint": self._candidates.status, "methods": ["GET"], "summary": "只读候选池与排序解释"},
            {"path": "/fusion/candidates/browse", "endpoint": self._browse_candidates, "methods": ["GET"], "summary": "按类型和发现时间浏览候选"},
            {"path": "/fusion/candidates/refresh", "endpoint": self._candidates.request_refresh, "methods": ["POST"], "summary": "排队拉取候选来源"},
        ]
        for route in fusion_routes:
            route.update(auth="bear", dependencies=[Depends(get_current_active_superuser)])
        def signed_in(authorization: str = Header(default="", alias="Authorization"),
                      current_user=Depends(get_current_active_superuser)):
            # The host also accepts a shared API key for bearer-mode routes.
            if (getattr(current_user, "is_active", False) is not True or
                    not re.fullmatch(r"(?i:Bearer) [A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", authorization or "")):
                raise HTTPException(status_code=403, detail="请使用管理员账号登录后操作")

        fusion_routes.append({"path": "/support", "endpoint": self._support, "methods": ["GET"], "auth": "bear",
                              "dependencies": [Depends(get_current_active_superuser), Depends(signed_in)]})
        if self._execution:
            for name, endpoint, method in (("status", self._execution.status, "GET"),
                                           ("preflight", self._execution_preflight, "POST"),
                                           ("review", self._execution_review, "GET"),
                                           ("authorize", self._authorize_execution, "POST"),
                                           ("revoke", self._execution.revoke, "POST")):
                deps = [Depends(get_current_active_superuser)]
                if method == "POST":
                    deps.append(Depends(signed_in))
                fusion_routes.append({"path": "/fusion/execution/" + name, "endpoint": endpoint,
                                      "methods": [method], "auth": "bear", "dependencies": deps})
        return [{"path": "/status", "endpoint": self.status, "methods": ["GET"], "auth": "apikey", "summary": "配套字幕补查状态"},
                {"path": "/run", "endpoint": self.run_once, "methods": ["POST"], "auth": "apikey", "summary": "检查到期的字幕任务"},
                {"path": "/seed", "endpoint": self.seed, "methods": ["POST"], "auth": "apikey", "summary": "仅补查选定下载记录"}, *fusion_routes]

    def _authorize_execution(self, request: ExecutionApproval):
        return self._execution.authorize(request.expected_digest)

    def _execution_preflight(self, draft: dict):
        # Do not contact Emby or inspect viewing records before a binding has been saved.
        try:
            selected = draft.get("fusion_server_ref")
            if not selected or sum(row.get("name") == selected for row in self._fusion_servers()) != 1:
                return {"success": False, "reason": "SERVER_UNAVAILABLE"}
        except Exception:
            return {"success": False, "reason": "SERVER_UNAVAILABLE"}
        return self._execution.preflight(draft)

    def _execution_review(self):
        status = self._execution.status()
        profile = self._fusion.status()
        if profile.get("binding_verified") and profile.get("enabled"):
            status["profile_name"] = profile.get("user_name")
        return {"page": render_execution(status, self.__class__.__name__)}

    def get_form(self):
        try:
            server_options = [{"title": row["name"], "value": row["name"]}
                              for row in self._fusion_servers() if row.get("name")]
        except Exception:
            server_options = []
        subtitle_form = _card("配套字幕", [
            _node("VRow", content=[
                _field("VSwitch", "enabled", "自动补查中文字幕", color="primary"),
                _field("VSwitch", "notify", "完成和到期通知", color="primary"),
            ]),
            _node("details", props={"class": "mt-2"}, content=[
                _node("summary", "补查设置", props={"class": "text-body-2 cursor-pointer"}),
                _node("VRow", props={"class": "mt-2"}, content=[
                    _field("VTextField", "interval_hours", "补查间隔（小时）", type="number", min=1, max=24),
                    _field("VTextField", "retry_days", "最长等待（天）", type="number", min=1, max=14),
                    _field("VTextarea", "local_media_roots", "本地媒体库根目录", cols=12, rows=2,
                           hint="每行一个。115 STRM 沿用助手映射。"),
                ]),
                _caption("馒头原发布页 · 新下载及手选任务 · 保留已有字幕"),
            ]),
        ])
        execution_form = get_execution_form() if self._execution else []
        return [*get_form_fields(server_options), *get_candidate_form(), *execution_form, subtitle_form], {
                    "enabled": False, "notify": False, "interval_hours": 3, "retry_days": 7, "local_media_roots": "",
                    **FUSION_DEFAULTS, **copy.deepcopy(CANDIDATE_DEFAULTS),
                    **(copy.deepcopy(EXECUTION_DEFAULTS) if self._execution else {})}

    def get_page(self):
        labels = {"waiting_transfer": "等待入库", "waiting_subtitle": "等待配套字幕", "retry": "等待重试",
                  "saved": "字幕已保存", "existing": "保留已有字幕", "expired": "补查已到期"}
        with self._lock:
            tasks = sorted(copy.deepcopy(list(self._tasks.values())), key=lambda t: t["created"], reverse=True)[:100]
        candidate_page = render_candidates(self._artwork.decorate(self._candidates.status()), self.__class__.__name__)
        profile_status = self._fusion.status()
        profile_page = render_page(profile_status, self.__class__.__name__)
        profile_attention = (profile_status.get("enabled") is True and
                             (profile_status.get("status") in {"error", "unconfigured", "unverified", "scope_required"}
                              or profile_status.get("stale") is True))
        if self._candidates.enabled:
            remaining_profile = profile_page[1:] if profile_attention else profile_page
            profile_details = _node("details", props={"class": "mb-4"}, content=[
                _node("summary", "偏好详情" if profile_attention else profile_summary(profile_status),
                      props={"class": "text-body-2 px-3 py-2 cursor-pointer"}),
                *remaining_profile,
            ])
            overview = [candidate_page[0], *([profile_page[0]] if profile_attention else []),
                        *([profile_details] if remaining_profile else []), *candidate_page[1:]]
        else:
            overview = [*profile_page, *candidate_page]

        def needs_refresh(task):
            return any("Emby刷新未确认" in str(target.get("refresh", ""))
                       for target in task.get("targets", {}).values())

        def task_row(task):
            state = task["status"]
            refresh_pending = needs_refresh(task)
            color = "warning" if refresh_pending or state in {"retry", "expired"} else "success" if state in {"saved", "existing"} else "medium-emphasis"
            refresh_messages = list(dict.fromkeys(
                _text(str(target.get("refresh")), limit=240) for target in task.get("targets", {}).values()
                if target.get("refresh")
            ))[:3]
            message = _text(task.get("message"), limit=360)
            # Keep errors actionable. Routine queue activity belongs in its record.
            issue = state in ("retry", "expired") or message.startswith("季集信息不完整")
            return _node("div", props={"class": "py-3"}, content=[
                _node("div", props={"class": "d-flex flex-wrap align-center justify-space-between ga-2"}, content=[
                    _node("div", _text(task["title"], "片名未知", 180), props={"class": "text-subtitle-2 text-break"}),
                    _node("span", "需刷新 Emby" if refresh_pending else labels.get(state, "待处理"),
                          props={"class": f"text-caption text-{color}"}),
                ]),
                *([_caption(message, "mt-1")] if issue and message else []),
                *([_caption("请在 Emby 刷新这部作品。", "mt-1")] if refresh_pending else []),
                _node("details", props={"class": "mt-2"}, content=[
                    _node("summary", "补查记录", props={"class": "text-caption text-medium-emphasis cursor-pointer"}),
                    _caption(f"{task['attempts']} 次补查 · {len(task['targets'])} 个文件", "mt-1"),
                    *([_caption(message, "mt-1")] if not issue and message else []),
                    *[_caption(note, "mt-1") for note in refresh_messages],
                ]),
            ])

        def task_rows(rows):
            content = []
            for task in rows:
                if content:
                    content.append(_node("VDivider"))
                content.append(task_row(task))
            return content

        pending = [task for task in tasks if task["status"] not in {"saved", "existing"} or needs_refresh(task)]
        completed = [task for task in tasks if task["status"] in {"saved", "existing"} and not needs_refresh(task)]
        subtitles = task_rows(pending) if pending else [
            _caption("暂无待补查任务" if self._enabled else "未启用", "")]
        if completed:
            subtitles.append(_node("details", props={"class": "mt-3"}, content=[
                _node("summary", f"已完成 · {len(completed)}", props={"class": "text-body-2 cursor-pointer"}),
                *task_rows(completed),
            ]))
        subtitles.append(_node("details", props={"class": "mt-3"}, content=[
            _node("summary", "补查范围", props={"class": "text-caption text-medium-emphasis cursor-pointer"}),
            _caption("原发布页中文字幕 · 不阻塞上云 · 时间轴未校验", "mt-1"),
        ]))
        execution_page = []
        if self._execution:
            execution_status = self._execution.status()
            if profile_status.get("binding_verified") and profile_status.get("enabled"):
                execution_status["profile_name"] = profile_status.get("user_name")
            execution_page = render_execution(execution_status, self.__class__.__name__)
        return [*overview, *execution_page, _card("配套字幕", subtitles), source_credits()]
