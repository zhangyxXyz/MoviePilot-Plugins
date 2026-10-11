# TheTVDB 探索 2.3.0

为 MoviePilot V3 提供 TVDB 电影、电视剧探索，以及原生媒体身份的详情识别。
支持默认季集编排、季简介、真实集号和按需单集翻译；翻译失败保留原文。
TVDB 热度写入 `popularity`，不作为十分制评分。

在插件配置中启用插件并填写可用的 TVDB API Key，按需启用宿主代理。
插件通过宿主 `RequestUtils` 请求 TVDB，不新增 Python 依赖。

## 接口与兼容性

- 探索：`/api/v1/plugin/TvdbDiscover/tvdb_discover`，沿用宿主 API Key 校验。
- 季集：`/api/v1/plugin/TvdbDiscover/tvdb_season/{series_id}/{season_number}`，
  使用宿主 bearer 认证，返回 `Response[MediaSeason]`。
- 详情识别只接管显式 `MediaSource.TVDB` 与有效媒体 ID，支持同步和异步调用。

要求 MoviePilot `>=3.1.2`。本次来源包仅包含插件 Python 源码和元数据，
不包含版本说明提到的宿主原生详情页兼容补丁。季集内容在宿主界面中的完整展示，
仍需前端支持上述插件接口；本仓库不修改宿主或前端。

源码和更新说明位于[当前仓库 dev 分支](https://github.com/zhangyxXyz/MoviePilot-Plugins/tree/dev/plugins.v3/tvdbdiscover)。
`main` 保留上游同步用途；宿主市场固定读取 `main`，推送 `dev` 不代表自动上架。

## 离线验证

在插件仓库根目录使用同级 MoviePilot 的锁定开发环境运行：

```sh
../MoviePilot/.venv/bin/python -m pytest tests/v3/tvdbdiscover -q
```

Windows 使用 `../MoviePilot/.venv/Scripts/python.exe`。测试由仓库引导隔离配置和
数据库，并在 HTTP 边界替换 TVDB 请求，覆盖分页、令牌刷新、缓存、来源身份、
默认季集编排、翻译回退及端点响应。
