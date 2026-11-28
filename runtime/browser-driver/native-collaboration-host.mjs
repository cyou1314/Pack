import {resolveNativeLogin} from './native-login.mjs';
import {createBrowserSessions} from './playwright.mjs';
import {createNativeAccountAdapter,nativeProviderOrigin} from './native-account-adapter.mjs';
import {expectedNativeAccountProof} from './native-identity.mjs';
import {AccountExecutionLeases} from '../account-execution-leases.mjs';
import {createCollaborationRunner,destinationFor} from '../collaboration-runner.mjs';

const fail=code=>{throw Error(code)};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);

/** Trusted developer host, not a CLI, MCP tool, startup path or live selector
 * contract. hubCall must be authenticated for this one dedicated account
 * and pinned Hub instance, with no automatic retry or token switching.
 * createPageDriver must come from reviewed Provider-specific host code, not
 * model output or remote task data. It must verify identity, model and memory
 * through native-identity on each inspect; never echo a stored proof. Both approval
 * callbacks require explicit local owner decisions. No default approvals.
 */
export async function createNativeCollaborationHost({
  directory,accountRef,provider,hubCall,createPageDriver,authorizeEgress,authorizeNativeSend,
  createSessions=createBrowserSessions,executionSupervisor,timeoutMs=60000
}={}){
  if(!nativeProviderOrigin(provider)||
     [hubCall,createPageDriver,authorizeEgress,authorizeNativeSend,createSessions].some(x=>typeof x!=='function'))
    fail('invalid_native_host');
  const local=await resolveNativeLogin({directory,accountRef});
  if(local.provider!==provider)fail('native_host_profile_provider_mismatch');
  const context={account_ref:accountRef,provider,access_kind:'native'};
  const pinned=destinationFor(context,local.executionProfile);
  // Enrollment must pre-exist. Loading a stored proof does not establish
  // live login; the driver verifies again on every inspection under leases.
  const proof=await expectedNativeAccountProof({root:directory,binding:pinned});
  const sessions=await createSessions({root:local.browserRoot,headless:false});
  const leases=new AccountExecutionLeases({root:local.leaseRoot});
  const runner=createCollaborationRunner({
    hubCall,accountLeases:leases,authorizeEgress,timeoutMs,executionSupervisor,
    resolveProfile:async ref=>{
      if(ref!==accountRef)fail('native_host_account_mismatch');
      const fresh=await resolveNativeLogin({directory,accountRef});
      if(!same(destinationFor(context,fresh.executionProfile),pinned))
        fail('native_host_profile_changed');
      return fresh.executionProfile;
    },
    resolveAdapter:async destination=>{
      if(!same(destination,pinned))fail('native_host_account_mismatch');
      // Called only after the runner owns the shared account lease.
      const currentProof=await expectedNativeAccountProof({root:directory,binding:pinned});
      if(currentProof!==proof)fail('native_host_identity_changed');
      return createNativeAccountAdapter({
        profile:local.executionProfile,destination:pinned,sessions,
        expectedAccountProof:proof,authorizeNativeSend,
        createPageDriver:async({context:browser})=>{
          const pages=browser.pages();
          if(pages.length>1)fail('native_single_page_required');
          const page=pages[0]||await browser.newPage();
          if(page.isClosed())fail('native_page_unavailable');
          // Only a pristine browser page may be navigated here. An existing
          // sign-in, challenge, unrelated tab or conversation is never
          // overwritten to make a verification pass.
          if(page.url()==='about:blank')
            await page.goto(local.origin+'/',{waitUntil:'domcontentloaded',timeout:15000});
          return createPageDriver(Object.freeze({
            context:browser,page,root:directory,binding:pinned,origin:local.origin
          }));
        }
      });
    }
  });
  // Only the existing one-shot runner sends a message. The separate session
  // RuntimeSupervisor is not called, since submission != task completion.
  return Object.freeze({execute:taskId=>runner.execute(taskId),
    executionStatus:()=>runner.executionStatus()});
}
