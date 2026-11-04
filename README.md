# <img src="icons/viewing-assistant.png" alt="" width="40" height="40" align="absmiddle"> 观影助手

**按你的 Emby 口味，发现下一部想看的片。**

今晚想看点什么？打开「今晚看什么」，点 **帮我挑片**。助手从你已授权的 Emby 片库里挑出最多三部未看电影。时间紧，或特别需要中文字幕，展开「筛选」即可。选中后点 **在 Emby 打开** 进入对应作品。

## 安装

支持 **MoviePilot 2.15.6**。用管理员账号进入「插件 → 插件市场设置」，在现有仓库列表中追加以下地址并保存，再到插件市场搜索「观影助手」并安装。

```text
https://github.com/Board-King/MoviePilot-ViewingAssistant-Market
```

## 开始选片

**先看自己的片库。** 在 MoviePilot 管理员的插件看板中使用本人已授权的 Emby 账号，已验证的绑定可直接复用。首次绑定按引导选择服务器、账号和允许读取的媒体库，验证后返回。个人选片不需要开自动订阅。助手按需从近期未看影片中最多检查 96 条，已有可用偏好时按口味排序。字幕筛选使用 Emby 已识别的非强制中文轨，多版本影片只有在各版本均满足条件时才通过。没有符合条件的影片，可以一键放宽条件。

**也可以发现库外新片。**

1. 打开插件的「设置」，在「候选发现」选择榜单并开启更新。回到「查看数据」点「更新候选」，完成后即可浏览。
2. 已有个人绑定时，推荐也会使用该用户在授权库中的收藏与已看偏好。偏好在本机计算，管理员可以更改或停用。
3. 在「自动订阅」选「按匹配分」，指定 MoviePilot 用户和每天的部数，保存后确认授权。插件从 TMDB 热门电影中按个人口味选片，每天最多 3 部，遇到已有记录或疑似重复会顺延。新订阅可能触发 MoviePilot 搜索和下载。原先选了「均衡」或「严选」的用户可在看板点「改用匹配分」切换。剧集可浏览。

「按匹配分」查重时会核对影片编号，也会检查 Emby 中缺少编号的影片。未识别的影片如果以其他片名入库，仍可能重复。自动订阅支持本地 SQLite 与 Emby，电影下载目录至少需要 100 GiB 可用空间。

**配套字幕**可按需开启。馒头新下载的影片入库后，插件会补查原发布页的中文字幕，保存到本地视频或 STRM 旁。已有字幕会保留；等待字幕不阻塞上云。

**赞赏自愿，当前全部功能免费。** 在插件底部点「赞赏」即可打开微信赞赏码。建议可在微信赞赏时留言，我会看，不保证采纳。

## 鸣谢

<a href="https://www.themoviedb.org"><img src="assets/tmdb-blue-short.svg" alt="The Movie Database (TMDB)" width="100"></a>

This product uses the TMDB API but is not endorsed or certified by TMDB.

豆瓣待播剧集来源使用 RSSHub 提供的公开窗口。作品海报和资料随来源展示。

[版本下载](https://github.com/Board-King/MoviePilot-ViewingAssistant-Market/releases) · [进化路线](ROADMAP.md) · [市场调研](docs/market-research.md) · [维护与发版](MAINTAINING.md)
