# Codex 内置生图

这是本机 BeefTV 与 Codex 桌面运行时之间的适配插件。它使用本机 Codex 已登录账号的生图能力与额度；配置中的令牌仅用于访问 `127.0.0.1` 桥接，不是 OpenAI API Key。

每个任务生成一张图片。参考图最多 4 张，每张 8 MiB，总计 16 MiB；提示词最多 30000 字符。支持文字生图和参考图编辑，不支持遮罩、视频、音频。构图比例作为提示词要求，实际像素与比例由模型决定。透明背景通过原生标志提交。

尺寸菜单内置 1K、2K、4K、8K，以及 9:16、16:9、1:1、3:4、4:3、2:3、3:2、21:9 和自动比例。分辨率经 `request.quality` 映射为桥接 `resolution`，按最长边 1024、2048、4096、8192 像素写入原生生成要求。当前 Codex 内置工具没有强制像素参数，各档是请求目标，不能保证原生 8K 等精确输出；不自动放大图片。结果列出目标档位和真实尺寸。

任务接口：

- `POST /v1/images/jobs`，Bearer 本机令牌；`Idempotency-Key` 使用 BeefTV 任务稳定键。
- 请求 JSON：`{model:"codex-image",prompt,images:["data:image/png;base64,..."],aspectRatio,resolution,transparentBackground}`；resolution 允许 auto/1k/2k/4k/8k，缺省为 auto。
- `GET /v1/images/jobs/{id}` 查询；`DELETE /v1/images/jobs/{id}` 取消。
- 返回 JSON：`{id,status,message,images:[{dataUrl,mimeType?}],error?}`；status 为 queued、running、succeeded、failed、cancelled。

用 `install_images.py --pack 路径.beeftv-plugin` 打包；用 `install_images.py --install` 安装渠道与插件，`--make-default` 才切换默认生图模型。安装前正常退出 BeefTV。安装器保留旧渠道、文本及助手设置和项目数据，备份原加密配置；BeefTV 下次启动从 EXE 同级 `plugin-packages` 扫描注册插件。应用更新后如该包被替换，应重新运行安装器。应用内部将此目录扫描的插件标为随应用提供；本插件作者为 Local Codex Bridge。
