# <img src="icons/viewing-assistant.png" alt="" width="40" height="40" align="absmiddle"> 观影助手

**每天按你的 Emby 口味，自动订阅最多 3 部电影和 1 部电视剧。**

助手从已绑定账号的收藏和已看记录学习题材，按这些题材到 TMDB 找片。电影和电视剧分别按匹配度挑选。打开看板就能看到今天订到了什么、哪一季，以及推荐理由。

## 安装

支持 **MoviePilot 2.15.6**。用管理员账号打开「插件市场设置」，点击 **同步 Wiki**，回到插件市场搜索 **观影助手** 并安装。本插件源已由 MoviePilot Wiki 收录。[收录记录](https://github.com/jxxghp/MoviePilot-Wiki/pull/47)

也可以在市场设置中手动追加仓库地址。

```text
https://github.com/Board-King/MoviePilot-ViewingAssistant-Market
```

## 开启每日自动发现

1. 在「观影偏好」选择自己的 Emby 用户和媒体库，验证一次绑定。已有绑定可以直接用。
2. 在「自动订阅」选择「按观影口味 · 电影和剧集」，核对 MoviePilot 的订阅用户和每日额度。电影默认 3 部，剧集默认 1 部。多季剧只订最新已开播的普通季，特别篇和待播季跳过。
3. 保存后在同一窗口确认授权。此后 MoviePilot 定时发现和订阅，并可按宿主设置搜索和下载。旧版电影策略可继续使用，切换时需确认新增的剧集范围。

每天按北京时间分别结算。已有影片、已订或已经有该季正片的剧集会顺延。有些 Emby 剧集缺少 TMDB 编号时，同季查重可能无法完成。偏好证据不足、季号不明、无法查重或下载空间不足时，实际订阅数可以少于额度。支持本地 SQLite 与 Emby，下载目录至少需要 100 GiB 可用空间。电影缺少媒体编号且以别名入库时，仍有重复的可能。

## 其他功能

**先看自己的片库。** 在 MoviePilot 管理员的插件看板中使用本人已授权的 Emby 账号，已验证的绑定可直接复用。首次绑定按引导选择服务器、账号和允许读取的媒体库，验证后返回。个人选片不需要开自动订阅。助手按需从近期未看影片中最多检查 96 条，已有可用偏好时按口味排序。字幕筛选使用 Emby 已识别的非强制中文轨，多版本影片只有在各版本均满足条件时才通过。没有符合条件的影片，可以一键放宽条件。

「留到下次」与「不感兴趣」记录保存在本机，按 Emby 服务器、用户和授权库范围分开。它们用于今晚的库内片单，不会从一次跳过推断整个题材的偏好。库外候选和自动订阅继续使用各自的规则。再次打开留下的片单时会重新核对当前访问权限与影片信息。

**浏览候选。** 在看板按电影、剧集或近七天新发现筛选，点海报下的匹配分可查看依据。偏好在本机计算，向 TMDB 查找个人片源时只用题材编号。

**配套字幕**可按需开启。馒头新下载的影片入库后，插件会补查原发布页的中文字幕，保存到本地视频或 STRM 旁。已有字幕会保留；等待字幕不阻塞上云。

**赞赏自愿，当前全部功能免费。** 在插件底部点「赞赏」即可打开微信赞赏码。建议可在微信赞赏时留言，我会看，不保证采纳。

## 鸣谢

<a href="https://www.themoviedb.org"><img src="assets/tmdb-blue-short.svg" alt="The Movie Database (TMDB)" width="100"></a>

This product uses the TMDB API but is not endorsed or certified by TMDB.

豆瓣待播剧集来源使用 RSSHub 提供的公开窗口。作品海报和资料随来源展示。

[版本下载](https://github.com/Board-King/MoviePilot-ViewingAssistant-Market/releases) · [进化路线](ROADMAP.md) · [市场调研](docs/market-research.md) · [维护与发版](MAINTAINING.md)
