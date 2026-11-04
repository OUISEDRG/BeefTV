# BeefTV 本机 Codex 接入

用户已明确授权将默认文本和助手改为本机 Codex，复用现有登录，保留画布工具；不运行真实推理或扣费测试。

使用独立本地服务 `D:\BeefTV-Codex-Bridge`，监听 `127.0.0.1:18766`，Bearer 本机密钥。提供 OpenAI Chat Completions 给 BeefTV 现有官方协议，使用本机 Codex app-server 托管认证，不复制账号凭证。模型沿用当前 Codex 配置 `gpt-6-astra`。

BeefTV 继续拥有工具执行、画布授权和付费确认。服务将请求中的函数声明交给 Codex dynamicTools；收到 item/tool/call 后返回 OpenAI tool_calls，并保留对应进程和 RPC 请求。下一次 BeefTV 请求中的工具回执回填同一个 RPC 请求，再继续同一回合。工具调用 ID、工具声明、消息上下文均校验绑定。会话失效明确报错，不自动重放工具或重新推理。

每次新用户请求启动临时会话；系统/开发者内容进入 instructions；前序对话使用保留角色的历史数据，最新用户文字和图片保持原内容。工具链中的追加调用持续使用原会话。没有真实测试时不能声称账号实际推理权限已验证。

Codex 内建 shell、文件修改、MCP、插件和自动技能上下文在子进程隔离配置中关闭，仅暴露 BeefTV 传来的函数。服务不改用户 Codex 全局配置。停止/断连/过期会清理子进程。认证信息、提示词、工具结果不写日志。

配置迁移只更新 codex-local 通道、textModel、assistantModel 及对应索引，保留 Dreama 和所有其他设置。BeefTV 正常退出后原子写入加密配置和备份，再重开。
