# 双 Bridge 发行管理

在隔离工作树完成比较、构建、校验和安装回退演练。把官方候选、已选官方基线、定制提交、发行制品和生产记录分别保留。生产变更走独立授权。

## 官方候选

把官方 tag 获取到独立 ref，先从官方仓库核对 tag 指向，再用 `scripts/release_manager.py compare`。命令接收 `--repository`、`--selected`、`--candidate`、`--expect-candidate`、`--customization` 和 `--production`。输出每个变更文件的三方比较，以及 Git 实际报告的合并冲突。插件宿主、SDK、清单和协议归入独立核对清单。

`adopt` 表示只在官方侧变更，`retain` 表示只在定制侧变更，`retire-duplicate` 表示两侧得到相同文件或同样删除。两侧均改动的文件分别标记 `conflict` 和 `review-overlap`。这些分类用于审查，选定上游仍需独立协议移植与完整回归。

## Windows 发行

1. 用仓库的原生 Windows 构建脚本构建干净的定制提交。构建成功后记录源码提交、`VERSION` 和全部输出文件摘要。发行管理器的 `record_build` 为构建驱动生成该记录。
2. 以 `scripts/release_manager.py package` 打包，指定 `--repository`、`--bridge`、`--codex`、`--build`、`--build-receipt`、`--output`、`--tag` 和 `--customization`。三个仓库需处于干净状态，Bridge 发布源码直接从提交读取。包内包含桌面、两个 Bridge、两个自定义插件及 `release.json`。
3. 单独保留命令输出的 `manifestSha256`。通过 `release_manager.py verify --package <目录> --manifest-sha256 <摘要>` 校验包中的全部文件。
4. 只在隔离目标和数据副本运行 `scripts/dreama_release.py install` 与 `rollback` 演练。安装 CLI 必须传独立记录的 `--manifest-sha256`。双 Bridge 包会检查运行状态和账本版本，备份旧文件及 SQLite。回滚核对当前发行和完整备份，保留现有账本。

如果应用或 Bridge 启动后迁移了数据库，而旧发行的读取版本低于当前账本，回滚会拒绝替换代码。保留数据库和运行记录，使用与当前账本兼容的恢复发行。
