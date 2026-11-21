# staging50 复检记录（Gemini 审计接口建议）

来源：2026-11-20 Gemini 审计第 7 轮的两个接口建议，调度员采纳后自做。

## 改动（仅 hub.py，两处）
1. task_list：status 支持逗号分隔多状态（IN 查询）。单状态、空状态、
   逗号-only（视为无过滤）行为与旧版一致；与 staging49 的 room 过滤可组合。
2. room_read_new：新增尾参 tail=False。tail=True 时在同一 since_id 过滤内
   取最新 limit 条、返回仍按 id 升序；默认行为逐字不变。limit 钳制 1..200 沿用。

## 对抗自查
- 鉴权/工具面不变：工具数 65，无新增工具，仅两个既有工具的参数语义扩展。
- 注入面：多状态走参数化 IN 占位符，状态值不拼 SQL；tail 只切换 ORDER BY 方向。
- 向后兼容：App/运行时/桥现行调用均不传新参数，行为不变；status 传旧单值不变。
- 排序口径：task_list urgent DESC, id ASC 未动；room_read_new tail 返回升序，
  既有按 since_id 增量拉取的调用方不受影响。

## 测试
test_staging50.py（端口 18797 全新 DB）：13/13 PASS——多状态/单状态/空/
带空格/逗号-only/与 room 组合；tail 首尾取数、since_id 组合、limit=200 全量。
首跑 2 条 FAIL 为测试夹具假设错（task_create 会在房内落 task_card 消息，
房间并非只有手发的 6 条），修正夹具后整套重跑全过，代码未因此改动。

## 部署
2026-11-20 已部署生产：备份 hub.py.bak-pre50-20261120，重启 pack-hub 后冒烟——工具数 65、多状态查活跃集正常（当时 0 条）、room_read_new tail 对生产房间取到最新 3 条（id 179–181）、preflight ALL GREEN。
