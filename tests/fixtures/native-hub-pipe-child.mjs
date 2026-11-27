// CI-only child. Never included in the candidate payload.
import {createNativeTaskPipe} from '../../runtime/browser-driver/native-task-pipe.mjs';
import {createNativeHubTaskClient} from '../../runtime/browser-driver/native-hub-task-client.mjs';
const pipe=createNativeTaskPipe();
try{
  const hub=createNativeHubTaskClient({taskId:1,exchange:pipe.exchange});
  const claim=await hub('task_claim',{task_id:1});
  const args={task_id:1,claim_token:claim.claim_token};
  const first=await hub('collaboration_context_get',args);
  await hub('run_start',args);
  const second=await hub('collaboration_context_get',args);
  if(JSON.stringify(first)!==JSON.stringify(second) && first.items[0].text!==second.items[0].text)
    throw Error('fixture context changed');
  const receipt=await hub('task_complete',{...args,result:'fixture pipe answer'});
  if(receipt.status!=='review')throw Error('fixture receipt invalid');
}catch{process.exitCode=1;}
finally{await pipe.close();}
