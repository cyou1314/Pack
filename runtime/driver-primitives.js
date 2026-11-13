// ===== PRIMITIVES（按本机指南适配这一区）=====
async function egoOpenSpace(profileId, spaceName) {
  const existing = (await listTaskSpaces()).find((s) => s.name === spaceName);
  if (existing && (existing.profileId !== profileId || existing.ownership !== "agent"))
    throw new Error("space profile or ownership mismatch");
  const space = existing ? await taskSpace(spaceName) : await taskSpace(spaceName, { profileId });
  const actual = (await listTaskSpaces()).find((s) => s.id === space.spaceId);
  if (!actual || actual.profileId !== profileId || actual.ownership !== "agent")
    throw new Error("space profile verification failed");
  return space;
}
async function egoOpenMuse(space) {
  const page = space.page("p1");
  await page.goto("https://muse.ai/");
  await page.waitForLoadState();
  await page.waitForSelector('loc=css:button[aria-label="新旁聊"]', { state: "visible", timeout: 30000 });
  return page;
}
async function egoCurrentThreadId(page) {
  const state = await page.evaluate(() => ({
    url: location.href,
    log: !!document.querySelector('[role="log"][aria-label="聊天消息"]'),
    count: document.querySelectorAll('[role="log"][aria-label="聊天消息"] [data-message-item]').length,
  }));
  if (!state.log) throw new Error("chat message log unavailable");
  if (new URL(state.url).pathname === "/thread/new" && state.count !== 0)
    throw new Error("new chat contains history");
  return state.url;
}
async function egoNewChat(page) {
  await page.click('loc=css:button[aria-label="新旁聊"]');
  await page.waitForURL("https://muse.ai/thread/new", { timeout: 30000 });
  await page.waitForFunction(() => {
    const log = document.querySelector('[role="log"][aria-label="聊天消息"]');
    return !!log && log.querySelectorAll('[data-message-item]').length === 0 &&
      !!document.querySelector('textarea[placeholder="消息"]');
  }, undefined, { timeout: 30000 });
}
async function egoSend(page, text) {
  const editor = 'loc=css:textarea[placeholder="消息"]';
  await page.fill(editor, text);
  const ready = await page.evaluate((expected) => {
    const log = document.querySelector('[role="log"][aria-label="聊天消息"]');
    return location.pathname === "/thread/new" && !!log &&
      log.querySelectorAll('[data-message-item]').length === 0 &&
      document.querySelector('textarea[placeholder="消息"]')?.value === expected;
  }, text);
  if (!ready) throw new Error("pre-send verification failed");
  await page.click('loc=css:button[aria-label="发送"]');
  await page.waitForFunction((expected) => [...document.querySelectorAll('[role="log"][aria-label="聊天消息"] [data-message-role="user"]')].some(x => x.innerText.includes(expected)), text, { timeout: 30000 });
}
async function egoLastReply(page) {
  return await page.evaluate(() => {
    const log = document.querySelector('[role="log"][aria-label="聊天消息"]');
    const items = [...(log?.querySelectorAll('[data-message-item]') || [])];
    const last = items.at(-1);
    const busy = !!document.querySelector('button[aria-label="停止"]') || !!last?.querySelector('[aria-busy="true"]');
    if (!last || last.getAttribute('data-message-role') !== 'assistant' || busy) return { text: "", stable: false };
    const clone = last.cloneNode(true);
    clone.querySelectorAll('button, [role="button"], script, style').forEach(x => x.remove());
    return { text: (clone.innerText || clone.textContent || "").trim(), stable: true };
  });
}
async function egoChatUrl(page) { return await page.url(); }

