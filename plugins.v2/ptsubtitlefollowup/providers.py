"""Original-torrent subtitle providers. No search engine, AI or account storage."""
from __future__ import annotations

import re
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

MAX_DOWNLOAD = 8 * 1024 * 1024
MACHINE_MARK = re.compile(r"机翻|機翻|机器翻译|機器翻譯|AI[ _-]*(?:翻译|翻譯|字幕)|machine[ _-]*translat", re.I)


class ProviderError(Exception):
    """A deliberately sanitized error, safe to show in the plugin page."""


@dataclass(frozen=True)
class RemoteSubtitle:
    identifier: str
    filename: str
    created: str = ""


def mteam_torrent_id(page_url: str) -> str | None:
    parsed = urlsplit(page_url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if host != "m-team.cc" and not host.endswith(".m-team.cc"):
        return None
    match = re.fullmatch(r"/detail/(\d+)/?", parsed.path)
    return match.group(1) if match else None


class MTeamProvider:
    """Uses the site's configured API key; credentials never enter task state."""

    base = "https://api.m-team.cc"

    def __init__(self, site: dict, proxies: dict | None = None):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = proxies or {}
        self.api_headers = {
            "User-Agent": site.get("ua") or "MoviePilot",
            "x-api-key": site.get("apikey") or "",
            "Accept": "application/json, text/plain, */*",
        }
        self.download_headers = {
            "User-Agent": site.get("ua") or "MoviePilot",
            "Referer": "https://kp.m-team.cc/",
        }
        if site.get("cookie"):
            self.download_headers["Cookie"] = site["cookie"]
        if not self.api_headers["x-api-key"]:
            raise ProviderError("站点未配置 API Key，请在 MP 站点设置中维护")

    def close(self):
        self.session.close()

    def _post(self, endpoint: str, identifier: str):
        try:
            with self.session.post(
                self.base + endpoint, data={"id": identifier},
                headers=self.api_headers, timeout=(8, 20), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code != 200:
                    raise ProviderError(f"站点接口 HTTP {response.status_code}")
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > MAX_DOWNLOAD:
                        raise ProviderError("站点接口响应过大")
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks))
                if not isinstance(data, dict) or str(data.get("code")) != "0":
                    raise ProviderError("站点接口未授权或暂时不可用")
                return data.get("data")
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise ProviderError("站点请求失败，将在下个周期重试") from None

    def list_subtitles(self, torrent_id: str) -> list[RemoteSubtitle]:
        if not re.fullmatch(r"\d+", str(torrent_id)):
            raise ProviderError("种子 ID 无效")
        items = self._post("/api/subtitle/list", str(torrent_id))
        if not isinstance(items, list):
            raise ProviderError("站点字幕列表格式发生变化")
        results = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or item.get("name") or "")
            labels = " ".join(str(item.get(k) or "") for k in ("name", "filename", "description", "note", "remark"))
            if MACHINE_MARK.search(labels):
                continue
            if not re.search(r"\.(srt|ass|ssa|zip)$", filename, re.I):
                continue
            # Clearly English-only entries are not downloaded. Content is also checked later.
            has_chinese_marker = re.search(r"\b(?:chi|chs|cht|zho|zh|zhs|zht)\b|简|繁|中文", filename, re.I)
            if str(item.get("lang")) == "6" and not has_chinese_marker:
                continue
            identifier = str(item.get("id") or "")
            if not identifier.isdigit():
                continue
            results.append(RemoteSubtitle(identifier, filename, str(item.get("createdDate") or "")))
        return sorted(results, key=lambda row: (row.created, int(row.identifier)), reverse=True)

    def download(self, subtitle: RemoteSubtitle) -> bytes:
        credential = self._post("/api/subtitle/genlink", subtitle.identifier)
        if not isinstance(credential, str) or not credential or len(credential) > 4096:
            raise ProviderError("站点字幕下载凭据格式异常")
        if any(c in credential for c in "\r\n&#"):
            raise ProviderError("站点字幕下载凭据格式异常")
        # M-Team returns an already URL-escaped credential. requests(params=...) would
        # escape '%' again and the server would return a JSON denial instead of subtitles.
        url = self.base + "/api/subtitle/dlV2?credential=" + credential
        try:
            with self.session.get(url, headers=self.download_headers,
                                  timeout=(8, 25), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ProviderError(f"字幕下载 HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").lower()
                if "json" in content_type or "html" in content_type:
                    raise ProviderError("站点返回了错误页面，未保存为字幕")
                chunks, length = [], 0
                for chunk in response.iter_content(65536):
                    length += len(chunk)
                    if length > MAX_DOWNLOAD:
                        raise ProviderError("字幕下载超过 8 MB 限制")
                    chunks.append(chunk)
                return b"".join(chunks)
        except ProviderError:
            raise
        except requests.RequestException:
            raise ProviderError("字幕下载失败，将在下个周期重试") from None
