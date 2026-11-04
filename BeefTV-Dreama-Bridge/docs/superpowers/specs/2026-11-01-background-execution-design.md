# 5秒后台自动执行

用户明确要求将原逐任务手动确认改为：以后准备完成5秒后默认执行，未点击取消就提交。后续明确关闭任务页或切标签也不暂停。因此采用Python后台线程与持久账本时间，不依赖浏览器计时器；前端只显示服务器倒计时并提供取消/立即执行。该授权替代旧的逐次确认要求，不授权代理自行创建收费测试。

新policy background-countdown、delaySeconds5、effectiveAt部署时间。仅createdAt不早于生效时间、prepared、generationSubmitted=false、合法报价/模型的任务可自动排期；覆盖生成/增强/延长。已有历史准备记录与未知/失败任务不补交。首次排期存autoExecuteAt+quoteId，重启保留。达到时间先持久autoAttemptedQuoteId，再走现有approve（源/绑定/身份/模型/重新报价检查与一次提交意图）。报价变化生成新quote，重新5秒；其他检查错误停止自动重试。未知收费回执只读对账。

取消请求先写线程安全内存标记，再等待账本锁；自动approve在远程检查前后核对取消标记，取消成功写cancelled。已开始收费提交不可撤销。立即执行使用原approve路由，删除浏览器confirm弹窗；同一任务后端状态锁仍防重复。

测试用假的外部收费网关，保留真实Bridge/Generation/HTTP/后台工作线程；证明无需打开console也可倒计时5秒一次提交。前端VM测试实际console的显示和两个按钮，不做真实扣费测试。直接维护本机桥接，备份源码；不改BeefTV程序。
