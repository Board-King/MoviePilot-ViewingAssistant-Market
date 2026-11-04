# 观影助手

**按你的 Emby 口味，发现下一部想看的片。**

从电影、剧集和「近 7 天新发现」中选一部。点海报查看作品资料，点海报下的推荐依据，看看它和你的收藏、已看记录有哪些共同点。

## 安装

支持 **MoviePilot 2.15.6**。用管理员账号进入「插件 → 插件市场设置」，在现有仓库列表中追加以下地址并保存，再到插件市场搜索「观影助手」并安装。

```text
https://github.com/Board-King/MoviePilot-ViewingAssistant-Market
```

## 开始选片

1. 打开插件的「设置」，在「候选发现」选择榜单并开启更新。回到「查看数据」点「更新候选」，完成后即可浏览。
2. 想看更合口味的推荐，就选择 Emby 服务器、填写用户 ID，并明确选择允许读取的媒体库。保存后验证绑定并更新观影偏好。偏好在本机计算，管理员可以更改或停用。
3. 想让 MoviePilot 自动订阅时，在「自动订阅」选择 TMDB 热门电影、指定 MoviePilot 订阅用户和每日额度。保存后单独确认授权；新订阅可能触发搜索和下载。当前支持本地 SQLite 与 Emby，每个候选电影下载目录至少需有 100 GiB 可用空间。剧集可浏览。

**配套字幕**可按需开启。馒头新下载的影片入库后，插件会补查原发布页的中文字幕，保存到本地视频或 STRM 旁。已有字幕会保留；等待字幕不阻塞上云。

**赞赏自愿，当前全部功能免费。** 在插件底部点「赞赏」即可打开微信赞赏码。建议可在微信赞赏时留言，我会看，不保证采纳。

## 鸣谢

<a href="https://www.themoviedb.org"><img src="assets/tmdb-blue-short.svg" alt="The Movie Database (TMDB)" width="100"></a>

This product uses the TMDB API but is not endorsed or certified by TMDB.

豆瓣待播剧集来源使用 RSSHub 提供的公开窗口。作品海报和资料随来源展示。
