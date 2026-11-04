# 已完成视频仍显示生成中

## 实际问题

视频任务 `d40e79a2e1233fde57b783f69616045d` 已于 2026-11-01 13:34:09 完成，产物已绑定到画布节点。任务详情接口返回 `succeeded / ready / result_ready`，但节点保存的是 `status=success`、`taskStatus=succeeded` 与旧的 `taskOutcome.phase=running`。节点有同任务的绑定 effect，完成时间却为空，更新时间仍停在完成前一次轮询。

界面优先读取明确的 taskOutcome，因此即使本地生成请求已结束，按钮仍显示生成中。常规恢复扫描又跳过已有视频的成功节点，单纯刷新无法修复。

## 修复范围

- 服务端验证任务产物和资源就绪后，绑定结果时同时写入权威任务 outcome、状态、进度、阶段及时间，避免成功的画布回执覆盖前端终态。
- 对已有同任务绑定结果、但残留 running/fetching 的成功节点，打开画布时只读查询原任务。只有原任务确认 ready 且产物身份匹配时，刷新任务元数据，不重复消费或绑定旧回执。
- 保留任务身份、画布归属和产物就绪校验。旧视频仍在、但新任务尚未完成时，不解除生成中状态。

## 验证与部署

回归测试使用隔离的 Go harness 与前端内存节点，不调用视频生成。前端测试覆盖实际矛盾状态、旧视频与新任务、缺少已落盘产物、任务身份变化和异步写回。部署前确认无进行中任务并正常关闭应用，备份程序、模型配置及数据库，只替换本次编译的 BeefTV.exe。

部署回执：`D:\BeefTV-Codex-UI-tools\completed-status-deployment.json`。不重跑已完成的视频，不修改其提示词、音色、媒体文件或连线。

## 本机验收记录

- 已安装 `codex-ui.4-completed-status`，EXE SHA-256 为 `8313678ae2ef1ea79338a35b6ec12503e561d8807a0f1ba81ebd267b9fce421d`。
- 37 个相关前端测试及 TypeScript 检查通过；三个 Go 包的绑定、状态回归测试通过，整体 EXE 编译成功。
- 已正常关闭并重启 BeefTV。桌面工具能够读取首页，但点击画布入口因坐标不可用失败，未把这一步记为实际界面验收。
- 因无法自动点击入口，使用已登记客户端读取原任务及完整画布，再通过应用正式 `canvas.document.commit` 接口进行一次 revision CAS 对账。仅更新目标节点的五个任务状态/时间字段，画布 revision 从 551 到 552；未直接写数据库。
- 只读回验确认 `ready / allowed / result_ready`、原完成时间、视频及提示词、连线、节点和模型配置保留；任务 ID 集合仍为原 13 条，没有新增生成。
- 对账回执：`D:\BeefTV-Codex-UI-tools\completed-status-reconciliation.json`；验收结果：`D:\BeefTV-Codex-UI-tools\completed-status-verification.json`。自动打开画布恢复路径已做行为测试，实际这条记录采用上述正式接口修复。
