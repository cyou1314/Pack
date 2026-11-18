#!/usr/bin/env python3
"""hub-staging30：room_watch.resolve_mention 回归（第 2 轮深查 W1/W2）。
跑法：/home/hatch/workspace/pack/hub/.venv/bin/python test_resolve_mention.py
只测纯函数，不连 hub、不写库。"""
import importlib.util
import io

# room_watch.py 顶层读 .dispatcher_token；测试只用纯函数，用桩 open 挡掉，
# 不复制真实 token、不连 hub。
_real_open = open


def _stub_open(path, *a, **k):
    if str(path).endswith(".dispatcher_token"):
        return io.StringIO("dummy-test-token")
    return _real_open(path, *a, **k)


import builtins
builtins.open = _stub_open
try:
    spec = importlib.util.spec_from_file_location("rw", "room_watch.py")
    rw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rw)
finally:
    builtins.open = _real_open

CASES = [
    # (body, targets, expected_kind, expected_name)
    ("@scout @absent 帮我出图", ["absent", "scout"], "hit", "scout"),   # W1：正文先出现者胜
    ("@absent @scout 两件事", ["absent", "scout"], "hit", "absent"),  # W1：正文先出现者胜
    ("@s @absent 前缀在前", ["absent", "scout"], "hit", "scout"),      # W1：前缀在前不被后精确抢走
    ("请发到 foo@absent.com 谢谢", ["absent", "scout"], "none", None), # W2：邮箱不触发
    ("见 https://x.com/@absent/post", ["absent", "scout"], "none", None),  # W2：URL 里不触发
    ("@a 帮我", ["absent", "scout"], "hit", "absent"),                 # 旧行为：唯一前缀
    ("@ABSENT 大写", ["absent", "scout"], "hit", "absent"),            # 旧行为：大小写不敏感
    ("@scoutx 不是成员", ["absent", "scout"], "none", None),           # 旧行为：后缀阻断
    ("@absent。标点后中文", ["absent", "scout"], "hit", "absent"),     # 旧行为：中文标点后可命中
    ("（@absent）括号里", ["absent", "scout"], "hit", "absent"),       # 括号前可命中
    ("@alice @albert 两个", ["alice", "albert"], "hit", "alice"),      # 精确命中优先于歧义前缀
    ("@al 帮我", ["alice", "albert"], "ambiguous", None),              # 歧义前缀仍报歧义
    ("没提任何人", ["absent", "scout"], "none", None),
    ("＠absent 全角", ["absent", "scout"], "hit", "absent"),           # 全角＠
    ("foo＠absent.com 全角邮箱", ["absent", "scout"], "none", None),   # W2：全角邮箱也不触发
]

fails = 0
for body, targets, ek, en in CASES:
    kind, name, span, cands = rw.resolve_mention(body, targets)
    ok = (kind == ek and name == en)
    if not ok:
        fails += 1
    print(("PASS" if ok else "FAIL"), repr(body), "->", (kind, name),
          "" if ok else f"expected ({ek}, {en})")
print(f"RESULT pass={len(CASES) - fails} fail={fails}")
raise SystemExit(1 if fails else 0)
