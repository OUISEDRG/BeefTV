# Dreama 积分预算和结算

状态目录的 `budget-policy.json` 采用 `schemas/budget.schema.json`。每日上限、五种操作上限、时区和日界线由用户配置。示例中的 `null` 保持未配置状态。未配置的操作无法新提交。已有作业的状态、原回执取回和媒体交付可继续处理。

每次收费传输前重新核对原报价、原任务和已提交的 SQLite 预留。预算或日界线发生变化且传输尚未开始时，保留任务历史并释放预留。传输已开始后，超时、未知回执和普通任务终态均保留预留，等待对应收费凭据。

在获得原作业的官方计费凭据并核对其中的任务归属后，可将一条证据登记到已迁移的账本。命令只访问传入的本机文件和 SQLite。执行命令会修改指定账本的计费记录，需要在对应 Bridge 停止且任务 Worker 退出后运行。独占锁会拒绝与正在运行的 Bridge 同时修改账本。

```powershell
python -B reconcile_billing.py --state <existing-state-directory> --evidence <verified-evidence.json>
```

证据遵守 `schemas/billing-evidence.schema.json`。每条 `reference` 唯一指向已核对的官方计费明细。保存证据引用时只放可追溯的标识，原账单内容、凭证和链接留在各自受控位置。

- `charge_confirmed` 需要原 Bridge 作业 ID、原预留意图 ID、持久化的原上游任务 ID，以及十进制字符串 `actualPoints`。
- `rejected_without_charge` 需要同一个作业与意图 ID、`upstreamTaskId: null`，以及官方证据明确确认的 `accepted: false` 和 `charged: false`。

命令按原报价或估算预留核对差额，并按原预算策略检查单次和当日累计超额。原策略或历史金额无法核对时，审计保留该结论。重复导入同一份证据返回原审计，冲突的凭据保持待核对。已结算实际积分会进入后续预算检查。
