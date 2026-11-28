/** Static local-profile integration inventory. Not an authorization gate.
 * Keep stage names in parity with Swift AgentConnectionReport.
 * Provider IDs here are local profile IDs, not Foundation Registry IDs.
 */
export function describeLocalIntegration({providerID,mode,enabledCapabilities,credentialState}={}){
  const stages=new Map([
    ['muse:nativeAccount','legacyCompatibility'],
    ['chatgpt:nativeAccount','nativeDriverCandidate'],
    ['gemini:nativeAccount','nativeLoginDevelopment'],
    ['doubao:nativeAccount','nativeLoginDevelopment'],
    ['openai:officialAPI','manualTextAPI'],
    ['gemini-api:officialAPI','manualTextAPI'],
    ['volcengine:officialAPI','manualTextAPI']
  ]);
  const allowed=['chat','toolCalling','fileAccess','browserAutomation','computerUse',
    'imageGeneration','videoGeneration','audio','codeExecution'];
  const states=['nativeSessionUnverified','apiKeyMissing','apiKeyPresentUnverified','apiKeyUnavailable'];
  if(typeof providerID!=='string'||typeof mode!=='string'||
     !Array.isArray(enabledCapabilities)||!enabledCapabilities.every(x=>allowed.includes(x))||
     !states.includes(credentialState))throw Error('invalid_local_integration_inventory');
  const stage=stages.get(providerID+':'+mode)??'unsupported';
  return Object.freeze({
    stage,credentialState,
    configuredCapabilities:Object.freeze([...new Set(enabledCapabilities)].sort()),
    implementedCapabilities:Object.freeze(stage==='manualTextAPI'?['message.send']:[]),
    verifiedCapabilities:Object.freeze([]),
    identityVerified:false,executionVerified:false,automaticExecutionEnabled:false
  });
}
