# BeefTV Dreama Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在BeefTV通过Dreama接入项目412186，正确传递画布参考图，当前禁止任何付费生成。

**Architecture:** 先确认官方CLI具备本地图片上传、真实引用和视频分镜保存能力，再实现本机HTTP转接、凭据隔离与持久任务账本。若最新CLI没有素材上传/绑定接口，阻断适配器部署，交付可复查的缺口证据，不以虚假模型渠道代替完成。

**Tech Stack:** Windows PowerShell；后续转接拟用Python标准库、SQLite和Dreama CLI。

**Spec:** ../specs/2026-11-01-dreama-bridge.md（用户已于本轮确认直接实施，并禁止扣费生成）

## Global Constraints
- 项目412186；新增“BeefTV 接入”剧集；原有剧集和分镜不改写。
- 首个模型7573d384-0128-4424-9e90-8a0291b9274b，九梦2.0 Pro 720P，画幅9:16。
- 支持BeefTV画布参考图是必要验收项。
- 本轮不得执行image/video/library生成命令的实际提交；不可通过重试变相扣费。
- 不把Token写入日志、代码或回复。不猜未公开接口；CLI能力缺口报告needs_ui。
- 当前BeefTV数据目录须读取实际storage-location.json，原渠道保持可用。

## Review Focus
- 最新CLI是否有官方上传和图片绑定能力：没有则在实施适配器前阻断。
- 图片不得静默丢失：测试不支持输入必须在远端写入前失败。
- 用户禁止生成：测试所有生成路径不能调用付费提交。
- 超时回执不明：测试持久unknown，不自动重提。
- 重复请求与有意重新生成：只按已证明存在的请求ID去重，不按提示词合并。

### Task 1: 核实官方CLI素材链路
Files: evidence/cli-help-1.0.5.txt, evidence/manifest.json, backups/
- [ ] 下载官方1.0.5临时二进制，读取版本和help；核对manifest（若无哈希明确记录）。
- [ ] 备份当前CLI和本地skill，更新为1.0.5，验证账号和模型查询。
- [ ] 检查根命令、library、storyboard update、video produce帮助能否上传并绑定参考图。
- [ ] 有完整官方链路时进入Task 3；否则记录缺口，在此停止代码部署，不发起任何生成。

### Task 2: 准备已授权项目容器
Files: config.binding.json, evidence/series-create.json
- [ ] 重新读取项目和剧集；仅在不存在时创建“BeefTV 接入”，未知提交先对账。
- [ ] 独立series get回读真实ID，保存非敏感绑定信息；不创建测试分镜、不填写提示词。

### Task 3: 转接服务（前置：Task 1素材接口核验通过）
Files: bridge/dreama.py, bridge/media.py, bridge/server.py, bridge/tasks.py, tests/
- [ ] 先编写并运行失败用例：素材丢失、未授权生成、超时、重复任务、重启恢复。
- [ ] 实现参数数组CLI调用、固定项目绑定、官方素材上传及引用回读、127.0.0.1鉴权、任务账本。
- [ ] 核实BeefTV协议和稳定请求ID/预估入口，按已证实的协议实施；不假设客户端行为。
- [ ] 运行全套测试，并实际执行不扣费的素材上传/引用回读联通验证。

### Task 4: BeefTV配置和交付（前置：Task 3通过）
Files: start.ps1, stop.ps1, README.md, evidence/verification.md
- [ ] 经受支持UI/API添加独立渠道并读取模型；未通过兼容验证不切换默认模型。
- [ ] 验证画布图片实际到达剧梦并被引用，不启动付费生成。
- [ ] 明确标记“已接通/未生成验收”的实际范围；提供启动停止与回滚说明。
- [ ] 后续真实生成必须另获授权；本轮不执行。