// Independent persistent room conversation; task driver remains unchanged.
async function packChatMain() {
  const {profileId, spaceName, action, chatUrl, message} = globalThis.PACK_TASK;
  const done = x => console.log('DRIVER-RESULT ' + JSON.stringify(x));
  let page;
  try {
    const space = await egoOpenSpace(profileId, spaceName);
    page = space.page('p1');
    await page.goto('https://muse.ai/');
    await page.waitForLoadState();
    await page.waitForSelector('loc=css:button[aria-label="新旁聊"]', {state:'visible', timeout:30000});
    if (action === 'seed') {
      const before = await egoCurrentThreadId(page);
      await egoNewChat(page);
      const after = await egoCurrentThreadId(page);
      if (!after || after === before) throw Error('new_chat_not_verified');
    } else {
      const url = new URL(chatUrl);
      if (url.origin !== 'https://muse.ai' || !/^\/thread\/[^/]+\/?$/.test(url.pathname) || url.pathname === '/thread/new') throw Error('invalid_chat_url');
      await page.goto(url.href);
      await page.waitForLoadState();
      const actual = await egoCurrentThreadId(page);
      if (new URL(actual).pathname !== url.pathname) throw Error('chat_session_not_verified');
    }
    const beforeCount = await page.evaluate(() => ({items:document.querySelectorAll('[role="log"][aria-label="聊天消息"] [data-message-item]').length, users:document.querySelectorAll('[role="log"][aria-label="聊天消息"] [data-message-role="user"]').length}));
    const editor = 'loc=css:textarea[placeholder="消息"]';
    await page.fill(editor, message);
    const ready = await page.evaluate(expected => !!document.querySelector('[role="log"][aria-label="聊天消息"]') && document.querySelector('textarea[placeholder="消息"]')?.value === expected, message);
    if (!ready) throw Error('pre_send_verification_failed');
    await page.click('loc=css:button[aria-label="发送"]');
    await page.waitForFunction(({count, text}) => {
      const nodes = [...document.querySelectorAll('[role="log"][aria-label="聊天消息"] [data-message-role="user"]')];
      return nodes.length > count.users && nodes.slice(count.users).some(n => n.innerText.includes(text));
    }, {count:beforeCount, text:message}, {timeout:30000});
    let previous = '', stable = 0;
    for (let i=0; i<60; i++) {
      await new Promise(r => setTimeout(r, 3000));
      const count = await page.evaluate(() => document.querySelectorAll('[role="log"][aria-label="聊天消息"] [data-message-item]').length);
      const cur = await egoLastReply(page);
      if (count >= beforeCount.items + 2 && cur.stable && cur.text && cur.text === previous) stable++; else stable = 0;
      previous = cur.text || '';
      if (stable >= 2) {
        const url = await egoChatUrl(page);
        if (new URL(url).origin !== 'https://muse.ai' || new URL(url).pathname === '/thread/new') throw Error('chat_url_not_persisted');
        return done({ok:true, reply:cur.text, chatUrl:url});
      }
    }
    throw Error('reply_not_confirmed_stable');
  } catch(e) {
    let url = '';
    try { if (page) url = await page.url(); } catch {}
    done({ok:false, error:/\/(?:login|signin|sign-in|auth)(?:[/?#]|$)/i.test(url) ? 'login_required' : String(e?.message || e)});
  }
}
packChatMain();
