#!/usr/bin/env node
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { resolveNativeLogin,runNativeLogin } from './native-login.mjs';
import { createBrowserSessions } from './playwright.mjs';

const controller=new AbortController();
const interrupt=()=>controller.abort();
process.once('SIGINT',interrupt);
process.once('SIGTERM',interrupt);
try {
  if(process.argv.length!==3||!process.stdin.isTTY||!process.stdout.isTTY)
    throw Error('interactive_login_required');
  const directory=path.join(os.homedir(),'.pack','agents-v2');
  const binding=await resolveNativeLogin({directory,accountRef:process.argv[2]});
  console.log('Opening an isolated Pack browser. Sign in manually; do not enter passwords in this terminal.');
  const result=await runNativeLogin({binding,createSessions:createBrowserSessions,signal:controller.signal,
    waitForExit:({context,signal})=>new Promise(resolve=>{
      const rl=readline.createInterface({input:process.stdin,output:process.stdout});
      const finish=()=>{
        context.off('close',finish);
        signal?.removeEventListener('abort',finish);
        rl.off('close',finish);
        rl.off('line',finish);
        rl.close();
        resolve();
      };
      context.once('close',finish);
      signal?.addEventListener('abort',finish,{once:true});
      rl.once('line',finish);
      rl.once('close',finish);
      console.log('Press Enter to close and preserve the session. Login, identity and memory remain unverified.');
      if(signal?.aborted) finish();
    })
  });
  console.log(result.status);
} catch {
  // Error objects may include navigation URLs or other browser details.
  // Never print them. No automatic lease deletion/retry on uncertain close.
  console.error('Native login could not be completed. Check profile, pinned browser dependencies and lease state. Do not remove a lease until the browser is confirmed stopped.');
  process.exitCode=1;
} finally {
  process.removeListener('SIGINT',interrupt);
  process.removeListener('SIGTERM',interrupt);
}
