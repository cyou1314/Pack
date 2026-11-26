import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

// Candidate lock verification only. A freshly generated lock is evidence
// for review, not an approved release trust root or browser binary signature.
export function verifyBrowserCandidateLock(lock,manifest){
  const fail=()=>{throw Error('invalid_browser_candidate_lock');};
  const version=manifest?.dependencies?.['playwright-core'];
  if(typeof version!=='string'||!/^\d+\.\d+\.\d+$/.test(version)||
     Object.keys(manifest.dependencies).length!==1||lock?.lockfileVersion!==3||
     lock.name!==manifest.name||lock.version!==manifest.version)fail();
  const packages=lock.packages;
  if(!packages||Object.keys(packages).sort().join(',')!==',node_modules/playwright-core')fail();
  const root=packages[''],pkg=packages['node_modules/playwright-core'];
  if(root?.name!==manifest.name||root.version!==manifest.version||
     Object.keys(root.dependencies||{}).length!==1||root.dependencies['playwright-core']!==version||
     pkg?.version!==version||pkg.resolved!==('https://registry.npmjs.org/playwright-core/-/playwright-core-'+version+'.tgz')||
     pkg.link||pkg.hasInstallScript||Object.keys(pkg.dependencies||{}).length||
     Object.keys(pkg.optionalDependencies||{}).length||Object.keys(pkg.peerDependencies||{}).length)
    fail();
  const match=/^sha512-([A-Za-z0-9+/]{86}==)$/.exec(pkg.integrity||'');
  if(!match)fail();
  const bytes=Buffer.from(match[1],'base64');
  if(bytes.length!==64||bytes.toString('base64')!==match[1])fail();
  return {version,integrity:pkg.integrity,resolved:pkg.resolved,releaseApproved:false};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{
    const root=path.dirname(fileURLToPath(import.meta.url));
    const manifest=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8'));
    const lock=JSON.parse(await fs.readFile(path.join(root,'package-lock.json'),'utf8'));
    console.log(JSON.stringify(verifyBrowserCandidateLock(lock,manifest),null,2));
  }catch{
    console.error('Browser candidate lock unavailable or invalid.');
    process.exitCode=1;
  }
}
