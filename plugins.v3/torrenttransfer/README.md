# 自动转移做种

作者：seiun, jxxghp
仓库：https://github.com/zhangyxXyz/MoviePilot-Plugins

## 分类与标签

- 保留原分类：转入 qBittorrent 时保持原分类；qBittorrent 转入 Transmission 时将分类写入 `分类/原分类` 标签，例如 `分类/国产剧`。不修改下载目录。
- 保留原标签：开启后复制原标签；关闭则不复制。
- 仅保留这些原标签：支持英文或中文逗号分隔，按完整名称精确匹配，留空保留全部。仅在保留原标签开启时生效。
- 转移后新增标签：仍然添加，不受原标签筛选影响。
- 分类标签独立于原标签开关，并自动去除重复标签。

推荐保护标签：`H&R,保留,勿删`。若已有任务使用 `误删`，可同时加入。

这只影响之后转移的任务，不批量修改 Transmission 中已存在的任务。
下载器助手、IYUU 等插件仍可能另外添加站点或辅种标签。

V3 源码位于 `plugins.v3/torrenttransfer`，V2 同步更新位于 `plugins.v2/torrenttransfer`。
