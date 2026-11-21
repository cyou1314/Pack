import json, os
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
PORT = 18795
URL = f"http://127.0.0.1:{PORT}/mcp"
HERE = os.path.dirname(os.path.abspath(__file__))
DISP = open(os.path.join(HERE, ".dispatcher_token")).read().strip()
def client(tok):
    return Client(StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {tok}"}))
async def call(tok, tool, **kw):
    async with client(tok) as c:
        r = await c.call_tool(tool, kw)
        return json.loads(r.content[0].text)
async def call_err(tok, tool, **kw):
    try:
        async with client(tok) as c:
            r = await c.call_tool(tool, kw)
            return None
    except Exception as e:
        return str(e)
