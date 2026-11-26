"""Search within the existing subscription's MoviePilot sites, filters and budget."""
from copy import copy
import hashlib
import re
from uuid import uuid4

from .gap_host import release


def _filters(params, include, snapshot, allow_backup):
    # Only the subscription's standalone group constraint may be relaxed.
    # Compound expressions and administrator defaults remain in force.
    result = dict(params)
    known_groups = {snapshot["group"], *snapshot.get("episode_groups", {}).values()}
    if (isinstance(include, str) and result.get("include") == include
            and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", include)
            and include.casefold() in known_groups):
        if allow_backup or include.casefold() != snapshot["group"]:
            # The exact group is enforced on parsed candidates after native filtering.
            del result["include"]
    return result


def _same_series(media, media_id, tv_type):
    if not media or media.type != tv_type or getattr(media, "episode_group", None):
        return False
    provider = getattr(media, "media_source", None) or getattr(media, "source", None)
    source_id = getattr(media, "media_id", None)
    tmdb_id = getattr(media, "tmdb_id", None)
    return (provider in ("themoviedb", None) and bool(provider or tmdb_id)
            and str(source_id or tmdb_id) == media_id and tmdb_id in (None, int(media_id), media_id))


def _offer(context, snapshot, tv_type):
    meta, torrent = context.meta_info, context.torrent_info
    if (not meta or not torrent or getattr(context, "match_status", None) == "candidate"
            or not _same_series(context.media_info, snapshot["media_id"], tv_type)
            or meta.season_list != [snapshot["season"]]):
        return None
    episodes = meta.episode_list
    if not episodes or len(episodes) > 1000 or any(type(n) is not int or not 1 <= n <= 5000 for n in episodes):
        return None
    try:
        group, spec = release(meta)
    except ValueError:
        return None
    title = torrent.title
    if not isinstance(title, str) or not title or len(title) > 1024 or not title.isprintable():
        return None
    link = torrent.page_url or torrent.enclosure
    if not link or not torrent.site or spec != snapshot["spec"]:
        return None
    token = hashlib.sha256(f"{torrent.site}\0{title}\0{link}".encode()).hexdigest()
    # Unverified pack contents cannot establish which files a downloader will select.
    return dict(token=token, group=group, spec=spec, episodes=sorted(set(episodes)),
                selectable=False, title=title)


def search_candidates(sub, snapshot, include_alternatives, allowed=lambda: True):
    from version import APP_VERSION
    from app.chain.media import MediaChain
    from app.chain.search import SearchChain
    from app.chain.subscribe import SubscribeChain
    from app.schemas.types import MediaType, SystemConfigKey

    owner, search = SubscribeChain(), SearchChain()
    media_id = snapshot["media_id"]
    if not allowed() or getattr(sub, "episode_group", None) or getattr(sub, "best_version", None):
        return []
    if APP_VERSION == "v3.2.9":
        from app.application.configuration import get_configured_system_config
        from app.application.subscription.sitebudget import SubscriptionSiteBudget
        from app.runtime.stop import runtime_stop_state
        settings = get_configured_system_config()
        recognition = dict(media_source="themoviedb", media_id=media_id)
        search.configure_subscription_site_budget(SubscriptionSiteBudget(
            repository=owner.subscription_search_repository, owner=f"viewing-assistant:{uuid4().hex}",
            cancelled=lambda: not allowed(), stop_state=runtime_stop_state))
    elif APP_VERSION == "v2.15.6":
        from app.db.systemconfig_oper import SystemConfigOper
        settings = SystemConfigOper()
        recognition = dict(source="themoviedb", mediaid=media_id, tmdbid=int(media_id))
    else:
        return []
    media = MediaChain().recognize_media(mtype=MediaType.TV, **recognition)
    if not _same_series(media, media_id, MediaType.TV):
        return []
    media = copy(media)
    if APP_VERSION == "v3.2.9":
        from app.chain.subscribe.metadata import apply_subscription_classification
        media = apply_subscription_classification(media, sub)
    media.season = snapshot["season"]
    sites = owner.get_sub_sites(sub)
    if sub.sites:
        sites = [site for site in sites if site in sub.sites]
        if not sites:
            return []
    if not allowed():
        return []
    contexts = search.process(
        mediainfo=media, keyword=sub.keyword, sites=sites,
        area="imdbid" if sub.search_imdbid and media.imdb_id else "title",
        custom_words=sub.custom_words.splitlines() if sub.custom_words else None,
        rule_groups=sub.filter_groups or settings.get(SystemConfigKey.SubscribeFilterRuleGroups) or [],
        filter_params=_filters(owner.get_params(sub), sub.include, snapshot, include_alternatives))
    found = []
    for context in contexts or []:
        offer = _offer(context, snapshot, MediaType.TV)
        if offer and (include_alternatives or offer["group"] == snapshot["group"]):
            if APP_VERSION == "v2.15.6" and sub.media_category:
                context.media_info = copy(context.media_info)
                context.media_info.category = sub.media_category
            found.append((offer, context))
    return found
