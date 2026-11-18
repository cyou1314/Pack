"""D1 孤儿行检查器：hub 全表无 FOREIGN KEY 声明，引用完整性靠本检查兜底。
用法：python3 orphans.py <pack.db 路径>；发现孤儿时退出码 1 并逐项打印计数。"""
import sqlite3, sys

CHECKS = [
    ("tasks.room_id", "SELECT COUNT(*) FROM tasks t LEFT JOIN rooms r ON r.id=t.room_id WHERE r.id IS NULL"),
    ("tasks.assignee", "SELECT COUNT(*) FROM tasks t LEFT JOIN members m ON m.id=t.assignee WHERE t.assignee IS NOT NULL AND m.id IS NULL"),
    ("tasks.created_by", "SELECT COUNT(*) FROM tasks t LEFT JOIN members m ON m.id=t.created_by WHERE m.id IS NULL"),
    ("messages.room_id", "SELECT COUNT(*) FROM messages x LEFT JOIN rooms r ON r.id=x.room_id WHERE r.id IS NULL"),
    ("messages.author_id", "SELECT COUNT(*) FROM messages x LEFT JOIN members m ON m.id=x.author_id WHERE m.id IS NULL"),
    ("messages.task_id", "SELECT COUNT(*) FROM messages x LEFT JOIN tasks t ON t.id=x.task_id WHERE x.task_id IS NOT NULL AND t.id IS NULL"),
    ("runs.task_id", "SELECT COUNT(*) FROM runs x LEFT JOIN tasks t ON t.id=x.task_id WHERE t.id IS NULL"),
    ("runs.member_id", "SELECT COUNT(*) FROM runs x LEFT JOIN members m ON m.id=x.member_id WHERE m.id IS NULL"),
    ("attachments.room_id", "SELECT COUNT(*) FROM attachments a LEFT JOIN rooms r ON r.id=a.room_id WHERE r.id IS NULL"),
    ("attachments.uploader_id", "SELECT COUNT(*) FROM attachments a LEFT JOIN members m ON m.id=a.uploader_id WHERE m.id IS NULL"),
    ("attachments.message_id", "SELECT COUNT(*) FROM attachments a LEFT JOIN messages x ON x.id=a.message_id WHERE a.message_id IS NOT NULL AND x.id IS NULL"),
    ("task_transfers.task_id", "SELECT COUNT(*) FROM task_transfers x LEFT JOIN tasks t ON t.id=x.task_id WHERE t.id IS NULL"),
    ("task_transfers.from_member", "SELECT COUNT(*) FROM task_transfers x LEFT JOIN members m ON m.name=x.from_member WHERE m.id IS NULL"),
    ("task_transfers.to_member", "SELECT COUNT(*) FROM task_transfers x LEFT JOIN members m ON m.name=x.to_member WHERE m.id IS NULL"),
    ("member_skills.member_id", "SELECT COUNT(*) FROM member_skills x LEFT JOIN members m ON m.id=x.member_id WHERE m.id IS NULL"),
    ("member_skills.skill_id", "SELECT COUNT(*) FROM member_skills x LEFT JOIN skills s ON s.id=x.skill_id WHERE s.id IS NULL"),
    ("member_memory_versions.no_current", "SELECT COUNT(*) FROM member_memory_versions v WHERE NOT EXISTS (SELECT 1 FROM member_memory mm WHERE mm.member=v.member AND mm.scope=v.scope AND mm.\"key\"=v.\"key\")"),
    ("room_summaries.room_id", "SELECT COUNT(*) FROM room_summaries x LEFT JOIN rooms r ON r.id=x.room_id WHERE r.id IS NULL"),
    ("review_log.task_id", "SELECT COUNT(*) FROM review_log x LEFT JOIN tasks t ON t.id=x.task_id WHERE t.id IS NULL"),
    ("desktop_lock.task_id", "SELECT COUNT(*) FROM desktop_lock x LEFT JOIN tasks t ON t.id=x.task_id WHERE x.task_id IS NOT NULL AND t.id IS NULL"),
]

def check_orphans(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = {}
    for name, sql in CHECKS:
        out[name] = conn.execute(sql).fetchone()[0]
    conn.close()
    return out

if __name__ == "__main__":
    res = check_orphans(sys.argv[1])
    bad = {k: v for k, v in res.items() if v}
    for k, v in res.items():
        print(f"{'ORPHAN ' if v else 'ok     '}{k}: {v}")
    print("RESULT:", "CLEAN" if not bad else f"{sum(bad.values())} orphans in {len(bad)} checks")
    sys.exit(1 if bad else 0)
