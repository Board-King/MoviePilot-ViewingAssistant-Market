"""Source contracts shared by scheduling and UI; no network/client imports."""

SOURCE_SPECS = {
    "tmdb_personal_movie": {
        "id": "tmdb_personal_movie", "label": "按口味发现电影", "media_type": "movie",
        "provider": "themoviedb", "group": "tmdb_trending", "pagination": "numbered",
        "transport": "tmdb", "throttle_group": "tmdb_api", "personal": True,
    },
    "tmdb_personal_tv": {
        "id": "tmdb_personal_tv", "label": "按口味发现剧集", "media_type": "tv",
        "provider": "themoviedb", "group": "tmdb_trending", "pagination": "numbered",
        "transport": "tmdb", "throttle_group": "tmdb_api", "personal": True,
    },
    "tmdb_popular_movie": {
        "id": "tmdb_popular_movie", "label": "TMDB 热门电影", "media_type": "movie",
        "provider": "themoviedb", "group": "tmdb_trending", "pagination": "numbered",
        "transport": "tmdb", "throttle_group": "tmdb_api",
    },
    "tmdb_popular_tv": {
        "id": "tmdb_popular_tv", "label": "TMDB 热门剧集", "media_type": "tv",
        "provider": "themoviedb", "group": "tmdb_trending", "pagination": "numbered",
        "transport": "tmdb", "throttle_group": "tmdb_api",
    },
    "douban_tv_upcoming": {
        "id": "douban_tv_upcoming", "label": "豆瓣待播剧集", "media_type": "tv",
        "provider": "douban", "group": "douban_trending", "pagination": "snapshot",
        "transport": "upcoming_rss", "throttle_group": "rsshub_ddsrem", "max_received": 200,
        "description": "原待播插件使用的公开 RSSHub 单份窗口，无个人账号，不代表完整待播榜。",
    },
    "mp_followed_shares": {
        "id": "mp_followed_shares", "label": "关注分享", "media_types": ("movie", "tv"),
        "providers": ("themoviedb", "douban"), "group": "mp_shares", "pagination": "filtered_pages",
        "transport": "mp_shares", "throttle_group": "moviepilot_api",
    },
}
