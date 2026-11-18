# staging33 慢工复检：member_avatar_set（工具数 58 → 59）

增量：新增调度员专属工具 `member_avatar_set(name, avatar_combo)`，为在册成员
更换参数化头像组合（UI 定稿 §12.6「更换头像」的写路径；此前只有登记时可指定）。

## 对照检查

- **鉴权边界**：`require(me, "dispatcher")` 与 register/remove 同口径；定向测试
  实证成员 token 调用被拒（member self-call forbidden）。
- **并发原子性**：查占用（_used_combos）→校验→UPDATE 全程持全局 _lock，与
  member_register 的 S1 复检范式一致；并发登记/改组合不会撞组合。
- **校验完整性**：四字段必填（与 register 的「缺字段自动补齐」不同——更换是
  明确意图，缺字段整笔拒绝，避免半个组合落库）；每字段按 AVATAR_* 参数集
  逐个校验；坏 JSON / 非对象 / 不存在成员 / 已移除成员均整笔拒绝且不落库
  （定向测试 16 项全覆盖、全过）。
- **占用口径**：占用集只计 removed_at IS NULL 的在册成员（与 _used_combos
  既有口径一致）；设为本人当前组合幂等通过（cur.combo 豁免），不误报 taken。
- **数据完整性**：只 UPDATE avatar 一列的组合四键 + combo 键；glyph/c1/c2
  保留（测试实证）；members 其他列不动；无迁移（avatar 列已存在）。
- **审计**：成功路径写 audit(member_avatar_set)，staging 库实查 3 条落库；
  失败路径在 commit 前抛错、不写审计不落库。
- **与既有纪律一致性**：错误文案沿 register 的 `avatar_combo: ...` 前缀；
  返回形状 {name, avatar} 与 register 的 avatar 字段同构，App 可复用解析。
- **回归**：run-all-suites 七套件对 staging33 hub.py 全跑（见 regression.log）；
  staging15 工具数断言随部署同步 58→59。

## 残留观察

- owner/dispatcher 本人也可被调度员改组合（App 以调度员身份操作，与登记时
  指定组合的权限面一致），不单独设限。
- App 侧组合选择器只应提供参数集内的值；中枢校验是最终防线。
