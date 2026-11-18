# staging15 · 批准链完整性修复（决议消息 kind='decision'）

## 问题

规则引擎自动决议与 review_file 决议的消息以 kind='chat' 落库、作者为 dispatcher。
按现行口径作者可删改自己的 chat 消息：软删后正文变占位符，`_approval_state`
扫不到决议模式 → 误判任务未决议 → 可重新 filing。决议可被作者本人抹除，
批准链留痕不完整（staging14 复检残留观察项）。

## 改动（hub-decision-kind.diff，共 3 处，最小改动）

1. `_approval_gate` 的规则决议 INSERT：kind 'chat' → 'decision'（正文格式不变，
   仍带「规则库自动通过/拒绝：…」后缀）。
2. `review_file` 的决议 INSERT：kind 'chat' → 'decision'（正文仍带「（独立审查）」）。
3. `_approval_state` 决议扫描：`kind='chat'` → `kind IN ('chat','decision')`，
   兼容历史 chat 决议与 App 以调度员身份发的 chat 决议（PackApp.swift:134/144
   的 App 内批准走 kind='chat'，必须继续认）。

`message_delete`/`message_edit` 本身未改：两者已拒绝非 chat 消息
（`only chat messages can be deleted/edited`），kind 改完决议即自动不可删改，
专测 A4/A5/B5/B6 已实证此链成立。

## App 侧兼容（代码层确认）

- PackApp.swift:223-224：消息列表对 `kind != "chat"` 一律加 caption2 小标签
  展示 kind 字符串 —— kind='decision' 会显示一个小 "decision" 标签，不崩不丢。
- PackApp.swift:85：待批列表只筛 kind='approval_request'，不受影响。
- App 自身批准（:134/:144）仍以 kind='chat' 发决议，中枢侧靠扫描兼容继续认。

## 验证（隔离：staging15 / 端口 8813 / 每阶段全新 DB，test-output.txt）

- 本轮专测 test_decision_kind.py：**17/17**。规则决议与审查决议均验证：
  kind='decision'、dispatcher 本人 delete/edit 被拒、正文完好未软删、
  再 filing 被拒（already decided）；历史 chat 决议仍被识别；普通 chat
  消息删除通道未被误伤。
- 全套回归：S1 专测 36/36、S1 复检 45/45、staging14 功能 78/78、
  test_more rc=0。
- 并发 C 系列关键项复跑：C2 双 filing 8/8 轮恰 1 成功、C3 双删恰 1、
  C4 双交活恰 1、C7 非持有人冒发不触发决议、C8 审计无 URL 查询串泄漏
  （C1b/C6 为语句级旧代码复现，不走线上代码路径，结果与 staging14 一致）。

注：首轮跑批时 test_skills/test_review 曾因其 BASE 路径写死 staging13、
误读旧令牌而 rc=1，系测试脚手架路径问题、与本改动无关；修正路径后重跑全过。

## 上线步骤

1. 备份：`cp hub/hub.py hub/hub.py.bak-20261116-decision`（并备份 pack.db）。
2. 覆盖：`cp hub-staging15/hub.py hub/hub.py`，`py_compile` 过一遍。
3. 重启：`systemctl restart pack-hub`，确认 active。
4. 冒烟（见下）。异常则回滚备份并重启。

## 冒烟法

1. `skill_list` 等只读工具可达、工具数仍 43。
2. 造一条必命中规则 + 任务，持有人发 approval_request：
   - 返回含 auto_decision；查 DB 该决议消息 kind='decision'；
   - 以调度员身份 message_delete 该消息 → 应被拒（only chat）；
   - review_file 对该任务再 filing → 应被拒（already decided）。
3. 在 App 里看该决议消息：正文正常、带小 "decision" 标签即兼容成立。
