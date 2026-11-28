import {createNativeCollaborationHost} from './native-collaboration-host.mjs';
import {createChatGPTPageDriver} from './chatgpt-page-driver.mjs';

/** Existing ChatGPT developer entrypoint; no default live selector contract. */
export async function createChatGPTCollaborationHost({
  surface,observeIdentity,...options
}={}){
  if(!surface||typeof surface!=='object'||
     [options.hubCall,observeIdentity,options.authorizeEgress,options.authorizeNativeSend]
       .some(x=>typeof x!=='function'))throw Error('invalid_chatgpt_host');
  const contract=Object.freeze({...surface});
  try{
    return await createNativeCollaborationHost({...options,provider:'chatgpt',
      createPageDriver:args=>createChatGPTPageDriver({...args,surface:contract,observeIdentity})
    });
  }catch(error){
    if(error.message==='native_host_profile_provider_mismatch')throw Error('chatgpt_profile_required');
    throw error;
  }
}
