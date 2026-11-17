import sys
sys.path.insert(0, ".")
import importlib.util
spec = importlib.util.spec_from_file_location("rw", "room_watch.py")
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)
f = rw.parse_template_command
names = ["出图", "调研", "Weekly", "Weekend"]
cases = [
    ("模板：出图 一只猫", ("hit", "出图", "一只猫")),
    ("模板：Weekly 报告", ("hit", "Weekly", "报告")),
    ("模板：weekly 报告", ("hit", "Weekly", "报告")),       # W5: 全小写命中
    ("模板：WEEKLY 报告", ("hit", "Weekly", "报告")),       # W5: 全大写命中
    ("模板：week 报告", ("ambiguous", "week", ["Weekly", "Weekend"])),  # 前缀歧义（大小写不敏感）
    ("模板：weeke 报告", ("hit", "Weekend", "报告")),       # 唯一前缀（小写）
    ("模板：不存在 x", ("unknown", "不存在", None)),
    ("普通消息", None),
]
bad = 0
for body, want in cases:
    got = f(body, names)
    ok = got == want
    bad += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + repr(body) + ("" if ok else f"  got={got} want={want}"))
print("RESULT:", "ALL PASS" if not bad else f"{bad} FAILURES")
sys.exit(1 if bad else 0)
