# 验收项 5 实打裁定（2026-11-20，调度员）

执行：Mac 上 Codex 经临时隧道从外部视角运行 verify_item5.py，打隔离 staging 实例
（71 工具、全新库），原始输出见同目录 ITEM5-VERIFY-RESULT.md（提交 4308f26）。

总账：29 项断言，28 PASS，1 FAIL——该 FAIL 经查为测试节奏假象，非中枢缺陷（见下）。

## FAIL 项裁定：[quota] egress_check 60/分频控

- 现象：脚本连打 65 次 egress_check 无一次被限流（i=64 跑满）。
- 证据：靶场库审计 65 条 egress_check 行，时间跨度 82.8 秒，任意 60 秒窗口内
  最多 49 条——隧道往返约 0.79 次/秒，窗口内根本攒不到 60 次，阈值未被触发。
- 复证：调度员在 VM 本地以新配对成员 burst-ext 快速连打，第 61 次即被拒：
  {"error": "rate_limited", "retry_after": 60}，且 external_egress_throttled
  审计行落库 1 条（4.5 秒内完成 61 次）。
- 裁定：频控实现正确，本项按 PASS 计。验收项 5 合计 29/29 通过。

## 附注

- [quota] 5MB+ 附件一项的 PASS 证据是传输层 Broken pipe（请求体超 MCP 4MiB
  上限、未达工具层），与既有结论一致；工具层 5MB/50MB 精确口径已由 staging
  直测套件覆盖（test_direct 9/9）。4MiB 上限按群主 2026-11-20 拍板单独立项。
- 靶场实例与隧道已于验收后销毁。
