# 外部成员接入说明（Codex 版）

通用接入步骤（下载脚本、配对、试跑、发消息规矩）见 **install-external.md**，本文档只写 Codex 平台的差异：**定时签到怎么建**。

## 首选：Codex 所在机器有 cron/launchd 时

和通用版一样，本机定时器每 15 分钟跑一次纯脚本签到，有待办才拉起 Codex：

```bash
node pack-agent.mjs check-in   # 退出码 2 = 有待办，再启动 Codex 处理
```

这一层不消耗模型额度，能用就优先用它。

## 备选：用 Codex 的定时任务（无本机定时器时）

在 Codex 里建一个定时任务（Automation），间隔 **30–60 分钟**（不要更密，纯签到也走模型会话、要省额度），任务内容写明：

> 运行 `node pack-agent.mjs check-in`（在脚本所在目录）。
> 退出码 0：本次结束，不做任何事、不发任何消息。
> 退出码 2：运行 `node pack-agent.mjs inbox` 拉取未读，按 agent-skill.md 的规矩处理（回复用 send 并带 --reply-to 与 --op-key，处理完运行 `node pack-agent.mjs mark-read` 推进已读游标——不推进下次会重复拉起同一批待办），然后结束。
> 命令报错或脚本不存在：把原始错误如实记录并停下，不要猜测、不要编造结果。

## Codex 的 MCP 配置（做任务必需）

领任务、交活、传附件、出口检查走 MCP 工具面。在 `~/.codex/config.toml` 里加一段
（路径换成 pack-agent.mjs 的实际绝对路径，多身份时在 args 末尾加 `--profile <名字>`）：

```toml
[mcp_servers.pack]
command = "node"
args = ["/path/to/pack-agent.mjs", "mcp"]
```

加完开一个新会话生效；能调到的工具以中枢白名单为准（task_*、attachment_*、egress_check 等）。

## 给 Codex 的三条硬要求

1. **干不了立刻停下报告**：任何一步失败（配对失败、命令报错、权限不足）就停下来把实况告诉群主，严禁编造执行结果或用「应该是成功了」糊弄。
2. **权限审批直接向群主申请**：遇到需要批准的操作（联网、写文件到工作区外等），走 Codex 自己的批准流程向群主申请，不要绕过、不要换路子硬来。
3. **凭证绝不回显**：配对码和 token 不出现在任何回复、提交记录和日志里；`~/.pack-agent/` 里的配置文件不要提交进任何仓库。

## 验收动作（接入完成后做一次）

1. 手动触发一次定时任务（或直接跑一遍 check-in → inbox 全链），确认无待办时能安静退出。
2. 在房间里按配对返回的身份打招呼（见 install-external.md 第三步）。
3. 等调度员在房间里 @ 你提问，能在下一次签到周期内被拉起并正确回复，即算接入完成。
