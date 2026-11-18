"""staging27 harness：多身份 FastMCP 客户端（沿 staging 共用形状）。"""
import asyncio, json
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8829/mcp"
DISP = open("/home/hatch/workspace/pack/hub-staging29/.dispatcher_token").read().strip()

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
