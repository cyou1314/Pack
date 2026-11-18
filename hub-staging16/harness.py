"""staging14 复检共用 harness：多身份 FastMCP 客户端。"""
import asyncio, json, sys
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8814/mcp"
DISP = open("/home/hatch/workspace/pack/hub-staging16/.dispatcher_token").read().strip()

def client(tok):
    return Client(StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {tok}"}))

async def call(tok, tool, **kw):
    """返回 (ok, payload)：ok=True 时 payload 为解析后的 dict/str；False 时为错误文本。"""
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
    """注册 alice / bob / owner 三个成员，返回 {name: token}。"""
    async with client(DISP) as c:
        for name in ("alice", "bob", "owner"):
            r = await c.call_tool("member_register", {"name": name})
            TOKENS[name] = json.loads(r.content[0].text)["token"]
    TOKENS["dispatcher"] = DISP
    return TOKENS

if __name__ == "__main__":
    async def main():
        toks = await setup_members()
        ok, out = await call(toks["alice"], "member_list")
        print("member_list ok:", ok, [m["name"] for m in out["members"]] if ok else out)
        ok, out = await call(toks["dispatcher"], "room_list")
        print("room_list:", ok, out)
    asyncio.run(main())
