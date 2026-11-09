# P1 任务执行与收费安全合同

本分支只在隔离工作树开发和测试，未部署生产。P1 运行入口默认自动执行，收费由持久化意图和双层预算控制。`state/budget-policy.json` 缺失、无效或任一当前操作所需预算未配置时，禁止新的收费提交；已有任务的查询、只读对账、下载恢复和交付不检查新预算。示例见根目录 `budget-policy.example.json`，所有额度、时区及日界线保持 null。

## 身份与存储

| 字段 | 唯一含义 / 来源 |
|---|---|
| clientOperationId | 调用者重试身份；现有 Dreama HTTP Idempotency-Key 对应原始 jobs.key |
| beefTaskId | BeefTV 原生任务身份；旧调用没有传入时保留 null |
| providerRequestId | Provider 请求身份；没有原始字段则保留 null |
| bridgeJobId | Bridge 自己的任务身份 |
| upstreamTaskId | 经有效回执或只读对账确认的上游 ID；不从分镜 ID 推算 |
| resourceId | BeefTV 入库后的资源身份，P1 不代填 |
| sourceNodeId / artifactId | 原节点身份 / 产物身份，未知时不编造 |
| outputIndex / outputCount | 批量产物的位置与数量；媒体列表确定后更新数量 |

`task_contracts.py` 使用本地 JSON Schema Draft 2020-12 文件校验，不下载 Schema 或调用网络。媒体批次必须归属一个任务、输出序号完整且唯一、artifactId 唯一。ACK 校验任务/产物/输出序号/哈希/大小，并要求带时区的时间。P1 不提供 ACK HTTP 入口或清理 Worker，不删除未 ACK 媒体。

同一 Bridge 的数据库包含原始 `jobs/preparation_requests` 和新增表：

- `p1_tasks`：身份、输入摘要、合同状态、修订号、是否历史任务。
- `p1_events`：原子状态事件；相同状态重放不增加修订号。
- `p1_submissions`：唯一收费意图、报价/预留/实际积分、结算和日界线快照。
- `p1_legacy_records`：迁移时的原始行及其摘要，保持历史输入可读。
- `p1_schema`：新增合同的迁移版本。

状态转换的唯一来源是 `schemas/task-state.schema.json`。更新使用 SQLite 比较修订号与短事务。旧 Provider JSON 状态作为兼容投影保留；例如本地生成完成映射 `artifact_ready`，不能直接声称 BeefTV 已 ACK 的 `completed`。旧取消历史映射 blocked，上游确证的取消仍保留原始状态和查询结果。

## 提交顺序与未知回执

准备完成 → 核验原任务、绑定、模型和素材 → 获取报价/估算 → 校验预算 → SQLite `BEGIN IMMEDIATE` 检查并预留 → 写入唯一 submission_intent 并原子更新 Provider JSON → 事务提交 → 调用上游 → 保存回执 → 查询/下载。

所有五类流程通过 `SubmissionService`。数据库写事务不跨网络；同一进程使用每任务 RLock，不同进程依靠 SQLite 唯一意图和事务竞争。未知、超时、进程中断、空回执及回执持久化失败均不释放预留，不自动重提。重启后若尚无意图，仅只读预检被中断的任务可重新核验；已有意图只交给查询恢复路径。

新任务不生成 approvedAt、approvedPoints 或 autoExecuteAt。原有 approve HTTP 请求形状暂作兼容包装，也必须经过同一预算入口；它不是收费旁路。新的自动入口不需要人工积分确认。Dreama 预提交取消在引擎和 HTTP 路由均拒绝，Codex 取消能力没有修改。旧控制台 UI 在 P3 收敛，不能据本分支宣称 UI 已迁移。

## 金额与预算

金额以非负十进制字符串入库，使用 Decimal 和精度随输入增长的求和，避免浮点累计误差。未知金额为 null，不能默认为零。预算支持用户将来提供的数字或十进制字符串，布尔值、NaN、Infinity、负数及不完整配置均拒绝。

日预算采用用户明确配置的 IANA 时区和 HH:MM 日界线。DST 重复或不存在的边界拒绝收费。每个意图冻结策略与时间窗；策略变更不能回写既有意图。未结算的预留跨日继续占额；没有可靠金额的历史收费意图阻断新的收费提交。

| 路径 | billingSource | quotedPoints |
|---|---|---|
| 普通视频、图片 | cli_quote | 当前 CLI 报价 |
| 视频延长 | compat_quote | 现有兼容报价；P2 隔离内部 HTTP |
| 增强、独立音频 | estimated | null，本地估算放 estimatedPoints / reservedPoints |

实际积分只接受明确结算证据，不能根据成功状态或报价推算。实际与预留不同标记 variance，并按实际积分计入适用的日预算，后续超额会被阻断。释放预留必须有明确未受理、未收费证据；释放不会把同一旧意图变回可提交状态。

历史 queued/processing/download_failed 和带提交证据的 completed/failed/cancelled 同样保留未结算占额，不能因生成终态便假定未收费或已经结算。历史报价可确定时保守预留；金额或真实提交时间无法确定时阻断新收费，准备创建时间不能冒充提交时间。这一阻断不影响旧任务查询和交付。

## 迁移与降级

迁移只追加表、保存原始行，不删除任务或清空准备请求。Dreama 的 user_version 从 2 追加到 3，使旧执行器拒绝写入新账本。P1 自身支持到 3，新增合同支持到 1，遇到更高版本拒绝降级。Codex 仅在独立离线副本上验证同一身份/历史导入合同，保持其原 user_version；P4 才接入其运行服务。

回滚先封存新增任务和意图，再恢复升级前的完整数据库副本并对账新增记录。不能仅用旧程序打开新数据库，不能删 WAL/SHM 以规避版本检查。P1 演练已经实际证明冻结的旧 Dreama 构造器会拒绝迁移副本。

## 阶段边界与验证

CLI Client 的完整拆分与兼容 HTTP 隔离留到 P2；BeefTV 原生任务/插件接线和 UI 收敛留到 P3；Codex 运行服务修改留到 P4；真实 ACK、清理和绑定恢复留到 P5。P1 代码不读取另一 Bridge 的生产数据库，也不操作 BeefTV 画布库。

测试内的已配置额度是显式合成 fixture，位于 tests/fixtures_budget.py 和 tests/test_task_repository.py，不是运行默认值。验收使用仓库原有离线审计钩子及额外外网/CLI/凭据保护，所有临时文件在 D:\Codex。真实收费测试未执行。
