/**
 * Pack ego 驾驶层（在 Mac 上由 `ego-browser nodejs driver.js` 执行）。
 *
 * 契约：
 *   stdin 读入 JSON: { profileId, spaceName, message }
 *   最后一行必须打印: DRIVER-RESULT {"ok":bool, "reply":string, "threadVerified":bool, "chatUrl":string, "error":string}
 *
 * ！！！下方 PRIMITIVES 区的 6 个函数必须按 Mac 本机 `ego-browser skill` 打印的
 * 指南原文适配（不同版本 helper 名不同，禁止凭记忆猜）。控制流与红线逻辑已写好，不要改：
 *   1. 绑 profileId 建/开 Space（profileId 只在新建时生效，已有 Space 须核对归属）
 *   2. 打开 muse.ai，新建对话，并核对「当前对话是刚建的新对话」（落点核对）：
 *      记录新建前对话标识 -> 点新建 -> 对话标识发生变化且页面无历史消息，才算 threadVerified=true
 *   3. 核对通过才发送 message；发送后轮询页面直到回复生成完毕（内容不再变化）
 *   4. 只提取最后一条助手回复的纯文本放进 reply；整页快照不得写文件、不得整段输出
 */

// ===== PRIMITIVES（按本机指南适配这一区）=====
async function egoOpenSpace(profileId, spaceName) { throw new Error("primitive not adapted: egoOpenSpace"); }
async function egoOpenMuse(space) { throw new Error("primitive not adapted: egoOpenMuse"); }
async function egoCurrentThreadId(page) { throw new Error("primitive not adapted: egoCurrentThreadId"); }
async function egoNewChat(page) { throw new Error("primitive not adapted: egoNewChat"); }
async function egoSend(page, text) { throw new Error("primitive not adapted: egoSend"); }
async function egoLastReply(page) { throw new Error("primitive not adapted: egoLastReply"); } // -> {text, stable}
async function egoChatUrl(page) { return ""; }

// ===== 控制流（勿改红线逻辑）=====
async function main() {
  let input = "";
  process.stdin.on("data", (d) => (input += d));
  await new Promise((r) => process.stdin.on("end", r));
  const { profileId, spaceName, message } = JSON.parse(input);
  const done = (obj) => console.log("DRIVER-RESULT " + JSON.stringify(obj));
  try {
    const space = await egoOpenSpace(profileId, spaceName);
    const page = await egoOpenMuse(space);
    const before = await egoCurrentThreadId(page);
    await egoNewChat(page);
    const after = await egoCurrentThreadId(page);
    const threadVerified = Boolean(after) && after !== before;
    if (!threadVerified) {
      return done({ ok: false, threadVerified: false, error: "thread_verify_failed: new chat not confirmed, nothing sent" });
    }
    await egoSend(page, message);
    let last = "", stableCount = 0;
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const cur = await egoLastReply(page);
      if (cur.text && cur.text === last) { stableCount++; if (stableCount >= 2) break; }
      else { last = cur.text || last; stableCount = 0; }
    }
    if (!last) return done({ ok: false, threadVerified: true, error: "no reply extracted" });
    done({ ok: true, threadVerified: true, reply: last, chatUrl: await egoChatUrl(page) });
  } catch (e) {
    done({ ok: false, threadVerified: false, error: String(e && e.message || e) });
  }
}
main();
