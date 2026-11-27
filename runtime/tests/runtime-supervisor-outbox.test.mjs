import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {AgentAdapterRegistry, RuntimeSupervisor, SqliteEventStore, createAgentEvent} from "../agent-core/index.mjs";

const descriptor = {
  adapterId:"fixture.native", providerId:"fixture", displayName:"Fixture",
  version:"1.0.0", mode:"native_account", authKind:"none",
  capabilities:["events.push","session.create","message.send"],
};

class FixtureAdapter {
  constructor(agentId) {
    this.agentId=agentId;
    this.listeners=new Set();
    this.sequence=0;
    this.connected=false;
    this.calls=0;
  }
  subscribe(handler) {
    this.listeners.add(handler);
    return ()=>this.listeners.delete(handler);
  }
  emit(eventType,payload,fields={}) {
    const sequence=++this.sequence;
    const event=createAgentEvent({
      eventId:this.agentId+"-"+sequence, agentId:this.agentId,
      providerId:"fixture", eventType, sequence,
      occurredAt:"2026-11-27T00:00:00.000Z", payload,...fields,
    });
    for(const handler of this.listeners) handler(event);
  }
  async connect() {
    if(this.agentId==="bad") throw Error("fixture_connect_failed");
    this.connected=true;
    this.emit("connection.changed",{status:"ready"});
  }
  getHealth() {return {status:this.connected?"ready":"disconnected"};}
  async disconnect() {
    this.connected=false;
    this.emit("connection.changed",{status:"disconnected"});
  }
  async createSession() {
    this.emit("session.created",{providerSessionId:"thread",resumed:false},
              {sessionId:"thread",hubSessionId:"hub-fixture"});
    return {sessionId:"thread"};
  }
  async sendMessage({sessionId}) {
    this.calls++;
    this.emit("run.started",{},{sessionId,hubSessionId:"hub-fixture",runId:"run"});
    return {accepted:true,runId:"run"};
  }
}

test("real Outbox persists supervisor events; failed Agent does not block another",async()=>{
  const root=mkdtempSync(join(tmpdir(),"pack-supervisor-outbox-"));
  const databasePath=join(root,"events.db");
  let store=new SqliteEventStore({databasePath});
  const registry=new AgentAdapterRegistry();
  const adapters=new Map();
  registry.register({descriptor,create:async({agentId})=>{
    const adapter=new FixtureAdapter(agentId);
    adapters.set(agentId,adapter);
    return adapter;
  }});
  const supervisor=new RuntimeSupervisor({registry,eventStore:store});
  const published=[];
  supervisor.subscribe(event=>published.push(event.eventId));
  try {
    await assert.rejects(()=>supervisor.connectAgent({agentId:"bad",adapterId:descriptor.adapterId}),
                         /fixture_connect_failed/);
    assert.equal(supervisor.getAgentStatus("bad").status,"failed");
    await supervisor.connectAgent({agentId:"good",adapterId:descriptor.adapterId});
    await supervisor.createSession("good");
    const result=await supervisor.sendMessage({agentId:"good",sessionId:"thread",text:"fixture"});
    assert.equal(result.runId,"run");
    assert.equal(adapters.get("good").calls,1);
    assert.equal(adapters.get("bad").calls,0);
    await supervisor.shutdown();
    const pending=await store.listPending();
    assert.deepEqual(pending.map(e=>e.eventId),published);
    assert.equal(pending.filter(e=>e.agentId==="good"&&e.eventType==="run.started").length,1);
    assert.equal(pending.find(e=>e.eventType==="session.created").hubSessionId,"hub-fixture");
    store.close();
    store=new SqliteEventStore({databasePath});
    assert.deepEqual(await store.listPending(),pending);
    assert.equal((await store.getStats()).pending,pending.length);
  } finally {
    try {await supervisor.shutdown();} catch {}
    store.close();
    rmSync(root,{recursive:true,force:true});
  }
});
