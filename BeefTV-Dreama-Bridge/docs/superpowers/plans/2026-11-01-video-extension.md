# Video Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans in the existing bridge workspace.

**Goal:** 普通视频节点接剧梦extend，返回新增续写片段。
**Architecture:** extension.py独立网关/预检流程，复用Bridge账本、Generation提交状态机和回传；独立渠道目录/extension。
**Tech Stack:** Python、requests、ffprobe、BeefTV兼容协议。
**Spec:** ../specs/2026-11-01-video-extension-design.md

## Global Constraints
- 不替换二进制，不做收费测试，不改变原22+4模型/default。
- 一个MP4≤40MiB、2–30秒；续写4–30整数秒；九梦2.5Pro480P/720P按当前目录真实枚举。
- 严格原文，额外图片采用已有官方标签；目标视频独立字段绑定。
- 无Git仓库，备份+progress.md记录，所有公共短剧文件不改。

## Review Focus
- 源时长与新增时长混淆导致费用错：真实probe与calcToken参数测试。
- 分镜mode/target被修改仍提交：审批前完整回读拒绝测试。
- 未知收费回执重启重复扣费：专用分镜对账且只读测试。
- 续写片段被源音轨覆盖/拼接：仅Generation原返回逻辑，不继承enhancement finalize。
- 额外图片在images中顺序错：目标视频必须首位、图片标签按原文引用顺序。

### Task 1: 模型、输入、官方网关
**Files:** extension.py, enhancement.py, tests/test_extension.py
**Interfaces:** extension_models(); validate_extension_request(data); ExtensionGateway.prepare_board(job),inspect_board(job),estimate_extension(job),submit_video(job)
- [x] 添加失败测试覆盖两模型真实映射、一个视频/图片校验、源2–30、新增4–30、prompt原文、save/produce参数/收费护栏、完整绑定回读。
- [x] 运行观察RED，再实现至GREEN；官方POST只用模拟请求验证。

### Task 2: 持久化/HTTP/渠道
**Files:** extension.py, generation.py, server.py, console.js/html, install_extension.py, tests/test_extension.py, tests/test_server.py
**Interfaces:** ExtensionFlow.prepare(data,key),check_ready(job),estimate_job(job)；Server按kind分流；/extension/v1/models、/extension/v1/videos。
- [x] 添加预检不收费、准确quote、unknown重启不重提、输入被改、HTTP回传失败测试，再实现。
- [x] 仅提取Generation.estimate_job扩展点、enhancement.decode_video输入复用；不重构原生成。
- [x] 安装新渠道两模型，maxVideos1、images30、durationSupported true、空画幅，独立模型路径。

### Task 3: 核验、安装、交付
**Files:** README.md, progress.md, config.binding.json, evidence/extension-*.json
- [x] 全套测试/JS检查；真实非计费预检验证新分镜原文、extend、target及报价，不提交produce。
- [x] 正常退出空闲BeefTV后备份配置/增量安装，重启桥接和程序，回读配置。
- [x] 按代码审查技能独立最终复核，修重要问题；记录收费0及原生画布验收边界。
