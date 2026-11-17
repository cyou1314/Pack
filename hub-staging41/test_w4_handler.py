import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import room_watch as rw

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

class StubResp:
    def __init__(self, payload): self.content = [type("C", (), {"text": payload})()]

class StubClient:
    def __init__(self, fail_create=False, fail_post=False):
        self.fail_create, self.fail_post = fail_create, fail_post
        self.created, self.posts = [], []
    async def call_tool(self, name, args):
        if name == "task_create":
            if self.fail_create: raise RuntimeError("hub down")
            self.created.append(args)
            return StubResp('{"task_id": 99}')
        if name == "room_post":
            if self.fail_post: raise RuntimeError("post lost")
            self.posts.append(args)
            return StubResp('{"message_id": 1}')
        raise AssertionError(name)

MSG = {"id": 7, "kind": "chat", "author": "owner", "body": "@absent hello task"}

async def main():
    # 1. task_create raises -> propagates (run loop will retry, cursor stays)
    c = StubClient(fail_create=True)
    try:
        await rw._handle_message(c, "pack-main", MSG, ["absent"], [])
        check("create failure propagates", False)
    except RuntimeError:
        check("create failure propagates", True)
    check("no task created on failure", len(c.created) == 0)

    # 2. create ok, confirm post fails -> swallowed, exactly one task
    c = StubClient(fail_post=True)
    await rw._handle_message(c, "pack-main", MSG, ["absent"], [])
    check("confirm failure swallowed", True)
    check("exactly one task created", len(c.created) == 1, str(len(c.created)))

    # 3. happy path
    c = StubClient()
    await rw._handle_message(c, "pack-main", MSG, ["absent"], [])
    check("happy: one task + one confirm", len(c.created) == 1 and len(c.posts) == 1)

    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL

sys.exit(1 if asyncio.run(main()) else 0)
