# 维护与发版

这个仓库同时提供源代码、插件市场索引和安装包，可单独克隆、修改、测试与发版。

1. 在 `plugins.v2/ptsubtitlefollowup/` 修改 Python，在 `frontend/` 修改界面。新增运行文件时同步审核 `tools/package-files.txt`。`plugins.v3/ptsubtitlefollowup/` 是安装回退用的生成副本，不直接编辑。
2. 同步更新插件类、`package.v2.json` 和 `package.v3.json` 中的版本及本版说明。运行 `python tools/build.py --write-ui --write-v3`，提交生成的前端入口和 V3 副本。两代宿主共用一个 ZIP，每个发布版本使用新版本号。
3. 运行 `python -m unittest discover -s tests`、`node --test tests/config_flow.cjs tests/federation.test.mjs tests/tonight.test.mjs tests/library-picker.test.mjs`、`python tools/build.py` 和 `python tools/build.py --verify`。构建会拒绝版本不一致或过期的 V3 副本。提交后 GitHub 会在 Linux、Windows 及 Python 3.12、3.14 上检查。
4. 在 GitHub 的 **Actions → Publish MoviePilot release → Run workflow** 选择 `main`。检查通过后会发布 ZIP 和 SHA-256。用户在 MoviePilot 的插件市场检查更新即可。同一发行仓后续更新无需重复向 Wiki 申请。

目前支持 MoviePilot V2 2.15.6 和 V3 3.2.9 至 3.2.14 的标准 Python、SQLite 部署。上游发布新的稳定补丁后，请比对订阅、补集和下载目录接口，更新 `fusion/host_versions.py` 的已验证版本和 `fusion/subscription_host.py` 的源码摘要。市场索引和构建检查中的版本区间应覆盖同一组补丁。

使用虚构数据和断网的隔离宿主验证加载与设置、管理员权限、电影与最新已开播季的订阅、去重、双额度、撤销和重载、字幕完成事件及逐集补缺换回。发布后用宿主市场核对升级候选。支持范围限于标准 Python 和 SQLite。

V2 升级 V3 后，宿主和下载目录的授权依据有变化，需要在插件中重新核对并确认自动订阅范围；同一 V2 宿主更新插件不会因此撤销已有授权。NAS 由维护者手动更新。

请勿将真实配置、站点凭证、观影记录、NAS 截图或调试数据库提交到此仓库。
