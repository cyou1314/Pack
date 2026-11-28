import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {describeLocalIntegration} from '../agent-core/index.mjs';

const rows=JSON.parse(readFileSync(new URL('../../tests/fixtures/agent-integration-stages.json',import.meta.url),'utf8'));
test('local integration inventory matches shared Swift/Node fixture without granting execution',()=>{
  for(const row of rows){
    const report=describeLocalIntegration({...row,enabledCapabilities:['chat','toolCalling','chat'],
      credentialState:row.mode==='officialAPI'?'apiKeyPresentUnverified':'nativeSessionUnverified'});
    assert.equal(report.stage,row.stage);
    assert.deepEqual(report.implementedCapabilities,row.implementedCapabilities);
    assert.deepEqual(report.configuredCapabilities,['chat','toolCalling']);
    assert.deepEqual(report.verifiedCapabilities,[]);
    assert.equal(report.identityVerified,false);
    assert.equal(report.executionVerified,false);
    assert.equal(report.automaticExecutionEnabled,false);
  }
});
test('inventory rejects unknown credential state and capability vocabulary',()=>{
  const input={providerID:'openai',mode:'officialAPI',enabledCapabilities:['chat'],credentialState:'apiKeyPresentUnverified'};
  assert.throws(()=>describeLocalIntegration({...input,credentialState:'verified'}));
  assert.throws(()=>describeLocalIntegration({...input,enabledCapabilities:['unreviewed']}));
});
