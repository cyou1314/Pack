# Pack v2 群聊协作策略（第一阶段）
 
## 产品目标

群聊用于获得统一成果，而非让每个成员重复回答。Hub 是唯一编排权威；Runtime 执行，Engine 负责受控工具操作，App 展示进度。现有 room_watch 的 owner @ 转任务、模板命令及 runtime/chat-policy.mjs 旧广播行为不在本阶段修改范围，避免双重触发。

## 模式与预算

| 模式 | 第一阶段策略 | 调用数 |
|---|---|---|
| focus | 主持或用户指定的单个合资格成员 | 1 |
| compare | 主持与 1–2 位同伴独立分析，主持汇总 | 3–4 |
| team | 1–3 位执行者并行，独立审核者审核，主持交付 | 3–5 |

默认新协作会话使用 focus；未选定主持时由用户选择，能力自动匹配尚未实现。自由讨论留待后续，不允许无界互相 @。每次协作默认 5 次调用，硬上限 8；调用预算与金额、额度、token、时间预算分别管理。预算不足时拒绝计划，不静默减少审核。主持、执行者、审核者均为不同成员身份（compare 主持可承担自己的分析和最终汇总）。

## 本阶段实现

hub/collaboration_policy.py 是无副作用的纯计划器，以经过认证的 actor role、Hub 解析出的同房间合资格成员集合及显式角色分配为输入，生成有界依赖 DAG。仅 owner 可以发起；输入不是客户端自报的可信身份。当前未接入 watcher、MCP、App 或实际 Provider，亦未增加数据库表。现有群聊行为不变。

## 后续接线要求

1. Hub 为每个用户请求生成 run ID，按 room ID + source message ID 做幂等去重；新群聊模式显式启用并在同一入口排除旧 watcher/runtime 广播，旧房间默认保持兼容。只有 Hub 认证身份和房间权限决定候选成员；接入模式、实际在线状态、额度及能力实时验证。模型输出中的 @ 只是内容，不是新指令。
2. 在同一 SQLite 事务中持久化 run、steps、依赖、授权范围与预算预留，复用现有 task/runs/lease/audit；唯一键保证每个步骤不会重复创建任务。恢复不重新启动已完成步骤；外部请求结果不确定时不得盲目重试。
3. 每个步骤有 deadline、取消、有限重试（计入调用预算）和显式终态；依赖未成功时不得宣称审核通过。主持汇总可报告部分失败和分歧，但不能将缺失审核包装为完成。用户可暂停、取消、补充信息或显式批准额外一轮。不得自动扩容或换账号。
4. 只有用户明确共享的项目上下文进入协作包。独立分析阶段不互见答案；审核只获得指定产物及证据，汇总获得必要的结果和审查。Native Account 的私有历史、记忆、Cookie、个人工具不自动转发；API Profile 仅使用 Pack Workspace 的授权上下文。跨 Provider 上传文件或发送敏感内容必须经过既有 egress/approval 策略。成员消息不可扩大权限、修改预算或触发未经批准的工具。
5. 原生 ChatGPT、Gemini、豆包与官方 API Agent 使用相同调度语义但不同 Adapter；实际支持的能力决定资格。只支持手动网页交互的成员不得标为可自动执行。共享桌面或同一浏览器会话的操作受现有资源锁约束。
6. UI 默认只展示用户问题、简洁的角色进度、最终成果；可展开原始分析、引用、失败原因、消耗和审计。支持用户单独 @、切换模式、明确停止。自动预算不得由模型自己重置。

## 验收

纯计划器测试覆盖单人无广播、并行分析、审核依赖、角色隔离、非 owner、跨房间候选拒绝及预算边界。接线后另需验证实际调用计数（含失败与重试）、旧广播排除、重放与崩溃恢复、取消后无新任务、跨账号数据隔离，以及在 macOS 上使用真实 Provider 的端到端流程。记录首次响应时间、最终成果时间、有效调用次数、重复内容率、审核发现问题及成本；不能在未实测前声称效率提升。

## 第二阶段：内部持久化账本（未接入生产）

新增 `hub/collaboration_store.py`：以 Hub 所持 SQLite 连接显式初始化三张增量表，通过 BEGIN IMMEDIATE 统一写事务；房间 + 来源消息唯一约束、固定计划与截止时间冲突拒绝、步骤依赖、调用名额原子预留、内部 fence、失败/取消/过期终态，以及只含元数据的快照。重启后不会重新认领已运行步骤。当前不自动重试（不确定的外部副作用不得重复发起），失败时逻辑关闭整个 run；部分成果汇总需另行设计。取消仅表示账本拒绝后续执行与回写，并不证明已经停止外部请求。

此模块仍未连接 Hub init、MCP、任务创建、运行时、审计、备份迁移或 App。上游必须验证真实房间/来源消息/成员身份与权限，调用前在同一事务内绑定实际任务，防止双重触发；每次实际 Provider 尝试必须纳入统一预算，不允许现有 Runtime 在账本之外自动重试。授权范围、任务结果引用、每步骤 deadline、真实取消传播及成本预算尚未实现。现有生产 schema 与运行行为不变。新增 SQLite 实际事务、并发连接、恢复、依赖、失败、过期及取消回归测试，纳入 CI。

## 第三阶段：任务卡事务绑定（内部接线层）

新增 collaboration_dispatch.py 与 collaboration_task_links 增量表。协作计划在此层必须使用稳定成员 ID 的十进制字符串而非显示名。ready_steps 仅返回依赖已成功、未绑定步骤；bind_task 校验任务同房、同成员、open 且 attempts=0，并强制步骤/任务一对一。调用方必须在同一写事务内复用既有开卡、消息及审计操作；失败整笔回滚。reserve_claim 在既有 task_claim UPDATE 前原子占用一次调用预算，失败不得退回普通认领路径。reconcile_terminal 以可信任务状态判断完成，review 不算成功，已尝试后重新 open 则失败关闭。测试使用与 Hub 相关字段一致的最小 SQLite fixture，不等于生产 Hub 集成测试。

**保持关闭**：没有在 Hub init、MCP、watcher 或 Runtime 中启用此模块。启用前必须统一守卫所有任务变更入口（claim、complete、review、block、release、transfer、auto relay、sweeper、member_remove、cancel 等）、实际 Provider 调用重试、取消传播、共享结果授权与审计。绑定任务未认领前被取消、成员移除或 run 过期时，亦须通过已有取消机制同步处理；当前账本不会自动修改真实任务表。不要仅接 task_claim 就开放群聊。原有 task_list/room_read 对共享内容的可见性也需独立验收。

## 第四阶段：任务变更安全防线（未启用）

新增 collaboration_guard.py：可显式安装 SQLite triggers，对已绑定的 tasks 行约束身份/输入不变、认领须先有账本预留、尝试数最多一次、持有人 token 不可替换、完成必须经过 review、禁止 reopen/transfer/delete/replace，终止先关闭账本再清理卡。未绑定的既有任务不受影响。close_linked_tasks 通过同事务的可信取消回调清理未完成卡，验证任务取消与 token/lease 清除；回调尚未接入 Hub 的 run/desktop/audit 清理。触发器不验证操作者、外部请求、截止时间或审核内容，不能替代 Hub 权限、时间检查和真实 Provider 取消；也不是针对任意数据库写入者的安全沙箱。

审计 hub.py 的 SQL 修改入口：_auto_relay、sweep_once（转交过期、租约回收）、member_remove、task_claim、_task_update_impl、task_complete、task_block、task_review、task_cancel、task_release、task_transfer、task_accept_transfer，以及开卡/排班/加急/退避修改。尤其 sweeper 与 member_remove 的批量 SQL 不能仅靠 trigger 拒绝，否则会回滚整个批次；启用前必须先分流协作任务并使用同事务的终止清理。现阶段 guard 仅测试显式安装，不在生产 init 安装，不发布协作任务，不改变旧群聊。后续还需守卫实际 Provider 重试、共享内容访问和 deadline，并测试完整 Hub 事务及运行时。

## 第五阶段：真实 Hub 终止路径（仍不开放协作创建）

hub.py 已接入现有 task_cancel、sweep_once、member_remove 和 _auto_relay 路径。仅检测到显式初始化的 collaboration_task_links 表时启用相应分支；正常 Hub init 不创建该表，也不会导入新增模块。取消/超时/成员移除在同一事务中停止 run、取消关联卡、关闭既有 runs、释放桌面锁、清理 pending transfer 并写审计。sweeper 在旧回收前独立写事务处理协作截止时间与租约失效，旧回收及未认领接力跳过已绑定卡。成员移除覆盖未绑定步骤。新增真实 Hub schema + 本地 MCP 测试，包含旧卡兼容性。

仍未开放协作创建或认领：task_claim/reserve、task_review/reconcile、block/release/transfer 等入口及 Provider 重试、deadline 的实时检查尚须完整接线。终止只是 Hub 逻辑终止，不能证明外部请求已停止。安装器 build-dmg.sh 与 manager.py 固定清单未包含协作模块，当前正常安装不启用 schema；启用前必须补齐打包、升级、卸载、回滚和测试。不得手工在生产初始化协作表。

## 第六阶段：实际认领及审核路径（测试启用）

Hub task_claim 在同一事务内预留一次协作预算，失败回滚；持有人路径对已绑定任务检查 run 状态、截止时间、租约，并使用写事务；run_start 对已登记过的协作任务拒绝再次登记。task_review 通过时同步成功步骤、拒绝时终止整个 run；明确 partial/failed 的信封不得作为成功批准。task_block 直接结束协作并保留失败类别，quota 保留耗尽事实；task_transfer、task_accept_transfer、task_release 禁止对绑定任务操作。真实 MCP fixture 增加认领、重复登记、交活、审核、失败等测试。仍仅在测试显式初始化账本，未开放生产创建；当前认领仅校验现有成员身份/房间等基本边界，真实 Provider readiness、结果共享授权、审核语义、Provider 内部调用计量、实际取消传播、打包等仍待验收。run_start 的一次登记不等于保证一次模型调用；外部执行体必须遵循预算协议。

## 第七阶段：macOS 安装包兼容性

build-dmg.sh 和 manager.py 的程序文件白名单已纳入四个协作模块，新增构建/安装前文件检查、staging 导入检查、重装残留检查、卸载清理及 Ubuntu/macOS CI 安装事务回归。保留现有数据库、账号凭证、附件及配置，不复制整个 Hub 目录。当前不启用协作 schema，不改变生产功能；安装事务测试为隔离 mock，不等于真实 DMG 构建或签名/安装验收。仍需要固定外部 Engine 与审核锁，且不支持将已启用协作数据的数据库降级到旧 Hub。

## 第八阶段：按步骤授权的文本 Context Gateway（测试启用）

新增 collaboration_context.py 与 context grants 表：真实 owner 身份（非 dispatcher 或 Agent 自报）授权精确的同房 owner chat 消息及已通过审核的直接依赖任务 result；根消息及全部直接依赖必需，禁止任意其他任务、私有记忆、附件、全文历史。单步骤最多 8 项，序列化数据 16 KiB 上限；超限拒绝而非静默截断。授权固定接收成员 ID、provider 标识与内容指纹，读取重新检查房间、成员、租约、run、版本和撤销。新 MCP collaboration_context_get 仅在显式创建 schema 后注册，并要求实际任务 claim token；owner grant/revoke 目前仅可信内部函数，没有开放外部授权写工具。测试覆盖依赖、跨房、篡改、预算、撤销、真实 MCP holder。安装器同步加入模块白名单。

**尚未达到全局隔离**：旧 task_list/room_read/inbox/attachment 等读取面仍保留原有范围，已有任务 body/result 及 report 仍可能被其他内部成员读到。新 Gateway 不等于撤销旧数据权限；不可发布真实敏感协作内容，生产 schema 不启用。provider 字符串不是账号或会话身份；未验证真实账号绑定、跨 Provider 出口授权、运行时实际消费及调用预算。已交付数据不能被撤销收回。后续应先为协作任务使用最小公开占位内容、保护所有旧读取路径、建立 owner 授权入口与 adapter 账号绑定，再启用生产。禁止将未授权数据写入 task body 或公开 room report。

## 第九阶段：旧读取面收敛（测试启用）

普通 Agent 的 task_list 对关联卡只返回状态/分工元数据与上下文入口占位，隐藏 title/body/result/envelope/review_note；room_read_new 和共用 inbox 格式化路径遮蔽关联消息正文、payload、附件、引用及任务标题，保留 ID/排序以维持游标。owner/dispatcher 仍有审阅权限。协作 task_update 仅写进度通知，task_complete 仅写交活通知，拒绝附带附件；结果仍存既有 task 行供审核与已授权依赖读取。attachment_list/get 拒绝普通成员读取关联附件，未绑定附件只允许上传者读取；旧 egress_check 不把关联卡当作跨 Provider 授权。仅 schema 存在时生效，旧库不改行为。

这不是完整数据防泄露沙箱：普通 room_post、自主复制、历史公开 owner 消息、全局共享记忆、摘要、独立上传后发布、直接文件/DB 访问、外部 Provider 已收数据及其他侧通道仍需单独限制。尚未开放生产 schema/创建入口，不能把敏感内容交给不可信执行体。后续需将协作成员的工具面缩小至独立执行协议，明确输入/输出公开等级及真正的 Provider 账号和出口授权；附件暂不自动分发。

## 第十阶段：专用 Agent 执行身份（测试启用）

新增内部 executor binding：真实 owner 为新成员固定 provider、native/api 模式与本地随机 UUID 账号引用；不接受密钥、Cookie 或邮箱作引用，绑定不可修改/复用，撤销后保留限制，不降级为普通成员。所有 MCP 工具入口追加默认拒绝白名单（heartbeat、自己的 task_list、claim、context_get、run_start/finish、update、complete/block），与原 external/access_mode 闸叠加。专用成员只列当前已授权绑定卡，认领前重验 grant 与来源，run_start/complete 再验；交付要求已登记 run，文本和 envelope 分别限制 16 KiB。真实 MCP 测试覆盖旧工具拒绝、无授权不消耗预算、绑定读取、正常交付和撤销。

此为测试环境的专用身份协议，不是实际 Provider 登录验证。UUID 仅本地逻辑引用，未来可信 adapter 必须验证真实账号/session/Keychain 绑定。暂未提供公开 provisioning/revoke API，撤销阻止新调用不代表中断已发出的模型请求或自动关闭所有活跃 run。普通旧成员仍可参与测试协作，生产入口仍关闭；启用前应强制专用身份、事务化创建/撤销、工具旁路审计及运行时接入。

## 第十一阶段：Provider-neutral Runtime 执行契约（离线验证）

新增 `runtime/collaboration-runner.mjs`：复用 Swift `AgentProfile` provider/mode/UUID credential reference 契约，不引入第二份账号数据。可信宿主注入 no-retry Hub transport、profile resolver、adapter resolver 与对精确请求 SHA-256/目标账号的显式出口授权。流程 claim → context_get → 核对账号/模式/能力 → 真实本地账号检查回调 → 出口授权 → run_start → context_get 复验 → 单次 adapter.execute → task_complete。失败按固定原因 block，不传播原始上游异常；交付响应不明时不重发、不擅自改卡。超时 AbortSignal 与调用结果不明触发进程内账号隔离；不能保证上游取消、不能跨进程恢复。明确 API token usage 或 unknown(null)，没有估算费用或伪造原生平台用量。新增 Node mock 回归覆盖双模式、越权、授权拒绝、变更、超时及交付不确定。Hub context 返回 deadline 供 runner 限制调用窗口。

**仍未接入实际 Provider**：resolver/verifyAccount/authorizeEgress/execute 均为可信宿主注入契约，mock 验证不代表真实账号验证、网络取消或一次模型请求。现有 runtime.mjs 和 installer 不启动此 runner；旧 runtime 工具自动重试不可直接用于本协议。真实接线前需实现本机单写者、持久化不确定调用隔离、可信的 Keychain/浏览器账号解析、出站授权与无重试 transport、用量审计和取消传播。Provider catalog 是候选，不是已实现适配器；保持生产协作关闭。

## 第十二阶段：设备本地账号解析与跨进程租约

Swift `AgentLocalAccountResolver` 通过现有 `AgentRegistryStore.profile(id:)` 查询固定 UUID，校验 provider/mode/credential reference/chat capability；API 只验证本机 vault 中凭证存在，不返回密钥或宣称上游登录已验证；native 明确返回仍需浏览器验证。Node `AccountExecutionLeases` 使用共享私有目录内的 UUID 独占 mkdir 租约，拒绝符号链接、非私有根及重复租约；只允许原持有者按 inode 校验 rmdir 自己的空目录，不按时间或 PID 自动破锁。Runner 强制注入账号租约，确认交付/明确失败才释放；超时、Provider 结果不明、交付不明保留标记，跨 runner/进程重启继续拒绝。新增 Swift mock vault 与 Node 本地文件系统回归。

边界：宿主必须指定所有执行器共同使用的预建私有目录；没有启动接线、GUI 恢复入口、自动恢复或断电持久性保证。普通同 UID 的恶意进程不受此协作锁约束；账号引用仍是本地 profile ID，不是经过 Provider 验证的唯一远端账号。真实 API transport、浏览器账号验证、Keychain 签名实机验收、跨语言宿主集成仍未完成。不得自动清理未知租约；用户应先核对上游请求和 Hub 状态后显式恢复。

## 第十三阶段：官方 API 文本适配器（未接生产）

新增 Swift AgentOfficialAPIAdapter：OpenAI Responses、Gemini Developer generateContent、火山方舟 Chat Completions，固定 HTTPS 官方地址、显式模型、输出预算、无工具/附件、无应用层自动重试。使用原注册表和 Keychain，按精确 HTTP body SHA-256/URL/目标 profile/deadline 经可信宿主显式授权后读取密钥，仅在本进程 HTTP header 使用；拒绝重定向，ephemeral URLSession，响应读取上限 1 MiB。输出只取完成的文本，不返回思维链；保留 API 返回的 token 用量，缺失则 null。Gemini 输出计量暂按 totalTokenCount - promptTokenCount（包括思考等额外 token，非仅可见文本；非计费结算）。OpenAI store=false 不代表零数据留存。方舟 max_completion_tokens 仅支持兼容模型，不自动退回其他参数。HTTP 不明确失败抛出固定错误，宿主应保留账号租约。新增注入 transport 的离线测试。

参考官方文档：https://developers.openai.com/api/reference/resources/responses/methods/create 、https://ai.google.dev/api/generate-content 、https://docs.volcengine.com/docs/ark/chat-api?lang=zh 。未验证真实 API key、模型兼容性、签名 Keychain、网络重定向实际行为、上游取消与网络层 exactly-once；未连接 Node runner/IPC/生产入口，可信宿主负责租约、审批与结果提交。API 原生工具和多模态仍关闭。

## 第十四阶段：Native Account 单次会话适配契约（未接生产）

新增 `runtime/browser-driver/native-account-adapter.mjs`，复用 BrowserSessions 与 Node Runner：ChatGPT/Gemini/豆包固定 origin 候选，单次验证/执行，可信宿主提供已登记的本地账号证明摘要、当前账号/model/memory/busy 检查、真实页面 driver 和准确发送授权。拒绝登录未完成、挑战、账号/模式/域不符；新建独立空会话（不清除原账号历史或强制临时聊天），按准确发送文本 SHA-256、会话及目标再授权；一次 sendOnce，按新 turnID 等待完成 assistant 文本，不回读旧会话、思维过程或私有记忆，不推算原生 token。取消或发送/回复不明确不重试。Runner 在明确未执行/完成时先关闭 adapter 再释放账号租约，close 失败保留租约，未知执行不强行关闭或重开。Node mock 回归覆盖身份、来源、拒绝授权、旧回复、部分输出及清理顺序。

尚无真实 Provider 页面 driver、登录账号证明采集/登记 UI、会话稳定 ID 映射、实际消息 DOM 与完成检测。固定域只是导航范围，不是所有网络请求的隔离策略；不能承诺保留原生记忆、工具可用性或阻止 provider 原生后台调用。必须遵守各平台条款与授权范围，不绕过登录、验证码、风控和权限。原生执行器未启动、未打包、未连接真实账号；生产入口仍关闭。

## 第十五阶段：API 执行宿主与 Hub 接线（未接生产）

新增 `collaboration_executor_info`（仅 opt-in schema 注册，专用 token 查询自己的绑定，撤销后拒绝）与 Swift `AgentAPICollaborationHost`：采用 Swift 内进程 API lane，复用 registry/vault/official adapter；Node 保留 native lane，两者使用同一预建私有目录下 UUID.lease 的 mkdir/rmdir 协作锁。App `DedicatedCollaborationHub` 工厂显式提供专用成员 token，使用现有 HubClient 单次调用，不经过旧自动重连重试或日志，不读 dispatcher 配置。流程身份确认→本地 credential presence→租约→claim→context→run_start→精确 HTTP 请求授权→重验上下文→真实 transport→单次交付；未知结果保留租约。Swift 模拟 Hub+HTTP 端到端测试和 Python 真实 MCP 身份测试。API 429 不视为周额度100%，宿主 block 使用 task 类别。Token usage 暂仅返回宿主，未持久入账。

这是可调用的 App 工厂而非已启动生产工作流。仍须提供安全的专用 token provisioning/存储、真实用户授权 UI、统一租约路径、Hub 创建/调度入口、原生真实页面 driver、签名 Keychain/真实 API 验收。无自动任务轮询、不传 API key 跨进程、不启用 schema，不新增 IPC 服务。Swift 与 Node 生命周期分别实现，后续需契约测试防漂移；现有 HubClient 底层 URLSession 不保证网络层 exactly-once，且现有响应读取并非流式限长。未知租约需显式人工恢复。

## 第十六阶段：私有协作创建及审核驱动派发（opt-in MCP）

新增 owner-only `collaboration_executor_bind`（仅新建成员）、`collaboration_create/status/cancel`。创建采用 owner+32 位随机 request_key 幂等、24 小时内固定 deadline、显式选择专用成员和预算；私有 body 存 collaboration_requests，房间只写非 chat 类型的占位消息，旧 watcher 不应触发普通 @ 派单。事务内创建 DAG、精确授权根请求、生成并绑定首批任务卡；完成 dispatcher 审核才自动发放下一批依赖结果与任务卡，后续派发通过 savepoint 全批回滚，授权失败则保留已审核结果并停止全组。无模型调用、无自动广播。真实 MCP 回归覆盖 owner/普通成员隔离、幂等、私有正文、compare 并行→汇总、预算不足回滚、取消。仅新 managed run 自动推进；旧测试 ledger 不变。

限制：未启用生产 schema，尚未接 UI 或安全专用 token 发放/撤销流程；绑定接口接受已由 dispatcher 登记的全新成员，不能证明其令牌从未泄露，实际生产应原子化 provision。私有请求只保护正常 Hub 工具读取，不是 DB 加密、备份隔离或任意本机代码沙箱。用户创建时授权选定 DAG 的直接依赖结果共享，实际 Provider 出口仍由宿主单独确认。当前执行器尚未消费 step role，下一阶段须将角色指令与预算固定到执行请求；实际平台调用未验收。普通 dispatcher 仍能审核；未加自动审核。

## 第十七阶段：固定角色执行规范

Hub 返回 role 与 max_output_tokens：answer/synthesis 4096、worker 3072、analysis/review 2048。Swift/Node 验证 step/role/预算；API 使用用户上限与角色上限的较小值，native 仅提供简洁输出指令，无法强制平台 token 限制。审核、分析、汇总的固定指令区别开，依赖结果仍为不可信数据。Node 429 不再自动标记周额度 100%。生产关闭，真实 Provider 验收仍待完成。

### 只读智能路由预览

owner-only `collaboration_route_preview` 按专用绑定的六种文本候选、在线心跳、空队列、未占用活跃 DAG、新鲜额度筛选。未知额度默认排除，需显式 opt-in；确定性排序及本地账号去重，不推断模型优劣。只读建议，仍需 owner 确认创建，不发授权或调用模型。候选资格不证明已登录、真实能力或费用；预览也不是预约或原子调度，真实创建/执行仍须验证。MCP 回归覆盖未知额度与活跃计划排除。生产仍关闭。
