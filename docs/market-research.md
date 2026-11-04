# 市场调研 · 2026-11-04

**研究问题**　为什么已经有 Emby 和 MoviePilot 的人还要安装观影助手，谁会成为第二个使用它的人，又有什么理由让下一户 NAS 主人安装？

本次是公开资料桌面研究，核对了作者仓库、上游文档、用户原帖及本仓发行数据。未开展中文家庭访谈。项目星数反映对相邻品类的关注；个别用户的经历用于形成待验证需求。不能从中推算中国自建媒体家庭的数量或本插件的留存率。

## 已经有人解决了什么

| 对照 | 已公开的能力 | 对产品选择的影响 |
| --- | --- | --- |
| SuggestArr | 根据 Plex、Jellyfin 或 Emby 的观看历史发现内容并向请求系统提出请求。调研时 GitHub 为 1,376 星 | 单做「按口味自动找片」很容易与成熟替代品撞车。[作者仓库](https://github.com/giuseppe99barchetta/SuggestArr) |
| YiMao、EmbyPulse | 中文方案已覆盖用 Telegram 求片、跟踪 MP 处理及到库消息，或让 Emby 用户提请求、管理员审批并跟踪状态 | 「求片到入库」已有直接同栈对手。先检验一个不同的入口：已有 MP 的家庭只装插件，普通成员从手机打开，无需另部署一套求片服务或使用 Telegram。[YiMao](https://github.com/xzb177/YiMao) · [EmbyPulse](https://github.com/zeyu8023/emby-pulse) |
| Swiparr | 自托管多人选片、手机投票和匹配。调研时 GitHub 为 803 星 | 投票有现成替代品。我们的原型应和它对比从“开始商量”到“在自家库里真播起来”的用时。[作者仓库](https://github.com/m3sserstudi0s/swiparr) |
| Emby Companion | macOS 客户端已把库内未看选片、作品状态和 MoviePilot 订阅接到同一处 | 跨服务联动已有人做。普通成员的手机入口、可核实的中文字幕与真实开播是更具体的试验。[作者说明](https://github.com/ffinly/emby-companion) |
| MoviePilot 原生插件生态 | 用户已经能从豆瓣想看同步订阅，也能订阅榜单；V3 有独立版本与镜像边界 | 榜单继续作为输入。若只支持 MP 2.15.6，潜在用户即使被演示吸引，也可能无法安装。[想看同步](https://github.com/jxxghp/MoviePilot-Plugins/tree/main/plugins.v2/doubansync) · [榜单订阅](https://github.com/jxxghp/MoviePilot-Plugins/tree/main/plugins.v2/doubanrank) · [V3 说明](https://github.com/jxxghp/MoviePilot-Wiki/blob/main/v3.md) |

## 用户在哪里遇到麻烦

- **主动求片和真正观看之间有落差。** 一位 Seerr 管理员描述家人经常点片、最终很少看，并希望关联请求与播放。这支持用“到库后有没有看”衡量价值。另一个讨论直接提出按个人历史给推荐，回复中已有替代工具。[请求后的观看](https://github.com/seerr-team/seerr/discussions/1813) · [个人推荐需求](https://github.com/seerr-team/seerr/discussions/2565)
- **多人决定看哪部，本身就耗时间。** 一个每周办电影夜的家庭讲到成员常为选片争论，征求手机投票方案；回复里有人提出局域网扫码加入。这是具体场景，但这组参与者不能代表中文 MP 家庭。[用户原帖](https://www.reddit.com/r/selfhosted/comments/1ob2kml/suggestions_self_hosted_app_for_family_movie/)
- **本项目自身的独特线索是中文字幕和本地可用性。** 0.6.5 已能按 Emby 偏好排名，区分电影查重结果，并为馒头新入库影片补查同一发布页字幕，包括本地 STRM 侧车。下一版要验证有多少家庭真的会用字幕与时长来决定今晚看哪部，以及这种选择是否比原有 Emby 浏览方式更快。[当前发行版](https://github.com/Board-King/MoviePilot-ViewingAssistant-Market/releases/tag/PtSubtitleFollowup_v0.6.5)

## 三条方向如何选

| 方向 | 谁得到价值 | 取舍 |
| --- | --- | --- |
| 更强的自动囤片或更多榜单 | 已明确要自动订阅的管理员 | 与现有插件重叠，下载更多也可能让选择更难。保留为库外内容的供给能力 |
| 运营大屏、周报、求片门户 | 服主 | EmbyPulse、YiMao 已有实际方案。这些能力可在用户需要时接入，暂不投入第二套控制台 |
| 从今晚能看的电影开始，到共同选片、开播和下次备片 | 管理员、独自观影的人及家人 | **优先验证。** 这个组合利用了现成的 Emby、MP、STRM 和字幕能力，也能让一个从未打开 MP 的家庭成员感受到价值。最大风险是入口不方便、字幕元数据不全，或家庭共选频率低 |

## 市场起点与获取试用

本仓 0.6.5 正式 ZIP 在调研时 GitHub 计数为 **2 次下载**，其中可能含验收下载；独立安装数和活跃家庭数未知。当日第三方源 [Wiki 收录申请 #47](https://github.com/jxxghp/MoviePilot-Wiki/pull/47) 仍在审核。用户现在可以手动加源，收录后也要经过相应的同步流程。官方项目对在国内平台宣传 MoviePilot 有明确提示，试用以自有 GitHub 演示、允许的插件源、自愿报名和已有用户转介为主。[发行数据接口](https://api.github.com/repos/Board-King/MoviePilot-ViewingAssistant-Market/releases/tags/PtSubtitleFollowup_v0.6.5) · [市场说明](https://wiki.movie-pilot.org/plugin) · [上游 V2 项目说明](https://github.com/jxxghp/MoviePilot/blob/v2/README.md)

第一批试用要同时听管理员与实际观众的意见，记录原来的选片办法、过去一周共同观影次数、装好后谁第一次使用、最终播放哪一步失败。公开报告只用匿名汇总和虚构截图。后续阶段、目标样本数与改变方向的条件见[进化路线](../ROADMAP.md)。

**调研后的排期决定**　以上家庭共选需求来自定性材料。维护者选择先验证个人库内选片：10 位试用者中有多少人真正通过推荐开播，第二周是否自行再用。家庭共选留到个人复用成立后再招募验证。[个人阶段规格](movie-night.md)
