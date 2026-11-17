"""staging39 harness: multi-identity FastMCP client (shared staging shape)."""
import asyncio, json
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8842/mcp"
DISP = open("/home/hatch/workspace/pack/hub-staging42/.dispatcher_token").read().strip()

def client(tok):
    return Client(StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {tok}"}))

async def call(tok, tool, **kw):
    async with client(tok) as c:
        try:
            r = await c.call_tool(tool, kw)
            txt = r.content[0].text if r.content else ""
            try:
                return True, json.loads(txt)
            except Exception:
                return True, txt
        except Exception as e:
            return False, str(e)

TOKENS = {}

async def setup_members():
    async with client(DISP) as c:
        for name in ("alice", "bob", "owner"):
            r = await c.call_tool("member_register", {"name": name})
            TOKENS[name] = json.loads(r.content[0].text)["token"]
    TOKENS["dispatcher"] = DISP
    return TOKENS
