// Private length-prefixed stdin/stdout transport. stdout is protocol-only.
// No logging, token loader, network endpoint, retries or unbounded readline.
export function createNativeTaskPipe({input=process.stdin,output=process.stdout}={}){
  const iterator=input[Symbol.asyncIterator]();
  let buffered=Buffer.alloc(0),busy=false,closed=false;
  async function take(count){
    while(buffered.length<count){
      const next=await iterator.next();
      if(next.done)throw Error('native_task_pipe_eof');
      const chunk=Buffer.from(next.value);
      if(buffered.length+chunk.length>128*1024)throw Error('native_task_pipe_overflow');
      buffered=Buffer.concat([buffered,chunk]);
    }
    const value=buffered.subarray(0,count);
    buffered=buffered.subarray(count);
    return value;
  }
  return Object.freeze({
    async exchange(frame){
      if(closed||busy||!Buffer.isBuffer(frame)||frame.length<1||frame.length>32*1024)
        throw Error('native_task_pipe_invalid_request');
      busy=true;
      try{
        const header=Buffer.alloc(4);header.writeUInt32BE(frame.length);
        await new Promise((resolve,reject)=>output.write(Buffer.concat([header,frame]),
          error=>error?reject(error):resolve()));
        const count=(await take(4)).readUInt32BE();
        if(count<1||count>64*1024)throw Error('native_task_pipe_invalid_response');
        return await take(count);
      }catch{
        closed=true;throw Error('native_task_pipe_unconfirmed');
      }finally{busy=false;}
    },
    async close(){
      closed=true;
      // Destroy only the child input, not the parent's descriptors.
      input.destroy?.();
      await iterator.return?.();
    }
  });
}
