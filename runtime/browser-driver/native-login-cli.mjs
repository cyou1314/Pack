#!/usr/bin/env node
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { resolveNativeLogin,runNativeLogin } from './native-login.mjs';
import { createBrowserSessions } from './playwright.mjs';
import { diagnoseNativeSurface } from './native-diagnostics.mjs';

const controller=new AbortController();
const interrupt=()=>controller.abort();
process.once('SIGINT',interrupt);
process.once('SIGTERM',interrupt);
let managed=false;
try {
  const diagnostic=process.argv.length===4&&process.argv[3]==='--diagnose';
  managed=process.argv.length===4&&process.argv[3]==='--managed-login';
  if(managed) {
    // Parent owns the only write end. EOF also stops the browser if the
    // App crashes/exits. No command language, credential or prompt channel.
    if(process.stdin.isTTY||process.stdout.isTTY)throw Error('managed_pipe_required');
    process.stdin.once('end',interrupt);
    process.stdin.once('error',interrupt);
    process.stdin.on('data',interrupt);
    process.stdin.resume();
  } else if((process.argv.length!==3&&!diagnostic)||!process.stdin.isTTY||!process.stdout.isTTY)
    throw Error('interactive_login_required');
  const directory=path.join(os.homedir(),'.pack','agents-v2');
  const binding=await resolveNativeLogin({directory,accountRef:process.argv[2]});
  console.log('Opening an isolated Pack browser. Sign in manually; do not enter passwords in this terminal.');
  const result=await runNativeLogin({binding,createSessions:createBrowserSessions,signal:controller.signal,
    waitForExit:({context,signal})=>new Promise(resolve=>{
      if(managed) {
        const finish=()=>{
          context.off('close',finish);
          signal?.removeEventListener('abort',finish);
          resolve();
        };
        context.once('close',finish);
        signal?.addEventListener('abort',finish,{once:true});
        if(signal?.aborted)finish();
        return;
      }
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
      rl.once('line',async()=>{
        if(diagnostic&&!signal?.aborted){
          try{
            const report=await diagnoseNativeSurface({context,provider:binding.provider,signal});
            if(!signal?.aborted)console.log(JSON.stringify(report,null,2));
          }catch{console.error('Read-only diagnostic unavailable.');}
        }
        finish();
      });
      rl.once('close',finish);
      console.log(diagnostic?'After manually opening the intended provider page, press Enter to print fixed probe counts and close. No identity or execution verification.':
        'Press Enter to close and preserve the session. Login, identity and memory remain unverified.');
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
  if(managed){
    process.stdin.pause();
    process.stdin.removeListener('end',interrupt);
    process.stdin.removeListener('error',interrupt);
    process.stdin.removeListener('data',interrupt);
  }
  process.removeListener('SIGINT',interrupt);
  process.removeListener('SIGTERM',interrupt);
}
