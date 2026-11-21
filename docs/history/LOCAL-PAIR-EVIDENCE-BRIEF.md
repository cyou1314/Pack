# 单机版 pair 留证施工单（2026-11-20）

背景：EXTERNAL-AGENT 线已关账（EXTERNAL-AGENT-RESULT.md），唯一留证欠账是
「单机版独立 profile pair」——原由 Gemini 分担未做。本单在 Mac 本机单机栈
（~/.pack/local-hub，只绑 127.0.0.1:8794）上把 pair 全链走一遍并留证。
全程只动 Mac 本地栈，不碰 VM 生产中枢。

## 步骤

1. 在 pack-repo 先 git pull 到最新（应能看到本单与 e4942ad 之后的提交）。
2. 确认本地栈在跑：本地中枢 launchd 代理应已加载；用本地端点
   http://127.0.0.1:8794/mcp 发一次 initialize 握手确认可达。若本地中枢没在
   跑，先按 ~/.pack/local-hub 里既有的 launchd 配置拉起；拉不起来就停下回报，
   不要改配置、不要重装。
3. 取本地中枢的调度员 token（在 ~/.pack/local-hub 目录内找，只在内存/本机
   命令里用，不打印、不写进任何文件、不进仓库）。
4. 用调度员身份在本地中枢建一个配对码：成员名 pair-evidence-1120、
   provider=codex、绑定本地中枢里现有的主房间（先 room_list 看本地房间名，
   用它实际的名字）。配对码只在本机命令里用一次，不落盘、不进证据文件。
5. 从 pack-repo 的 runtime/pack-agent.mjs 拷一份到临时目录，用独立 profile
   完成配对：node pack-agent.mjs pair --hub http://127.0.0.1:8794
   --code <配对码> --profile local-evidence。再跑 check-in 与 inbox 各一次
   （预期：配对成功、0 未读、退出码 0）。
6. 留证：把下列事实写进 docs/external-agent/LOCAL-PAIR-EVIDENCE.md 并提交
   入仓——本地中枢上新成员的 id、name、kind=external、joined_msg_id；本地
   配置文件路径与权限（应为 0600）；check-in/inbox 的退出码与未读数；执行
   时间。证据文件里不许出现配对码与任何 token。
7. 收尾：用调度员身份把 pair-evidence-1120 从本地中枢移除
   （member_remove），在证据文件里注明已移除；删掉临时目录里的脚本副本
   与 local-evidence profile 配置。
8. 回报：证据文件路径、提交号、每步成败一句话。任何一步失败就停在那一步
   回报，不要绕路硬做。
