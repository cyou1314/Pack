import {saveMuseImage} from './muse-image-store.mjs';
import path from 'node:path';

export function resolveMuseApiBaseUrl(env = process.env) {
  const value = String(env.PACK_MUSE_API_URL || 'http://127.0.0.1:47821').trim();
  let url;
  try { url = new URL(value); } catch { throw Error('invalid_local_muse_api_url'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw Error('invalid_local_muse_api_url');
  }
  return url.origin;
}

// A status report may contain account metadata. Bound its size before
// parsing; never return raw status bodies from the diagnostic CLI.
async function boundedStatus(response){
  const limit=1024*1024;
  if(!response.body)throw Error('muse_status_invalid');
  let length=0;const chunks=[];
  for await(const chunk of response.body){
    length+=chunk.length;
    if(length>limit)throw Error('muse_status_too_large');
    chunks.push(Buffer.from(chunk));
  }
  return JSON.parse(Buffer.concat(chunks,length).toString('utf8'));
}

function segment(value) {
  if(typeof value!=='string'||!value||value.length>256||value==='.'||value==='..'||
     /[\x00-\x1f\x7f]/.test(value)) throw Error('invalid_muse_identifier');
  return encodeURIComponent(value);
}

/** Existing local Muse4AI service contract, not an official cloud API.
 * Only fixed operations are exposed. Never follow redirects or automatically
 * retry a request. The bridge owns its existing idempotency/recovery policy.
 */
export function createMuseEngineClient({baseUrl,key,directory,fetchImpl=globalThis.fetch}={}) {
  baseUrl=resolveMuseApiBaseUrl({PACK_MUSE_API_URL:baseUrl});
  if(typeof key!=='string'||!/^msk_.{20,}$/.test(key)||
     typeof directory!=='string'||!path.isAbsolute(directory)||typeof fetchImpl!=='function')
    throw Error('invalid_configuration');
  async function request(endpoint,method='GET',body,idem) {
    const r=await fetchImpl(baseUrl+endpoint,{method,signal:AbortSignal.timeout(30000),redirect:'error',headers:{
      authorization:'Bearer '+key,...(body?{'content-type':'application/json'}:{}),
      ...(idem?{'idempotency-key':idem}:{})
    },...(body?{body:JSON.stringify(body)}:{})});
    if(!r.ok)throw Error('muse_http_'+r.status);
    return endpoint==='/v1/status'?boundedStatus(r):r.json();
  }
  return Object.freeze({
    providerId:'muse',
    status:()=>request('/v1/status'),
    submitJob(body,idem) {
      if(typeof idem!=='string'||!/^pack-task-[1-9][0-9]*-attempt-[0-9]+-fence-[0-9]+$/.test(idem))
        throw Error('invalid_muse_idempotency_key');
      return request('/v1/jobs','POST',body,idem);
    },
    getJob:id=>request('/v1/jobs/'+segment(id)),
    cancelJob:id=>request('/v1/jobs/'+segment(id)+'/cancel','POST',{}),
    async downloadImage(job,id) {
      const out=Array.isArray(job?.outputs)
        ?job.outputs.find(x=>x?.kind==='image'&&x.available&&x.asset_id):null;
      if(!out)throw Error('image_output_missing');
      const dest=await saveMuseImage({directory,out,taskId:id,download:()=>fetchImpl(
        baseUrl+'/v1/assets/'+segment(out.asset_id)+'/content',{
          headers:{authorization:'Bearer '+key},signal:AbortSignal.timeout(120000),redirect:'error'
        })});
      return '图片已生成并保存至本机：'+dest;
    }
  });
}
