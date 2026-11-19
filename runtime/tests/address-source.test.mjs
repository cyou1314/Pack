import {test} from 'node:test';
import assert from 'node:assert/strict';
for (const modulePath of ['../pack-contracts.mjs','../muse4ai-bridge/pack-contracts.mjs']) {
  test(`${modulePath}: private success and public fallback`, async () => {
    const {discoverPublishedHub} = await import(modulePath);
    const original = globalThis.fetch;
    const events = [];
    let calls = [];
    try {
      globalThis.fetch = async (url, options) => {
        calls.push({url, options});
        if (String(url).includes('/pack-addr/')) return new Response('https://private.example/mcp\n', {status:200});
        throw Error('unexpected public fetch');
      };
      assert.equal(await discoverPublishedHub({addrToken:'fixture-only',onSource:(d,s)=>events.push([d,s])}), 'https://private.example/mcp');
      assert.equal(calls.length,1);
      assert.equal(calls[0].options.headers.Authorization,'Bearer fixture-only');
      assert.equal(calls[0].options.redirect,'error');
      assert.deepEqual(events,[['api.github.com',200]]);
      for (const mode of ['denied','invalid','missing']) {
        calls=[]; events.length=0;
        globalThis.fetch = async (url, options) => {
          calls.push({url,options});
          if (String(url).includes('/pack-addr/')) return new Response(mode === 'invalid' ? 'http://invalid.example/mcp' : '',{status:mode === 'invalid' ? 200 : 403});
          if (String(url).includes('/git/ref/')) return new Response(JSON.stringify({object:{sha:'a'.repeat(40)}}),{status:200});
          return new Response('https://public.example/mcp', {status:200});
        };
        const got=await discoverPublishedHub({addrToken:mode === 'missing' ? '' : 'fixture-only',onSource:(d,s)=>events.push([d,s])});
        assert.equal(got,'https://public.example/mcp');
        assert.equal(calls.filter(x=>String(x.url).includes('/pack-addr/')).length, mode === 'missing' ? 0 : 1);
        assert.ok(calls.filter(x=>!String(x.url).includes('/pack-addr/')).every(x=>!x.options.headers.Authorization));
        assert.equal(events.at(-1)[0],'raw.githubusercontent.com');
      }
    } finally { globalThis.fetch=original; }
  });
}
