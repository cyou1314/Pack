import fs from 'node:fs/promises';
import path from 'node:path';
import {createNativeCollaborationHost} from './native-collaboration-host.mjs';
import {nativeProviderOrigin} from './native-account-adapter.mjs';
import {createNativeHubTaskClient,nativeTaskApprovals} from './native-hub-task-client.mjs';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const outcomes=new Set(['review','blocked','unavailable','claimUnknown','stopUnknown','deliveryUnknown']);
const uncertain=()=>Object.freeze({status:'executionUnconfirmed',cleanupConfirmed:false,exitCode:1});
async function absent(file){
  try{await fs.lstat(file);return false;}
  catch(error){return error.code==='ENOENT';}
}

/** Single selected task, no polling or retry. Only a trusted source-level
 * integration can supply createPageDriver/createSessions. The pipe contains
 * scoped task data, never the long-lived Hub credential.
 * A successful Hub receipt and local cleanup are independent observations.
 */
export function createNativeTaskWorker({
  directory,accountRef,provider,taskId,pipe,createPageDriver,createSessions,timeoutMs=60000
}={}){
  if(typeof directory!=='string'||!path.isAbsolute(directory)||
     !UUID.test(accountRef||'')||!nativeProviderOrigin(provider)||
     !Number.isSafeInteger(taskId)||taskId<=0||
     typeof pipe?.exchange!=='function'||typeof pipe?.close!=='function'||
     typeof createPageDriver!=='function'||
     (createSessions!==undefined&&typeof createSessions!=='function')||
     !Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>120000)
    throw Error('invalid_native_worker');
  let used=false;
  const markers=[
    path.join(directory,'execution-leases',accountRef+'.lease'),
    path.join(directory,'native-sessions',accountRef,'.pack-session-lease')
  ];
  const markersAbsent=async()=> (await Promise.all(markers.map(absent))).every(Boolean);
  return Object.freeze({
    async run(){
      if(used)throw Error('native_worker_already_used');
      used=true;
      let result=uncertain();
      try{
        // Refuse a known existing lease before spending a claim budget.
        // Actual atomic acquisition still occurs inside the Host/Runner.
        const hubCall=createNativeHubTaskClient({taskId,exchange:frame=>pipe.exchange(frame)});
        const host=await createNativeCollaborationHost({
          directory,accountRef,provider,hubCall,createPageDriver,createSessions,timeoutMs,
          ...nativeTaskApprovals(hubCall,taskId)
        });
        if(!await markersAbsent()){
          result=Object.freeze({status:'unavailable',cleanupConfirmed:false,exitCode:1});
        }else{
          const outcome=await host.execute(taskId);
          const status=outcomes.has(outcome?.status)
            ?(outcome.code==='provider_outcome_unknown'?'providerUnknown':outcome.status)
            :'executionUnconfirmed';
          // Never delete a marker here. A concurrent new owner may cause a
          // conservative false negative, not a false cleanup confirmation.
          const cleanupConfirmed=await markersAbsent();
          const known=['review','blocked','unavailable'].includes(status);
          result=Object.freeze({status,cleanupConfirmed,exitCode:known&&cleanupConfirmed?0:1});
        }
      }catch{ /* raw exceptions and task contents never leave the worker */ }
      finally{
        try{await pipe.close();}
        catch{result=Object.freeze({...result,exitCode:1});}
      }
      return result;
    }
  });
}
