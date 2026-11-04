# 剧梦视频增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 视频节点选择剧梦增强A/B/C，源视频预检、逐任务积分确认、实际提交与原节点回传。
**Architecture:** 新enhancement模块实现独立输入/HTTP网关，共用SQLite账本、确认和结果缓存；通过任务kind分流，不改BeefTV二进制。
**Tech Stack:** Python3.14、requests、ffprobe/ffmpeg、BeefTV协议渠道。
**Spec:** ../specs/2026-11-01-video-enhancement-design.md

## Global Constraints

- 不执行付费增强提交测试；user确认以前只有上传/只读/报价。
- 四个增强选项与原22个视频模型分开配置，原文保留规则不变。
- 一条MP4≤40MiB、≤120秒；C≤30fps。按真实时长ceil计费。
- 只读官方API已验证；不猜未声明参数，不重试未知收费POST。
- 本目录无Git，沿用独立源码及备份，不创建空Git仓库或触碰短剧工作流。

## Review Focus

- 原视频已含声音而增强结果无音轨：无损补回原音轨并验证文件。
- 节点遗留5秒/9:16等参数：不裁剪或改画幅，以真实源文件元数据计费。
- 同时点确认或超时：只允许一次POST，重启通过clientRequestId对账。
- 结果任务ID或账户不符：拒绝绑定，不下载别的任务成果。
- 大文件/伪MP4/错误视频数：写远端前拒绝，原视频不修改。

### Task 1: 增强输入与官方网关
**Files:** enhancement.py; tests/test_enhancement.py
**Interfaces:** enhancement_models()->dict; validate_enhancement_request(data)->bytes; probe_video(path)->dict; EnhancementGateway.upload_video(path,meta), identity(), counts(), submit_video(job), task_status(id), find_task(job).
- [ ] 写测试：四种模型、一个视频校验、忽略节点时长、真实ffprobe读取、超限与C帧率、任务结果解析及幂等对账。
- [ ] 跑单测观察失败，实现最小代码，再跑至通过。收费HTTP以模拟transport验证参数，不访问真实POST。

### Task 2: 持久任务与共享结果回传
**Files:** enhancement.py; generation.py; tests/test_enhancement.py
**Interfaces:** EnhancementFlow(bridge, gateway=None).prepare(data,key), approve/cancel/poll继承Generation；Generation新增valid_task_id、reconcile_task_id、finalize_video三个扩展点，默认行为不变。
- [ ] 写测试：准备不提交、费用确认一次、UUID任务、回执不明恢复、成功结果、缺失音轨补回。
- [ ] 实现增强任务独立kind、源文件哈希缓存、身份/报价冻结、状态规范化；35项已有回归保持通过。

### Task 3: HTTP/任务页/模型渠道
**Files:** server.py; console.js; console.html; install_enhancement.py; tests/test_enhancement_http.py
**Interfaces:** /v1/videos按model分流；/v1/models返回22+4；/v1/jobs按kind显示源视频信息；已有approve/cancel/media接口复用。
- [ ] 写真实本地HTTP测试：一个内联视频到prepared、无收费、确认后模拟完成URL/Range、匿名重复拒绝。
- [ ] 新渠道四个profile：maxVideos1，maxImages0，maxAudios0，inline video，durationSupported false，空ratios/resolutions，reference_to_video；不改原渠道默认。
- [ ] 显示增强方式、源时长/尺寸、计费秒数及预估积分，提示词不参与增强。

### Task 4: 安装与核验
**Files:** README.md; config.binding.json; evidence/enhancement-*.json; progress.md
- [ ] 确认BeefTV画布已保存、无提交中操作，正常关闭后备份并增量安装渠道。
- [ ] 重启服务/BeefTV，核对四个增强profile与模型目录，UI能选择视频增强模型。
- [ ] 全套测试和本机JS语法检查、官方网关只读查询；不点费用确认。
- [ ] 独立最终审查，一次修复重要问题；交付实际限制和操作步骤。

## Completion record

Tasks 1–3 implemented and verified; task 4 deployment and independent review complete. Final 53 tests pass, console.js syntax passes, production read-only enhancement task lookup passes, no paid enhancement POST performed. Profile/channel configuration survives BeefTV restart with the prior default and all 22 generation models intact.

Plan adjustment: separate model directories were necessary to prevent channel refresh cross-imports: /v1/models=22 generation, /enhancement/v1/models=4 enhancement. Generation also gained query_task_status/check_ready hooks for exact UUID/source checking. Source audio is always mapped losslessly when present, including upstream silent-track cases; -shortest is intentionally absent.

Native canvas UI acceptance remains unverified: click failed with coordinate input geometry is unavailable, recovery screenshot timed out. Text state confirmed saved canvas; no fee button was clicked. Do not interpret implementation completion as a paid end-to-end acceptance. Evidence: evidence/enhancement-final-verification.json, evidence/enhancement-live-readonly.json. Remaining fees/real enhancement output test stays with the user by explicit prior instruction.
