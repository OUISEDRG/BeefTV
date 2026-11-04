# BeefTV 本机 Codex

把 BeefTV 的“默认文本模型”“助手模型”和“默认生图模型”连接到本机 Codex 已登录账号。文本和助手可选择原生目录中的 **8 个模型**；生图通过独立的 **Codex 内置生图** 插件接入。服务位于 `D:\BeefTV-Codex-Bridge`，文本 API 地址为 `http://127.0.0.1:18766/v1`。

## Codex 内置生图（v1.4）

分辨率菜单提供 1K、2K、4K、8K 四档，宽高比提供 9:16、16:9、1:1、3:4、4:3、2:3、3:2、21:9 以及自动。所选分辨率作为目标写入原生生成提示词，对应最长边 1024、2048、4096、8192 像素。当前 Codex 内置工具没有强制像素参数，因此选择 8K 不代表保证返回原生 8K；结果保留原始图片并报告实际尺寸，不做自动缩放或重新生成。此次通过当前 BeefTV 的声明分辨率界面显示四档，无需重建程序。

在 BeefTV 的默认生图模型或图片节点模型菜单中选择“Codex 内置生图”，输入提示词即可创建图片；添加参考图后可描述要进行的编辑。它通过本机 Codex 原生生图调用使用已有登录账号的能力与额度，无需 OpenAI API Key。原有文本、助手与 Dreama 图片、视频、音频渠道继续保留。

每个任务生成 **1 张**图片，最多使用 **4 张**参考图。支持 PNG、JPEG、WebP，每张参考图不超过 8 MiB，总计不超过 16 MiB，提示词最多 30000 字符。参考图由 BeefTV 转成内联图片提交；不支持遮罩、参考视频和参考音频。比例可选 `auto`、`1:1`、`16:9`、`9:16`、`4:3`、`3:4`、`3:2`、`2:3`，作为构图提示传给模型，**不保证固定像素或严格宽高比**。透明背景通过原生生图标志传递。

安装时先正常退出 BeefTV，在桥接目录执行：

```powershell
python install_images.py --install
```

安装器只新增或更新本机生图渠道，**默认不切换当前生图模型**；需要切换时执行 `python install_images.py --install --make-default`。不加 `--install` 只展示不含密钥的预览。插件包写入 `D:\BeefTv\plugin-packages\codex-images.beeftv-plugin`，BeefTV 下次启动会自动扫描注册，无需重建 EXE。安装器备份原加密配置，提交前再次核对 BeefTV 已退出及文件未被其他进程修改。应用更新若替换了此插件包，可重新运行安装器。

运行 Python 环境需要 `cryptography` 和 **Pillow**；可用同一个运行环境执行 `python -m pip install cryptography Pillow` 安装。Pillow 只校验输入和输出是否为有效图片，不改图、不缩放。模型选择值为 `codex-local-image::codex-image`，插件 provider 和 HTTP 请求中的模型 ID 均为 `codex-image`。

生图通常需要数分钟，使用独立的异步任务接口，三个接口均需 Bearer 本机桥接令牌：

- `POST /v1/images/jobs` 创建任务，必须提供稳定的 `Idempotency-Key` header。JSON 字段为 `model`、`prompt`、`images`（data URL 数组）、`aspectRatio`、`resolution`（auto/1k/2k/4k/8k，缺省 auto）、`transparentBackground`（布尔值）。目标分辨率参与幂等校验；缺省和 auto 兼容旧任务指纹。
- `GET /v1/images/jobs/{id}` 查询任务。状态为 `queued`、`running`、`succeeded`、`failed`、`cancelled`；成功图片位于 `images[].dataUrl`，由 BeefTV 保存到本地素材库。
- `DELETE /v1/images/jobs/{id}` 取消任务。

结果 metadata 中的 `requested_resolution` 是请求目标，`actual_dimensions` 来自实际图片；非 auto 的完成文案也同时列出两者。输出文件最多 30 MiB、最多 8192 × 8192 像素，参考图限制保持不变。安装升级仅在当前默认生图模型为 Codex 且旧 quality 无效时，将其迁移为 1K；其他模型档位不改。已有图片节点若保存旧的 auto 档位，可点击一次新的分辨率按钮更新节点自己的选择。

任务记录和去重键持久保存在 `state/image-jobs.sqlite3`，参考图与结果保存在 `state/image-jobs/{id}`。相同幂等键与相同请求会返回已有任务；同一键用于不同请求会返回冲突，不会另起一次生图。服务重启后可继续读取已完成结果；中断时尚未完成的任务会明确标为失败并说明结果未确认，不会自动重放或再次消耗额度。查询、取消与重试前应先核对原任务，新的生成意图使用新的操作键。

2026-11-02 已在正式安装完成一次真实验收：通过 BeefTV 独立 `canvas_image` 任务调用本机 Codex 内置生图，约 30 秒返回一张 1254 × 1254 PNG，任务一次尝试成功，图片已进入 BeefTV 资源和素材库，并经打开查看确认。验收记录为 `evidence/codex-image-acceptance-20261102.json`，测试图片为 `state/image-jobs/33feb2e561e246cc87386dd51146f703/output.png`。原默认图片模型、8 个文字模型和助手选择均已核对保留。参考图和透明背景经过协议及模拟测试，本次实际生成验证的是单张文字生图。

## 工具续接修复（v1.2.1）

修复助手执行几次工具后反复显示“模型没有完成回复”的问题。BeefTV 每次请求都会附带最新画布和步骤状态；桥接现在能将这些信息与工具回执一起送回原 Codex 回合，连续操作不会再被误判成多个新会话并耗尽本地并发上限。图片工具回执、实际用户消息绑定和回执去重继续保留。

升级服务前需等待现有请求结束；重启不会恢复之前已中断的推理，也不会自动重新提交画布生成任务。可在原对话继续要求助手先检查现有节点和任务，再处理尚未完成的部分。

## 模型和推理强度（v1.2，BeefTV 原生控件）

模型在 BeefTV 的模型菜单中选择，每个实际模型只有一个菜单项：gpt-6.1-sol、gpt-6-astra、gpt-6.1-terra、gpt-6-sol、gpt-6-luna、gpt-5.6-sol、gpt-5.6-terra、gpt-5.6-luna。

推理强度现在可直接在 BeefTV 内调整：打开画布右上角 Agent，或普通文本节点、脚本节点的输入面板，在模型选择旁点击“推理 高”等下拉框。选择档位即保存，无需打开网页。各模型分别保存设置；选择“跟随模型默认”可恢复该模型原生默认档位。文本节点与助手选择同一模型时会同步显示同一个设置。

本机程序使用官方 v2.1.1 源码构建，增加了原生推理控件，构建标记为 `codex-ui.1`（程序版本仍显示 v2.1.1）。源码在 `D:\BeefTV-Codex-UI`，构建工具和证据在 `D:\BeefTV-Codex-UI-tools`。以后安装官方更新可能替换这些本机控件，需要重新合入构建。原设置页保留为兼容入口，日常不需要打开。

各模型均支持 low、medium、high、xhigh、max；两个 Luna 模型不提供 ultra，其余 6 个提供 ultra。选项来自真实原生 model/list，未添加目录外的档位或 GPT-5.5。原有 Astra 的 high 被保留，其他模型初始跟随原生默认。

保存后无需重启，从下一次新请求开始生效；正在处理的工具链固定沿用开始时的强度。文本节点和助手对同一模型共用该设置，已保存的档位（包括“跟随模型默认”）优先于 BeefTV 创作页旧“思考”布尔开关和请求中的兼容 reasoning_effort 字段。推理强度调节的是思考投入，不保证越高的档位在所有任务中效果越好。

服务启动只读刷新原生目录到 `model-catalog.json`。实际执行仍使用独立工具隔离目录；两者分开，避免把 bundled 的隐藏/旧目录作为账号菜单。偏好保存在 `state/reasoning-settings.json`，API 模型目录和参数均严格校验，不支持的组合不会静默降档。

## 使用

- 默认文本模型：本机 Codex · gpt-6-astra。文本节点直接返回生成文字。
- 助手模型：本机 Codex · gpt-6-astra。BeefTV 自带助手继续执行自身画布工具，包括查询、创建/修改节点、连线、素材绑定等。实际能力由 BeefTV 当前提供的工具与画布权限决定。
- 参考图由原生图像消息传递；提示词正文不改写。历史对话以保留角色的上下文传入；工具链在同一 Codex 回合中继续。
- 不需要填写 OpenAI API Key。Codex 自己读取并管理已有登录，桥接不会复制账号凭证。模型运行在 OpenAI 服务端，本机桥接调用 Codex，并非离线推理。
- 使用将占用当前 Codex/ChatGPT 账号的额度，是否可生成以实际账号权限、限额与服务状态为准。安装器和服务启动检查本身不提交模型推理或媒体生成；实际生图由任务提交触发。
- 原有 Dreama 图片、视频、音频、增强、延长配置继续保留；它们仍按各自原有生成规则处理。

## 运行

`install-service.ps1` 安装当前用户的 Windows 计划任务 `BeefTV-Codex-Bridge`，并立即启动服务。任务在登录时启动隐藏的 `supervisor.py`，每分钟检查一次守护进程是否仍需启动；正常运行时忽略重复触发，不需要管理员权限或保存账号密码。旧的 Startup VBS 在新服务就绪后备份并移除，自动启动不再依赖 Codex 缓存中的 PowerShell。

`start.ps1` 启用并确保守护进程和服务均已就绪，只核对登录和模型目录，不生成。守护进程单实例运行，复用已启动的健康服务，在桥进程退出或启动失败后按退避策略重试；不会因一次健康检查超时杀掉活动进程，也不会抢占其他程序的端口。此服务独立于 BeefTV 窗口保持运行，所以从任意入口打开 BeefTV 均可使用同一连接。

`stop.ps1` 先写入持久停止标记 `state/supervisor.disabled`，待守护退出后调用原有服务关闭接口，避免被恢复机制重新拉起。存在活动会话时默认拒绝停止，确需中断时显式传 `-Force`。再次执行 `start.ps1` 才解除停止标记。若服务无响应，停止脚本禁用自动恢复并报告情况，不会按可能过期的 PID 强杀进程。

守护状态保存在 `state/supervisor.json`，恢复日志在 `state/supervisor.log`，服务输出追加到 `state/server.log` 和 `state/server-error.log`。守护日志限制大小并轮转；服务输出在重新启动前轮转，运行中的输出文件不会被截断。恢复只重启服务，不重新提交模型请求、画布工具调用或生成任务。正在进行的请求若因进程退出而中断，仍按原有逻辑明确报错。

本机密钥在 `state/local-api-key`，已写入 BeefTV 加密配置；不要发给他人。服务仅监听 loopback。未将 Codex shell、文件编辑、MCP、插件工具暴露给 BeefTV；画布操作由 BeefTV 原生权限系统执行。

当前 Codex 版本会保留 `C:\Users\JT\.codex\AGENTS.md` 中的全局用户偏好，未修改该文件；项目目录指令和自动技能加载已关闭。

## 验证和限制

此前文字桥的安装验证使用模拟 Codex RPC + 实际 BeefTV SDK，覆盖文本流、工具往返、顺序工具调用、回执去重、图片输入和配置保留。当时的真实 Codex 只读验证范围为 initialize、account/read、model/list 和无推理 ephemeral thread/start，没有在该验证阶段调用真实 turn/start；这不代表生图功能的实际运行范围。

v1.3 的自动化验证覆盖图片请求校验、异步任务状态、持久幂等、取消与重启中断、原生图片事件处理、加密配置安装与并发写入保护。插件合同另通过当前 BeefTV 源码的 Go 测试验证：真实 generation.Input 转换为请求、轮询解析图片、有效 PNG 进入结果处理，以及不支持的输入被拒绝。这些自动化检查不提交真实生图；账号权限与实际出图链路的验收需另行记录。

工具链中服务重启、等待过期或断连会明确报错，不自动重新推理或重放工具。服务允许多任务，各任务隔离，最多 8 条活动链。BeefTV 自带助手有 95 秒无实质输出超时，桥接转发真实文本和公开思考摘要以显示进度。

Codex 升级后如旧可执行文件被移走，可更新 `runtime-config.json` 的 `codexExecutable` 为 `Get-Command codex` 返回的路径，之后重启服务。

参考：[官方 Codex app-server 文档](https://learn.chatgpt.com/docs/app-server)。
