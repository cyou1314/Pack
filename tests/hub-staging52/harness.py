import json, os, urllib.request, urllib.error
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

PORT = 18798
URL = f"http://127.0.0.1:{PORT}/mcp"
PAIR_URL = f"http://127.0.0.1:{PORT}/pair"
HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "pack.db")
DISP = open(os.path.join(HERE, ".dispatcher_token")).read().strip()


def client(tok):
    return Client(StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {tok}"}))


async def call(tok, tool, **kw):
    async with client(tok) as c:
        r = await c.call_tool(tool, kw)
        return json.loads(r.content[0].text)


async def try_call(tok, tool, **kw):
    """返回 (True, result) 或 (False, error_str)，供拒绝类断言用。"""
    try:
        return True, await call(tok, tool, **kw)
    except Exception as e:
        return False, str(e)


def pair_post(payload, raw=None):
    """POST /pair：payload 为 dict（取其 code 字段）或直接传 raw bytes。"""
    data = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(PAIR_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body or "{}")
        except Exception:
            return e.code, {"raw": body}
