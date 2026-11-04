# 维护与发版

这个仓库同时提供源代码、插件市场索引和安装包，可单独克隆、修改、测试与发版。

1. 在 `plugins.v2/ptsubtitlefollowup/` 修改 Python，在 `frontend/` 修改界面。新增运行文件时同步审核 `tools/package-files.txt`。
2. 更新插件类和 `package.v2.json` 中的版本，填写本版更新说明。界面有变动时，运行 `python tools/build.py --write-ui`，提交生成的前端入口。每个发布版本使用新版本号。
3. 运行 `python -m unittest discover -s tests`、`python tools/build.py` 和 `python tools/build.py --verify`。提交后 GitHub 会在 Linux 和 Windows 上检查。
4. 在 GitHub 的 **Actions → Publish MoviePilot release → Run workflow** 选择 `main`。检查通过后会发布 ZIP 和 SHA-256。用户在 MoviePilot 的插件市场检查更新即可。同一发行仓后续更新无需重复向 Wiki 申请。

目前支持 MoviePilot 2.15.6。自动订阅使用该宿主版本已验证的接口，上调兼容范围前要在目标宿主复验。请勿将真实配置、站点凭证、观影记录、NAS 截图或调试数据库提交到此仓库。
