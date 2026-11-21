// Pack Runtime's MCP Streamable HTTP transport. Keep orchestration/retry policy in runtime.mjs.
export function createHubRpcClient({ getConfig, fetchImpl = fetch, isNetworkFailure }) {
  let sessionId = null;
  let rpcId = 0;

  async function rpc(method, params) {
    const cfg = getConfig();
    const headers = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      Authorization: `Bearer ${cfg.memberToken}`,
    };
    if (sessionId) headers["Mcp-Session-Id"] = sessionId;
    let res;
    try {
      res = await fetchImpl(cfg.hubUrl, {
        signal: AbortSignal.timeout(15000),
        method: "POST",
        headers,
        body: JSON.stringify({ jsonrpc: "2.0", id: ++rpcId, method, params }),
      });
    } catch (error) {
      if (isNetworkFailure?.(error)) error.hubNetworkFailure = true;
      throw error;
    }
    if (!res.ok) throw new Error(`hub HTTP ${res.status}`);
    const sid = res.headers.get("mcp-session-id");
    if (sid) sessionId = sid;
    let body;
    try {
      body = await res.text();
    } catch (error) {
      if (isNetworkFailure?.(error)) error.hubNetworkFailure = true;
      throw error;
    }
    let payload = null;
    if (body.trim().startsWith("{")) payload = JSON.parse(body);
    else {
      for (const line of body.split("\n")) {
        if (line.startsWith("data:")) payload = JSON.parse(line.slice(5));
      }
    }
    if (!payload) {
      const error = new Error("hub: empty response");
      error.hubNetworkFailure = true;
      throw error;
    }
    if (payload.error) throw new Error(`hub rpc error: ${JSON.stringify(payload.error)}`);
    return payload.result;
  }

  return { rpc, resetSession() { sessionId = null; }, getSessionId() { return sessionId; } };
}
