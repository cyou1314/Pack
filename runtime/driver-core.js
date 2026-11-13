/**
 * Pack ego 驾驶层·控制流（由 runtime.mjs 拼装后喂给 `ego-browser nodejs` 的 stdin）。
 *
 * 拼装顺序：PACK_TASK 注入行 -> driver-primitives.js -> 本文件。
 * 本文件不直接执行；原语函数（egoOpenSpace 等）由 driver-primitives.js 提供。
 * 红线逻辑（勿改）：新建对话后核对对话标识变化才发送；回复须连续两次读到
 * 相同文本才算确认稳定；只输出最后一条助手回复；整页快照不落盘不输出。
 */
async function packDriverMain() {
  const { profileId, spaceName, message } = globalThis.PACK_TASK;
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
    let last = "", stableCount = 0, confirmed = false;
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      const cur = await egoLastReply(page);
      if (cur.text && cur.text === last) {
        stableCount++;
        if (stableCount >= 2) { confirmed = true; break; }
      } else {
        if (cur.text) last = cur.text;
        stableCount = 0;
      }
    }
    if (!last || !confirmed) {
      return done({ ok: false, threadVerified: true, error: "reply_not_confirmed_stable" });
    }
    done({ ok: true, threadVerified: true, reply: last, chatUrl: await egoChatUrl(page) });
  } catch (e) {
    done({ ok: false, threadVerified: false, error: String((e && e.message) || e) });
  }
}
packDriverMain();
