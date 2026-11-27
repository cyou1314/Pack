import {mkdirSync,renameSync,createWriteStream} from 'node:fs';
import {createHash} from 'node:crypto';
import {pipeline} from 'node:stream/promises';
import {Readable} from 'node:stream';
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

const MAX_IMAGE = 100 * 1024 * 1024;
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
    return r.json();
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
    const out = job.outputs?.find(x => x.kind === 'image' && x.available && x.asset_id);
    if (!out) throw Error('image_output_missing');
    if (!['image/png','image/jpeg','image/webp'].includes(out.mime) || out.bytes > MAX_IMAGE) throw Error('image_output_invalid');
    const ext = {'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[out.mime];
    const dir = path.join(directory, 'images'); mkdirSync(dir, {recursive:true, mode:0o700});
    const dest = path.join(dir, `task-${id}-${String(out.asset_id).replace(/[^A-Za-z0-9_-]/g,'_')}.${ext}`), tmp = dest + '.part';
    const res = await fetchImpl(baseUrl + '/v1/assets/' + segment(out.asset_id) + '/content', { headers:{authorization:'Bearer ' + key}, signal:AbortSignal.timeout(120000), redirect:'error' });
    if (!res.ok || !res.body) throw Error('image_download_failed');
    let size = 0; const hash = createHash('sha256');
    const reader = async function* () { for await (const chunk of Readable.fromWeb(res.body)) { size += chunk.length; if (size > MAX_IMAGE) throw Error('image_too_large'); hash.update(chunk); yield chunk; } };
    await pipeline(reader(), createWriteStream(tmp, {mode:0o600}));
    if ((out.bytes != null && size !== out.bytes) || (out.sha256 && hash.digest('hex') !== out.sha256)) throw Error('image_integrity_failed');
    renameSync(tmp, dest);
    return `图片已生成并保存至本机：${dest}`;
    }
  });
}
