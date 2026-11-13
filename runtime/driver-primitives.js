/**
 * Pack ego 驾驶层·原语（Mac 本地文件，Codex 按本机 `ego-browser skill` 指南实现）。
 *
 * 本文件是模板：仓库里的函数是占位桩。Mac 上 ~/pack-runtime/driver-primitives.js
 * 才是真实现（Codex 已按本机指南写好 6 个函数，签名必须与这里一致），
 * 拉取仓库更新时不要用本模板覆盖 Mac 上的真实现；真实现应提交回仓库存档。
 *
 * 每个原语的语义：
 *   egoOpenSpace(profileId, spaceName) -> space 句柄；profileId 只在新建 Space 时生效，
 *       已有同名 Space 须核对其归属 Profile，不符要报错。
 *   egoOpenMuse(space) -> page 句柄（在 space 里打开 muse.ai 并等加载完成）。
 *   egoCurrentThreadId(page) -> 当前对话的稳定标识（URL 里的对话 id 等），取不到返回 ""。
 *   egoNewChat(page) -> 点「新聊天」并等新对话界面就绪。
 *   egoSend(page, text) -> 把 text 填入输入框并发送。
 *   egoLastReply(page) -> { text }：最后一条助手回复的纯文本（只要回复，不要整页快照外传）。
 *   egoChatUrl(page) -> 当前页面 URL 字符串。
 */
async function egoOpenSpace(profileId, spaceName) { throw new Error("primitive not adapted: egoOpenSpace"); }
async function egoOpenMuse(space) { throw new Error("primitive not adapted: egoOpenMuse"); }
async function egoCurrentThreadId(page) { throw new Error("primitive not adapted: egoCurrentThreadId"); }
async function egoNewChat(page) { throw new Error("primitive not adapted: egoNewChat"); }
async function egoSend(page, text) { throw new Error("primitive not adapted: egoSend"); }
async function egoLastReply(page) { throw new Error("primitive not adapted: egoLastReply"); }
async function egoChatUrl(page) { return ""; }
