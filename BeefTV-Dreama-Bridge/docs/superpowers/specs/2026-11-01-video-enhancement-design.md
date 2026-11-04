# 剧梦视频增强接入

用户已选择：在现有 BeefTV 视频节点新增剧梦增强选项，连接一个视频使用，不更换 BeefTV 程序。沿用前面的直接实施授权、逐任务费用确认和禁止自动扣费测试要求。

## 方案

原生 AI 超分节点在前后端均固定官方 BeefAPI/Topaz，仅修改渠道无法切换。另一方案需要重建 BeefTV；用户选择现有视频节点方案。新增单独「Dreama 视频增强」渠道，四个选项 A、B、C闲时、C优先；复用 seedance-videos-compatible 的视频引用请求和已有返回协议。本地服务分流增强任务，原22项生成模型及原文保留规则不变。

## 用户行为

连接一个原视频，选择增强选项并点击生成。在本机任务页显示完整源视频时长、计费秒数、增强方式和积分，用户单次确认后才提交。任务完成后视频回到 BeefTV。增强不读取提示词，不以生成节点秒数裁剪视频，不提供未经验证的2倍/4倍输出承诺；隐藏时长/比例/分辨率生成参数。输出由所选增强服务决定，保留原视频音轨（必要时从原视频无损补回）。

## 输入与计费

本版本接收一个内联MP4，最大40MiB、最长120秒；零图片/零音频独立参考。使用真实ffprobe读取时长、尺寸、帧率。C最高30fps。节点上原有duration/aspect/resolution可被宿主携带，增强忽略这些字段并在任务页明确源视频参数。
A/B/C闲时按ceil(源时长)*1积分预估，C优先按ceil(源时长)*2积分预估，最终以平台结算为准；依据当前官网确认弹窗，未发现独立增强报价API。C提交前读取实时队列，当前阈值high1500/low4500。身份绑定CLI whoami真实返回的userId/teamId/orgId。

## 接口依据

官网nestjs-DIQl8Szq.js声明POST /nestjs/tasks、GET /nestjs/tasks、GET /nestjs/tasks/{id}和GET /nestjs/tasks/running-count，均经/gw，当前CLI令牌只读访问成功。类型A=video-enhancement-aliyun，B=video-enhancement，C=video-enhancement-c；C priority为low/high。提交必须enablePointDeduction:true，clientRequestId独立持久UUID。按官网分镜增强调用，projectId/seriesId设为412186/3279881，不传storyBoardId/resultSaveMode，避免覆盖原分镜。

视频通过已有官方OSS授权与七牛上传，按SHA256去重并回读验证。提交意图先写账本再POST，回执不明只按clientRequestId/输入URL/类型查询原任务，不重发POST。成功结果必须下载、ffprobe验证再报告completed；校验任务归属和真实任务ID。外部故障不能包装成功。

## 验证与部署

本机模拟HTTP/CLI测试、真实ffprobe和离线视频文件验证；实际增强服务只读查询已存在任务，不做付费提交。关闭已保存的空闲BeefTV后备份加密配置、增量新增渠道，保留用户选择及其他渠道，重启回读。最后一次独立代码审查，修复实质问题后交付。
