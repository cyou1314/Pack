# 第三步重试单（第二版）——待群主回来后开工，勿提前执行

前置条件（全部满足才开工）：
1. 分类修复验证已通过：解除 paused 后至少跑完一个检查周期链（resume 检查 + 两次 timed 复查），session-check 日志中 PRE_SUBMIT_TRANSIENT 的 countsAsGeneric=false、失败时间不再累积、账号未再进入 paused。
2. 验证探针卡已 done。

相对 HEADLESS-STEP3-RETRY-BRIEF.md 的修订（其余条款沿用）：
- §0 状态门加严：切换前除只读确认 absent 状态为 ready 外，还须读库确认 failTimes 为空、且最近 15 分钟无新增普通失败记录；不满足则停下回报，不许带伤切换。
- §5 回滚条件拆分：接管后若 absent 进入 paused，先看 session-check 日志的分类——若由 countsAsGeneric=false 的分类攒成（修复后理论上不应发生），属补丁失效，立即回滚并回报；若为其他 countsAsGeneric=true 分类攒满，按真失败处理，同样回滚，但回报中须附分类名单与原文。
- 观察口径不变：cooling_down 先观察最多 10 分钟；limit_reached 不回滚、保持引擎运行回报待决。
- 接管判定不变：两次 status 检查（间隔约 3 分钟）均 ready；接管后调度员发文本卡 + 图卡（媒体解码首测）。
