# EXTERNAL-AGENT 结果（外部 agent 接入线，2026-11-20 关账）

施工单：EXTERNAL-AGENT-BRIEF.md（冻结 1fdbda4）。本线已全部上生产：
Phase A 部署 c3a7634（工具 65→71）、Phase B 部署 0450733（71→74）。

## 交付内容

- **件一 配对码体系**：pair_code_create/list/revoke、member_token_rotate、external_gate
  总开关、POST /pair 公开路由；长随机码、24h 过期、一码一成员、失败锁定 + 全局滑窗
  防扫；token 只出现一次、中枢只存哈希；名字三口径（占用拒 / 保留名拒 / reclaim 只
  轮换 token_hash 并保留入群水位与已读游标）。
- **件二 硬隔离**：members 四列（kind/display_name/bound_room_id/joined_msg_id）；
  member_rename 只改 display_name；入群水位硬截断（读路径 + 附件双侧）；require 层
  工具白名单闸门（名单外一律拒 + 审计）；任务/附件/频控/approval 双硬顶口径按定稿；
  external 独立附件配额（5MB/10 次日/50MB）；watcher 永久性拒绝回帖拒因不重试；
  _auto_relay 排除 external、未认领巡检对 external 宽限 2700s。
- **件三 收件箱**：inbox_pull（水位截断、10 天窗、上限 100、@ 标记与任务卡摘要）、
  inbox_mark_read（只进不退）、inbox_send（reply_to、防盲回结构化拦截、op_key 幂等）；
  脚本版本门（pack-agent <1.0.0 拒，非 pack-agent 客户端降级放行）。
- **件四 pack-agent.mjs**：runtime/ 正式落位；子命令 pair/inbox/mark-read/read/send/
  check-in/mcp；config 0600、--profile 隔离、pair 防串号（兑换后校验、宁废一码）。
- **件五 三份接入文档**：docs/external-agent/{install-external,install-codex,
  agent-skill}.md（Gemini 复核后已按 4 点必改修订，见下）。
- **件六 App 微修**：Views.swift 登录按钮条件加 kind != "external"——已派 Mac Codex
  施工（编译 + 入仓，不单独部署，随下一轮 App 重建生效），截至本文件入仓时待其提交。

## 验收证据

1. **staging 测试**：Phase A 178→197 项断言 0 失败（tests/archive/hub-staging51/REVIEW51.md）；
   Phase B 八套件 257→261 项 0 失败（tests/archive/hub-staging52/REVIEW52.md；+4 为 mark-read
   死循环回归断言），调度员均独立复跑 EXIT=0。
2. **Gemini 红队**：Phase A 首轮拦部署（2 必改 + 1 中风险 + 3 小修），六处修复
   e552bc7 后复核过关；最终裁定（2026-11-20 17:2x）：Phase A 放行、egress 节奏假象
   裁定无异议；Phase B 中枢实现与 5 项取舍全认可，脚本/文档 4 点必改（mark-read
   子命令缺失、MCP 配置未写、cron 示例吞退出码、egress_check 口径写错）+ 2 条建议，
   已全部修完入仓 e4942ad 并复跑全绿，按其口径直接接续部署、未再过一轮。
3. **验收项 5 实打**（隔离靶场、Codex 从 Mac 经隧道执行，ITEM5-VERIFY-RESULT.md
   4308f26）：29 项 28 PASS + 1 项经调度员裁定为隧道节奏假象按 PASS 计（裁定正本
   ITEM5-ADJUDICATION.md 5604d5c，含 VM 本地 4.5 秒第 61 次即限流的闭环复证），
   合计 29/29；Gemini 最终裁定确认无异议。
4. **生产部署**：Phase A 覆盖 + 重启 + 冒烟 71 工具 + preflight 全绿，部署后默认
   回归 ALL GREEN（regression-post51b.log）；Phase B 同流程至 74 工具；两次部署
   完成后 --full 全量回归 ALL GREEN（regression-full-post52.log，含 staging21
   对抗套件 rc=0；--full 包含默认套件全集）。
5. **真接入验收（生产实证）**：2026-11-20 18:0x–18:2x，Mac 上 Codex 按
   install-codex.md 自行完成配对，外部成员 codex-ext（id=13、kind=external、
   provider=codex、joined_msg_id=183）落号，凭证文件 0600；调度员在 pack-main
   发 @ 提问（消息 #184），其经 check-in/inbox 读到后以 inbox_send 回复 #185
   （reply_to_id=184），已读游标推进至 184。配对码全程未进仓库/日志正文。

## 单独立项与遗留

- **4MiB 传输上限单独立项**（2026-11-20 群主拍板）：MCP 传输层请求体上限使经 HTTP
  的附件实际约 ≤3MB 可达（工具层 5MB/50MB 配额口径不变、是更严不是更松）；文档已
  注记，放开方案另行评估，不阻塞本线。
- **单机版独立 profile pair 留证待补**：原由 Gemini 分担（本机单机版实测），其额度
  与通道限制未做；staging 隔离实例已双轮覆盖 pair 全链，生产真接入已实证，本地栈
  pair 留证后补。
- **地址自动发现后补小件未做**（定稿列为第一版后补）：给外部成员单签 pack-addr
  只读 PAT、脚本自拉 hub-url.txt 刷新，待需要时立项。
- 外部成员 codex-ext 保留在册（群主的 Codex 接入身份），后续可直接 check-in 使用；
  如需停用走 external_gate 或 member 移除流程。
