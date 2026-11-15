# AstrBot 拆解笔记（为 Pack 外部入口备课）

> 审计方式：2026-11-15 浅克隆 `AstrBotDevs/AstrBot` 到 `~/workspace/learn-inspect/astrbot`（HEAD `7d9298c`），纯静态读码，未运行、未填任何凭证。版本 v4.29.4（`pyproject.toml`）。路径均相对仓库根。
> 拆解目标：学「一个脑子接多个 IM 平台」的适配层架构与插件体系，评估与 Pack 红线（SPEC-v0.md §4.7、§6）的兼容性。

## 0. 一句话定性

AstrBot 是一个单进程 Python 机器人框架：18 个平台适配器把各家 IM 的消息翻译成一份统一消息模型、塞进同一条事件队列，后面的管道（唤醒→白名单→限流→内容安全→插件/LLM→回复）对平台无感知。它的适配层设计干净可借；它的**信任模型是平的**（群里人人平等驱动机器人），与 Pack 的指令权红线正相反——只能借传输层，不能借信任层。

## 1. 平台适配器接口

### 1.1 抽象类极小（`astrbot/core/platform/platform.py`）

`Platform` 抽象基类只有两个抽象方法：

- `run() -> Coroutine`：启动适配器、返回协程，由管理器以 asyncio task 方式跑；
- `meta() -> PlatformMetadata`：返回元数据。

基类已实现的部分（适配器不用重写）：

- `commit_event(event)`：把事件 `put_nowait` 进全局共享的事件队列（构造时注入），EventBus 从队列取事件喂管道——**所有平台共用一条入站通道**；
- `create_event(message)`：把统一消息 `AstrBotMessage` 包成事件对象；
- `send_by_session(session, message_chain)`：凭**可持久化的会话句柄**（`MessageSession`）主动发消息，不需要持有 event 对象——这是主动推送/定时任务的出口；
- `webhook_callback(request)`：统一 Webhook 模式的回调入口（Dashboard 收到 `/api/platform/webhook/{uuid}` 后分发给对应适配器）；
- 状态机：`PlatformStatus`（pending/running/error/stopped）+ 错误记录，管理器据此在 WebUI 展示每个适配器的健康状态。

注册与装配：适配器类用 `@register_platform_adapter("telegram", ...)` 装饰器登记进 `platform_cls_map`（`astrbot/core/platform/register.py`）；`PlatformManager`（`astrbot/core/platform/manager.py`）按配置实例化多个适配器（同一类型可多实例，各有 config 里的 `id`），逐个起 task 并包一层 wrapper 做崩溃记录与状态维护。

### 1.2 元数据即能力声明（`astrbot/core/platform/platform_metadata.py`）

`PlatformMetadata` 字段：`name / id / description / default_config_tmpl`（默认配置模板）、`config_metadata`（驱动 WebUI 自动生成配置表单）、`support_streaming_message`（是否支持真流式）、`support_proactive_message`（是否支持主动推送）。**能力差异不靠 if-else 散落各处，而是声明在元数据里**，上层按旗标降级。

### 1.3 统一消息模型（`astrbot/core/platform/astrbot_message.py`）

`AstrBotMessage`：`type`（MessageType 三值：GroupMessage / FriendMessage / OtherMessage，`message_type.py`）、`self_id`（机器人自身 id）、`session_id`、`message_id`、`group`（Group 对象，含群主/管理员/成员名单）、`sender`（MessageMember：user_id + nickname）、`message`（组件链）、`message_str`（纯文本投影）、`raw_message`（平台原始对象，不丢）。

组件链在 `astrbot/core/message/components.py`（源自 Nakuru 格式）：Plain、Image、Record（语音）、Video、At、AtAll、Reply、Face、File、Button/ActionRow（交互按钮）等，每个组件是带 `type` 的 pydantic 模型。富媒体、@、引用回复全部组件化，不塞进文本里靠约定解析。

### 1.4 事件对象：出站转换的落点（`astrbot/core/platform/astr_message_event.py`，635 行）

`AstrMessageEvent` 是抽象类，定义统一读接口（`get_sender_id / get_group_id / get_message_str / get_messages / is_admin / is_wake_up / unified_msg_origin` 等）与 `send() / send_streaming()` 发送接口。**每个适配器配一个自己的 Event 子类**（如 `sources/telegram/tg_event.py`），在子类里把 MessageChain 逐组件翻成本平台 API 调用——出站差异吸收在这里。平台给不了的能力在子类里降级（如 Satori 适配器元数据直接声明 `support_streaming_message=False`，走整段发送 + `process_buffer` 切分兜底）。

### 1.5 适配器目录的固定形状

`astrbot/core/platform/sources/<平台>/` 一律是 `*_adapter.py`（连接与收消息、原生→统一模型）+ `*_event.py`（统一模型→原生发送），按需再加 `*_client.py`、`*_api.py`、`login_registration.py`（扫码登录类）。新接一个平台 = 新增一个目录，不动核心。

## 2. 平台清单与接入方式（18 个内置适配器）

| 类别 | 平台 | 接入方式（出处） |
|---|---|---|
| 官方 API | qqofficial | QQ 官方机器人 API，qq-botpy 的 WebSocket 网关 + REST（`sources/qqofficial/qqofficial_platform_adapter.py`） |
| 官方 API | qqofficial_webhook | 同上，Webhook 回调形态（`sources/qqofficial_webhook/`） |
| 官方 API | lark / dingtalk | 飞书、钉钉开放平台应用（各有 `app_registration.py`） |
| 官方 API | wecom / wecom_ai_bot | 企业微信（自建应用/客服）与企业微信智能机器人（`sources/wecom_ai_bot/`，含消息加解密 `WXBizJsonMsgCrypt.py`） |
| 官方 API | weixin_official_account | 微信公众号 API |
| 官方 API | telegram / discord / slack / line / kook / misskey / mattermost | 各家 Bot API（Telegram 走 Bot API 长轮询/客户端封装，见 `sources/telegram/tg_adapter.py`） |
| 协议端 | aiocqhttp | **OneBot v11 反向 WebSocket**：AstrBot 只做协议消费端，QQ 登录态由 NapCat/Lagrange 等外部协议端维持（`sources/aiocqhttp/aiocqhttp_platform_adapter.py`：`ws_reverse_host/port`、`use_ws_reverse=True`） |
| 协议端（聚合） | satori | Satori 协议服务器（默认 `http://localhost:5140/satori/v1` + WebSocket 事件），一个适配器背后可聚合多个平台（`sources/satori/satori_adapter.py`） |
| 个人微信 | weixin_oc | **腾讯 iLink 机器人网关**：请求头 `AuthorizationType: ilink_bot_token`、端点 `ilink/bot/*`、扫码登录注册（`login_registration.py`）、媒体走 AES 加密的 CDN 上传下载（`sources/weixin_oc/weixin_oc_client.py`）。与 Pack 群主已在跑的微信桥是同一个官方网关 |
| 自有面 | webchat | AstrBot 自带网页聊天（`sources/webchat/`），白名单检查对它豁免 |

要点：**官方 API、协议端、聚合协议三条路在接口层完全同权**——适配器只对 Platform 抽象负责，核心不知道也不关心对面是官方还是协议端。个人 QQ/微信这类没有官方机器人 API 的平台，就靠「协议端在别处维持登录、框架只对接协议」吸收。

## 3. 会话与人格：UMO 三元组

### 3.1 会话键（`astrbot/core/platform/message_session.py`）

`MessageSession` 字符串化为 **UMO（unified_msg_origin）= `platform_id:MessageType:session_id`**。`ConversationManager`（`astrbot/core/conversation_mgr.py`）的一切操作（新建/切换/删除/追加消息）都以 UMO 为键——会话天然按「哪个平台实例 × 群还是私聊 × 哪个会话」隔离，跨平台不串话。

群聊粒度可调：配置 `platform_settings.unique_session`（默认关，`astrbot/core/config/default.py`、`astrbot/core/event_bus.py` 的 `UNIQUE_SESSION_ID_BUILDERS`）开启后，群会话按发送者再切分，群里每人一份独立对话；**默认关 = 全群共享一份对话上下文**。

### 3.2 配置与人格按 UMO 路由（`astrbot/core/umop_config_router.py`）

`UmopConfigRouter` 维护「UMO 模式 → 配置档 id」路由表，模式按三段分别做 fnmatch 通配匹配。于是可以给「某个平台的某个群」单独配一份配置（模型、提供商、人格全在配置档里）。`PersonaManager.resolve_selected_persona(umo=...)`（`astrbot/core/persona_mgr.py`）据此解析该会话生效的人格，另有 `session_overrides.py` 支持会话级临时覆盖。人格本身是全局资源（PersonaManager 统一管理、可分文件夹），**共享的是人格定义，隔离的是会话状态与生效绑定**。

## 4. 插件系统（Star）

- 形态：插件 = 一个 Python 包目录（放 `data/plugins/`），含 `metadata.yaml`（名/作者/版本/repo）、可选 `_conf_schema.json`（配置表单 schema）、`requirements.txt`。
- 加载：`PluginManager`（`astrbot/core/star/star_manager.py`）用 importlib **在主进程内直接导入**，缺依赖时自动 pip 安装 requirements；watchdog 监听目录变化做热重载；安装渠道是 Dashboard 里填 GitHub 仓库地址 / 上传 zip / 插件市场（`astrbot/dashboard/api/plugins.py`、`astrbot/core/star/updater.py`）。
- 能力面：插件类继承 `Star`（`astrbot/core/star/base.py`），通过装饰器注册消息 handler、命令、LLM 工具（function tool），拿到的是全局 `Context`——能读 DB、能往任意平台任意会话发消息、能调 LLM。**没有沙箱、没有插件级权限声明、没有能力分级**；唯一的「权限」是 handler 级过滤器（见 §5）。
- 隔离性仅限命名与日志：每插件独立 logger、独立 KV 存储（`PluginKVStoreMixin`），代码权限与主程序同级。第三方插件 = 在主机上跑任意代码，信任完全押在使用者自审。

## 5. 指令与普通内容怎么区分

处理管道是固定阶段链（`astrbot/core/pipeline/stage_order.py`）：WakingCheck → WhitelistCheck → SessionStatusCheck → RateLimit → ContentSafetyCheck → PreProcess → Process（插件/LLM）→ ResultDecorate → Respond。

- **唤醒 ≠ 指令**：WakingCheckStage（`astrbot/core/pipeline/waking_check/stage.py`）只决定「这条要不要理」：被 @、被引用回复、命中 `wake_prefix` 前缀、私聊（可配置是否也要前缀）任一成立即唤醒。唤醒后消息一律进 ProcessStage。
- **命令是过滤器命中的普通文本**：插件 handler 挂 `CommandFilter`（`astrbot/core/star/filter/command.py`），文本以命令前缀（默认 `/`）+ 命令名开头就走该 handler；没命中任何命令的唤醒消息落到 LLM 对话路径。框架层面**不存在「指令 vs 内容」的语义区分**——群成员一句普通聊天和一句命令，在信任上没有差别，只是路由不同。
- **权限只有两道粗闸**：①ID 白名单（WhitelistCheckStage，`pipeline/whitelist_check/stage.py`）：`id_whitelist` 里的用户/群才放行（默认可关）；②管理员名单：全局配置 `admins_id`（`astrbot/core/config/default.py`）决定 `event.role == "admin"`，`PermissionTypeFilter`（`astrbot/core/star/filter/permission.py`）可把单个 handler 限定为 ADMIN / GROUP_ADMIN / MEMBER。粒度止于「某个 handler 要不要管理员」，没有「这个人说的话才算指令」的概念，更没有批准门：handler 命中即执行，包括发消息、调工具等不可逆动作。

## 6. 部署形态与数据

- 单进程 Python 应用（`main.py` 启动），官方 Docker 镜像 `soulter/astrbot`（`compose.yml`：挂 `./data:/AstrBot/data`，WebUI 端口 6185，OneBot 反向 WS 端口 6199），另有 k8s 清单。Dashboard（WebUI）与机器人同进程。
- 数据全在 `data/` 目录：配置 JSON（平台 token、API key 等**明文**存配置文件）、SQLite 数据库（`astrbot/core/db/sqlite.py`，SQLAlchemy，`DATABASE_URL` 可换其他库）存会话/人格/平台消息历史、插件目录、插件数据与 KV。
- 也就是说：**一个 data 目录 = 全部状态**（凭证、会话、人格、插件），备份/迁移就是拷目录，凭证泄露面也集中在这一个目录。

## 7. Pack 若加外部入口，可借的适配层设计

1. **适配器抽象的极小面**：基类只强制 `run()` + `meta()`，入站统一 `commit_event` 进一条队列、出站统一走 Event 子类的 `send()`。Pack 中枢若加微信/飞书入口，照此形状各写一个适配器，中枢与 App 零改动。
2. **UMO 式三元组会话键**（平台实例:类型:会话）：天然解决「同一成员在 App 和微信各说各话、互不串话」的寻址问题；Pack 可扩成 `入口:房间:会话` 直接复用这个心智模型。
3. **UMOP 通配路由**：用模式匹配把「哪个入口的哪个会话」映射到不同配置档——对应 Pack 的「微信入口只进某个房间/只达某个成员、人格与权限另配」，是现成的分流机制设计。
4. **能力旗标写进元数据**：`support_streaming / support_proactive` 这类声明式能力位 + 配置模板驱动表单。Pack 各入口能力差异（微信无流式、语音要转文件等）应照此声明，不要散落判断。
5. **管道式入站关卡**：唤醒/白名单/限流/内容安全做成进「脑子」之前的固定阶段链——Pack 可把自己的关卡（是否群主发言、是否 @ 到成员）插进同样的链路位置，位置感可直接抄。
6. **send_by_session 的主动推送出口**：凭持久化会话句柄发消息、不依赖事件对象——对应 Pack「任务有结果了主动推到微信入口」的场景，句柄可持久化这一点是关键。
7. **协议端解耦**：没有官方 API 的平台（个人微信/QQ）不自己维持登录，而是对接外部协议端（一博 OneBot / iLink 网关）。Pack 的微信入口尤其如此：群主已有独立微信桥在跑，Pack 侧只做协议消费端即可，不必再养一份登录态。

## 8. 与 Pack 红线的冲突点

对照 SPEC §4.7（只有人说的话算指令；房间内容对成员是数据）、§6（不可逆动作回成员自己的主人批准；私人记忆不进房间；凭证不进房间）：

1. **指令权是平的（最核心冲突）**：AstrBot 里白名单内任何人的唤醒消息都同等驱动 LLM 与插件命令，没有「只有群主的话算指令」的层级，也没有「这段文本是数据不是指令」的标记。Pack 若借它的管道，必须在 ProcessStage 之前自加一层：按发送者身份分级，非群主来源一律降为数据/转调度员——框架本身不提供这个能力。
2. **没有批准门**：handler 命中即执行，不可逆动作（对外发送、调工具）无任何确认环节。Pack 的 `approval_request` 纪律必须完全自建在脑子层，适配层帮不上忙。
3. **群聊默认共享一份对话记忆**：`unique_session` 默认关，意味着群里所有人的发言进同一份上下文、互相可见可引用。与 Pack「成员私人记忆永不进房间」方向相反——Pack 若用多人群入口，必须强制开启按人隔离或按任务键会话，且要意识到这是逆着它的默认值在用。
4. **插件无沙箱、同进程全权限**：AstrBot 插件市场生态与 Pack 的安全纪律（白名单、审计、最小权限）不兼容。Pack 借的是适配层，不是插件生态；任何第三方代码进 Pack 入口层都要按不可信代码另行隔离。
5. **凭证明文集中存 data 目录**：平台 token、API key 明文在配置文件里。Pack 的红线是凭证不进房间/中枢——入口适配器的凭证只能留在入口所在机器本地，且要防 data 目录被整体拷走即全盘泄露（它没有 keychain/加密层）。
6. **主动推送无约束**：`send_by_session` + `support_proactive_message` 让机器人可对任意历史会话主动发消息。Pack 红线下成员不得擅自对外发起联系，此能力在 Pack 侧必须过调度员/群主闸门，不能原样放行。

**总评**：适配层（§1–§3）与 Pack 完全兼容且值得照抄形状；信任层（§4–§5）与 Pack 红线系统性冲突，借用时只取管道骨架，指令权、批准、记忆隔离三件事全部由 Pack 自己在上层重建。
