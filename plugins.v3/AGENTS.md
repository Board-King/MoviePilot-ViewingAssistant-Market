# V3 安装镜像

此目录的 `ptsubtitlefollowup/` 由 `tools/build.py --write-v3` 自动生成，仅用于 MoviePilot V3 的源码安装和 Release 下载失败后的回退。

只修改 `plugins.v2/ptsubtitlefollowup/` 的公共源码及 `frontend/`，然后运行 `python tools/build.py --write-ui --write-v3` 并提交镜像。不要单独修改这里的运行文件；CI 会验证两代安装内容一致。此说明不进入插件安装包。
