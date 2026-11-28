import test from "node:test";
import assert from "node:assert/strict";
import * as core from "../agent-core/index.mjs";

test("agent-core barrel exports only implemented interfaces",()=>{
  for(const name of ["AgentAdapterRegistry","RuntimeSupervisor","SqliteEventStore",
                     "AgentEventDeliveryWorker","createAgentEvent","createContextPackage",
                     "defineAdapterDescriptor","assertAdapterInstance"])
    assert.equal(typeof core[name],"function",name);
  for(const name of ["ADAPTER_MODES","AUTHENTICATION_KINDS","AGENT_CAPABILITIES","AGENT_EVENT_TYPES"])
    assert.ok(Array.isArray(core[name]),name);
  assert.equal("getAgentEventCapabilities" in core,false);
});
