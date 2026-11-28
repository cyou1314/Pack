/**
 * Admission control for existing one-shot collaboration execution lanes.
 * This does not submit Provider messages, own account leases or replace Hub
 * task fencing. Share one instance per Hub identity in a trusted process.
 */
export class CollaborationExecutionSupervisor {
  #active = new Set();
  #completed = new Map();
  #limit;
  #capacity;
  #stopping = false;
  constructor({maxConcurrent=4,maxTracked=4096}={}) {
    if(!Number.isSafeInteger(maxConcurrent)||maxConcurrent<1||maxConcurrent>32||
       !Number.isSafeInteger(maxTracked)||maxTracked<maxConcurrent||maxTracked>100000)
      throw new TypeError("invalid_execution_limits");
    this.#limit=maxConcurrent;
    this.#capacity=maxTracked;
  }
  async execute(taskId, operation) {
    if(!Number.isSafeInteger(taskId)||taskId<1||typeof operation!=="function")
      throw new TypeError("invalid_execution_request");
    if(this.#active.has(taskId)) return {status:"unavailable",code:"task_in_flight",usage:null};
    if(this.#completed.has(taskId)) return {status:"unavailable",code:"task_already_attempted",usage:null};
    if(this.#stopping) return {status:"unavailable",code:"supervisor_stopping",usage:null};
    if(this.#active.size>=this.#limit) return {status:"unavailable",code:"execution_capacity",usage:null};
    // No silent eviction of uncertain tasks. Stop admitting when the
    // bounded tracking table fills; operator must reconcile before restart.
    if(this.#active.size+this.#completed.size>=this.#capacity)
      return {status:"unavailable",code:"execution_tracking_full",usage:null};
    this.#active.add(taskId);
    let status="unknown";
    try {
      const result=await operation();
      if(result?.status==="unavailable" &&
         ["execution_capacity","execution_account_busy"].includes(result.code)){
        // Trusted runner only returns these from an explicit pre-claim
        // Hub receipt. No attempt was spent; allow a later user request.
        status=null;
        return result;
      }
      if(["review","blocked","claimUnknown","stopUnknown","deliveryUnknown"].includes(result?.status)){
        status=result.code==="provider_outcome_unknown"?"providerUnknown":result.status;
        return result;
      }
      return {status:"stopUnknown",code:"invalid_execution_result",usage:null};
    } catch {
      return {status:"stopUnknown",code:"execution_outcome_unknown",usage:null};
    } finally {
      this.#active.delete(taskId);
      if(status!==null) this.#completed.set(taskId,status);
    }
  }
  stopAdmission(){this.#stopping=true;}
  snapshot(){
    const counts={};
    for(const status of this.#completed.values()) counts[status]=(counts[status]??0)+1;
    return Object.freeze({accepting:!this.#stopping&&this.#active.size<this.#limit&&
      this.#active.size+this.#completed.size<this.#capacity,
      active:this.#active.size,tracked:this.#completed.size,
      maxConcurrent:this.#limit,outcomes:Object.freeze(counts)});
  }
}
