# 单机版独立 profile 配对留证

- 执行日期：2026-11-20（Asia/Shanghai）；中枢为本机 `127.0.0.1:8794`，房间 `pack-main`。
- 本地单机中枢由旧版升级为仓库版，冒烟确认 74 个工具，含 `pair_code_create`。升级前离线备份位于 `~/.pack/local-hub/backups/pair-pre-upgrade-offline-20261120-222907/`，数据库副本 `integrity_check` 为 `ok`。
- 一次性成员：`id=4`，`name=pair-evidence-1120`，`kind=external`，`bound_room_id=1`；以上由本地 `member_list` 核实。
- `joined_msg_id`：未核实：执行环境直读 DB 被拒；`member_list` 未返回此字段。配对后 inbox 返回 cursor 10，不将其冒充数据库字段实测。
- 独立配置 `~/.pack-agent/local-evidence.json` 的权限为 `0600`。配对成功；`check-in` 退出码 0，未读 0；`inbox` 退出码 0，未读 0，`has_more=false`。
- 脚本采用实际支持的配对码位置参数；配对码及凭据未写入本证据。
- 收尾：本地 `member_remove` 返回已移除 `pair-evidence-1120`，随后 `member_list` 确认该成员不再出现；`local-evidence.json` 与临时脚本目录已删除并确认不存在。
