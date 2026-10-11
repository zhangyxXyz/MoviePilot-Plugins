# 种子清理 0.5.5

作者：seiun
仓库：https://github.com/zhangyxXyz/MoviePilot-Plugins

MoviePilot V3 插件，按下载器和站点配置清理任务，支持下载错误筛选、站点失效确认、持续观察、H&R 保护、执行前复核及结果通知。侧栏入口：整理 → 种子清理。

0.5.5 将插件名称和工作台标题统一为“种子清理”，已有插件 ID 和配置保持兼容。

## 开发与验证

源码与构建产物位于 `plugins.v3/torrentcleanup/`，后端和前端测试集中在
`tests/v3/torrentcleanup/`。市场索引为仓库根目录的 `package.v3.json`。
使用同级 MoviePilot 的锁定开发环境，仓库测试引导会隔离配置、数据库并阻止外部网络。

在插件仓库根目录执行：

```sh
../MoviePilot/.venv/bin/python -m pytest tests/v3/torrentcleanup -q
cd plugins.v3/torrentcleanup
npm ci
npm test
npm run build
```

Windows 将解释器路径换为 `../MoviePilot/.venv/Scripts/python.exe`。
前端预览使用 `npm run dev -- --port 5177`，另一个终端运行 `npm run test:ui`；
仅使用离线示例数据，截图保存到系统临时目录或 `TORRENTCLEANUP_SCREENSHOTS` 指定目录。
首次运行页面测试前需执行 `npx playwright install chromium --only-shell`。

合入时修正了 Windows 本地绝对路径映射；下载器路径仍按 POSIX 语义解析，
文件越界、大小不符和符号链接检查保持有效。联邦构建只携带插件自身样式，
Vuetify 基础样式由宿主提供。

当前源码发布在 [dev 分支](https://github.com/zhangyxXyz/MoviePilot-Plugins/tree/dev/plugins.v3/torrentcleanup)。
`main` 保留上游同步用途；宿主市场固定读取 `main` 索引，推送 `dev` 不代表市场已经自动上架。

## 使用说明

首次安装默认关闭自动执行，先配置站点适配、保护要求和下载器任务，检查预览后再启用。不同条件组全部满足才处理，同组状态、关键词和分类任一匹配，必需标签全部匹配，保护标签任一匹配即保留。观察期、次数和间隔同时满足。多 Tracker 种子的所有关联站点都必须在范围内，且在启用失效核验时全部确认失效。

正常 Tracker 不周期性访问站点。明确失效时进一步核验，网络错误、认证失败和搜索无结果不判死种。身份关联与存在性缓存分离，站点配额跨任务共享。凭据复用 MoviePilot 站点管理，不在此包中提供账号配置。

执行通知通过宿主通知渠道发送，列出具体任务及结果。删除文件需通过跨下载器引用检查。仅删除任务不会删除文件。馒头未知删除错误码仍保留，不据不明 API 错误判定失效。

本包不包含本机安装记录、配置备份、运行数据、截图、凭据、node_modules 或缓存。
