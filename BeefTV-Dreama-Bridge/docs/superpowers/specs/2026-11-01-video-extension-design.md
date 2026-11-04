# 剧梦视频延长接入

用户要求将剧梦视频延长移植到BeefTV，已选择新增续写片段、原视频留在原节点；沿用当前已选的普通视频节点/独立渠道方式与逐次费用确认，不替换BeefTV二进制，不自动做付费测试。提示词正文严格原样，不增加“延长视频”或润色前缀。

选择：复用Python本机桥接，新增“剧梦视频延长（连接一个视频）”渠道。替换原AI超分或重编译BeefTV不符合既定方式；把普通生成秒数加长不能代表extend，故使用官网真实omniReferenceTaskType=extend。

证据：官网index-Bsshbw-t.js中Oj以hasVideoExtendEdit===1筛选；目标视频targetVideoUrl存于分镜，produceVideo携带omniReferenceTaskType=extend以及images=[目标视频URL,...额外素材URL]。新版完整endpoint/list共22项，其中九梦2.5Pro480P/720P hasVideoExtendEdit=1且isExternal=0；两项即梦为外部网页连接，不装成可直调。catalog快照evidence/extend-catalog-raw.json。模型当前新增时长4–30整数秒，原视频2–30秒；桥接MP4内联上限40MiB。

输入：一个MP4原视频，续写提示词，固定新增时长与模型对应分辨率，可选普通参考图片（复用原图片上限30/单张10MiB及原位标签规则），不支持第二视频/独立音频。目标视频由targetVideoUrl和images首位绑定，用户@视频1文字保留，不伪造素材ID。图引用仍在原位置通过官方assemble取得标签。输出为平台返回的新片段，禁止本地拼接、禁止将原音轨覆盖到续写片段。

准备：验证/缓存原视频，绑定当前账号；上传并SHA256回读，独立分镜经现有CLI创建/保存原文，再通过官网save写extend/targetVideoUrl。直接API/list完整回读逐字prompt、mode、target、时长和ID；正常任务不修改。预估走已观测calcToken(type1,eid,number1,duration,referenceVideoTotalDuration字符串两位)，返回非负有限数。

确认：绑定quoteId/points；重验源hash、账号、模型还支持非external extend、分镜未被修改且无videoTaskId，重新calcToken。持久submitting后仅POST一次produceVideo；回执未知靠专用分镜精确对账，不自动重提。numeric taskId沿用正常生成poll/download/Range，不用enhancement保留源音轨逻辑。

部署：独立/extension目录，原22生成和4增强不变，默认模型不变。界面区分原时长/新增时长/新片段；验证收费调用使用mock，真实只允许只读与非计费预检。无Git沿用源码zip/加密配置备份；不动短剧工作流生产包或项目锁。
