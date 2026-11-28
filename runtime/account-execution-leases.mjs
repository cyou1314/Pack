import fs from 'node:fs/promises';
import path from 'node:path';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

async function privateDir(directory) {
  const st=await fs.lstat(directory);
  if (!st.isDirectory() || st.isSymbolicLink() || (st.mode & 0o077)!==0 ||
      (typeof process.getuid==='function' && st.uid!==process.getuid()))
    throw Error('unsafe_account_lease_directory');
  return st;
}

/**
 * Local cooperating-process exclusion, retained on crash/ambiguous outcome.
 * root must be a pre-created, private Pack directory; all runners for the
 * same device must use the same root. Never break an existing lease by age.
 * No prompt, credential, claim token or account email is stored.
 */
export class AccountExecutionLeases {
  #root;
  constructor({root}) {
    if(typeof root!=='string' || !path.isAbsolute(root)) throw Error('invalid_account_lease_root');
    this.#root=path.resolve(root);
  }

  async acquire(accountRef) {
    if(typeof accountRef!=='string' || !UUID.test(accountRef)) throw Error('invalid_account_reference');
    await privateDir(this.#root);
    const root=await fs.realpath(this.#root);
    const rootStat=await privateDir(root);
    const lease=path.join(root,accountRef+'.lease');
    try { await fs.mkdir(lease,{mode:0o700}); }
    catch(error) {
      if(error.code==='EEXIST') throw Error('account_lease_exists');
      throw error;
    }
    // After mkdir, any uncertainty leaves the marker in place. The directory
    // itself is the exclusive marker, even if the process dies immediately.
    const leaseStat=await privateDir(lease);
    let released=false;
    return Object.freeze({
      release:async()=>{
        if(released) throw Error('account_lease_already_released');
        const currentRoot=await privateDir(root);
        const currentLease=await privateDir(lease);
        if(currentRoot.dev!==rootStat.dev || currentRoot.ino!==rootStat.ino ||
           currentLease.dev!==leaseStat.dev || currentLease.ino!==leaseStat.ino)
          throw Error('account_lease_identity_changed');
        // Never recursively delete or remove a lease containing unknown files.
        await fs.rmdir(lease);
        released=true;
      }
    });
  }
}
