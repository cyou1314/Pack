# H1/H2/H3 慢工复检（2026-11-16，staging16）

范围：额度数据源（member_quota_set + members 四字段）、房间改名/归档
（room_rename / room_archive / room_list include_archived）、头像组合
（_avatar_combo 分配 + member_register 加锁 + init 存量回填）。

## 六项复检

1. **鉴权边界**：三新工具全 require(dispatcher)；member_quota_set / room_rename /
   room_archive 均有成员越权实测拒绝。member_list 额度字段对全员可见——额度是
   群内公开信息（定稿 §4 用量卡全员可见），非秘密，可接受。
2. **并发原子性**：member_register 的「查占用→选组合→插入」全段走全局 _lock
   （S1 复检教训的同款修法）；8 路并发注册实测 24 人组合零撞。room_rename 的
   重名由 UNIQUE 约束兜底（与 room_create 同口径）。member_quota_set 是单行
   UPDATE，无读-改-写竞态。
3. **限额/校验口径**：week_used_pct 用 not(0<=x<=100) 拒绝越界且 NaN 安全；
   week_reset_at 复检中改为 not(x>0)（原 <=0 写法放行 NaN，已修）；
   extra_tokens_left >=0；三字段全空拒绝。room_rename 校验与 room_create 逐条同口径。
4. **数据完整性**：迁移全走 PRAGMA 先检后 ALTER（与既有迁移同范式，可重复执行）；
   头像回填只补缺 combo 者、按 id 顺序、used 集合递增，重复启动无副作用；
   avatar JSON 保留 glyph/c1/c2 v1 字段，老 App 渲染不受影响。
5. **审计**：member_quota_set（只记字段名不记值）、room_rename（旧名→新名）、
   room_archive / room_unarchive 各写一条 audit。
6. **与既有纪律一致性**：pack-main 改名/归档硬拒（App 与 watcher 以其为锚）；
   归档仅列表可见性，消息/任务不删不动（留痕纪律）；room_watch 以 room_list
   默认口径遍历，归档房自动跳过，无需改 watcher；改名后 watcher 按新房处理
   （从最新消息起、不追溯），与多房间纳入规则一致。

## 复检修复（2 处，已改已验语法）

- member_register 异常路径连接句柄：原仅 IntegrityError 分支关闭，组合耗尽等
  异常会漏关一个句柄 → 改 try/finally 统一关闭（存量复检「句柄滞留」教训）。
- week_reset_at 的 NaN 放行（见上第 3 项）。

## 测试证据

- 专测 test_h123.py 58 项：全新 DB 连跑 3 轮全过（功能/校验/越权/归档可见性/
  组合唯一/8 路并发注册/回归 post-read-task-skill）。
- 存量回归（staging15 套件）在 staging16 本体上复跑：functional 78/78 ✓；
  decision_kind 17/17 ✓（须全新 DB，复用旧 DB 会因成员重名假失败）；
  concurrency 输出与 staging15 基线逐行一致（C1b/C6 为语句级探针、复刻旧无守卫
  SQL 说明原缺陷，工具层修复由 live 项验证，非回归）；
  test_more 首跑死于其 FD 探针写死 staging15 端口 8813（本轮服务在 8814）——
  测试件工件、非回归；staging16 副本已改端口，按 phase C 顺序（同 DB 先
  functional 后 more）复跑确认。
- 修复后专测再跑第 4 轮确认终版文件。
