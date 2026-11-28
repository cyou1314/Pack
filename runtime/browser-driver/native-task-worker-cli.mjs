// Fixed developer entry, intentionally no live drivers registered yet.
// Never load a module path, selectors or identity observer from task data,
// environment variables or CLI input. Fixture drivers are not shipped.
import {createNativeTaskWorker} from './native-task-worker.mjs';
import {createNativeTaskPipe} from './native-task-pipe.mjs';
import {homedir} from 'node:os';
import path from 'node:path';

const reviewedDrivers=new Map();
const args=process.argv.slice(2);
const [provider,accountRef,rawTask]=args;
if(args.length!==3||!['chatgpt','gemini','doubao'].includes(provider)||
   !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(accountRef||'')||
   !/^[1-9][0-9]*$/.test(rawTask||'')||!Number.isSafeInteger(Number(rawTask))){
  process.exitCode=2;
}else if(!reviewedDrivers.has(provider)){
  // No claim, browser launch, account data read, or stdout diagnostic.
  process.exitCode=2;
}else{
  try{
    const worker=createNativeTaskWorker({
      directory:path.join(homedir(),'.pack','agents-v2'),provider,accountRef,taskId:Number(rawTask),
      pipe:createNativeTaskPipe(),createPageDriver:reviewedDrivers.get(provider)
    });
    process.exitCode=(await worker.run()).exitCode;
  }catch{process.exitCode=1;}
}
