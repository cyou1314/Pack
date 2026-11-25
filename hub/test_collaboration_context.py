import sqlite3
import unittest

from collaboration_store import initialize, write_transaction, create_run, claim_step, finish_step
from collaboration_dispatch import bind_task, reserve_claim
from collaboration_context import grant_context, read_context, revoke_context


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
        CREATE TABLE rooms(id INTEGER PRIMARY KEY,archived_at REAL);
        INSERT INTO rooms VALUES(1,NULL),(2,NULL);
        CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT,role TEXT,kind TEXT,
            removed_at REAL,provider TEXT,access_mode TEXT,bound_room_id INTEGER);
        INSERT INTO members VALUES(1,'owner','member',NULL,NULL,NULL,NULL,NULL),
            (2,'worker','member',NULL,NULL,'a','full',NULL),
            (3,'leader','member',NULL,NULL,'b','full',NULL),
            (4,'reviewer','member',NULL,NULL,'c','full',NULL),
            (5,'external','member','external',NULL,'x','full',1);
        CREATE TABLE messages(id INTEGER PRIMARY KEY,room_id INTEGER,author_id INTEGER,
            kind TEXT,body TEXT,edited_at REAL,created_at REAL,deleted_at REAL);
        INSERT INTO messages VALUES(10,1,1,'chat','root',NULL,1,NULL),
            (11,2,1,'chat','other room',NULL,1,NULL),
            (12,1,2,'chat','not owner',NULL,1,NULL),
            (13,1,1,'chat','deleted',NULL,1,2);
        CREATE TABLE tasks(id INTEGER PRIMARY KEY,room_id INTEGER,assignee INTEGER,
            status TEXT,attempts INTEGER DEFAULT 0,lease_expires REAL,result TEXT,updated_at REAL);
        """)
        initialize(self.conn)
        with write_transaction(self.conn):
            self.run=create_run(self.conn,room_id=1,source_message_id=10,deadline=100,now=1,
                actor_role="owner",mode="team",eligible={"2","3","4"},leader="3",
                workers=("2",),reviewer="4")

    def grant(self,key,member,refs,actor=1):
        return grant_context(self.conn,self.run,key,actor_id=actor,member_id=member,refs=refs,now=2)

    def bind(self,key,member,task):
        self.conn.execute("INSERT INTO tasks(id,room_id,assignee,status) VALUES(?,1,?,'open')",(task,member))
        bind_task(self.conn,self.run,key,task_id=task,member_id=member,now=2)
        fence=reserve_claim(self.conn,task,member_id=member,eligible_ids={member},now=2)
        self.assertTrue(fence)
        self.conn.execute("UPDATE tasks SET status='claimed',attempts=1,lease_expires=90 WHERE id=?",(task,))
        return fence

    def test_sources_and_roles(self):
        root=[("owner_message",10)]
        with write_transaction(self.conn):
            for refs in ([("owner_message",11)],root+[("owner_message",11)],
                         root+[("owner_message",12)],root+[("owner_message",13)],
                         root+[("private",1)],root+root,root+[("dependency_result",99)]):
                with self.assertRaises(ValueError):
                    self.grant("work-0",2,refs)
            for actor in (2,3,5):
                with self.assertRaises(ValueError):
                    self.grant("work-0",2,root,actor)
            with self.assertRaises(ValueError):
                self.grant("review",4,root)
            with self.assertRaises(ValueError):
                self.grant("work-0",3,root)
            self.assertTrue(self.grant("work-0",2,root))
            self.assertFalse(self.grant("work-0",2,root))
            fence=self.bind("work-0",2,20)
            with self.assertRaises(ValueError):
                read_context(self.conn,20,member_id=3,now=3)
            self.assertEqual(read_context(self.conn,20,member_id=2,now=3)["items"][0]["text"],"root")
            self.conn.execute("UPDATE tasks SET status='done',result='worker result',updated_at=3 WHERE id=20")
            self.assertTrue(finish_step(self.conn,self.run,"work-0",fence=fence,succeeded=True,now=3))
            with self.assertRaises(ValueError):
                self.grant("review",4,root)
            self.assertTrue(self.grant("review",4,root+[("dependency_result",20)]))
            self.bind("review",4,21)
            result=read_context(self.conn,21,member_id=4,now=4)
            self.assertEqual([x["text"] for x in result["items"]],["root","worker result"])
            self.conn.execute("UPDATE tasks SET result='changed' WHERE id=20")
            with self.assertRaisesRegex(ValueError,"source changed"):
                read_context(self.conn,21,member_id=4,now=4)

    def test_budget_revocation_and_destination(self):
        root=[("owner_message",10)]
        with write_transaction(self.conn):
            self.conn.execute("UPDATE messages SET body=? WHERE id=10",("中"*6000,))
            with self.assertRaisesRegex(ValueError,"byte budget"):
                self.grant("work-0",2,root)
            self.conn.execute("UPDATE messages SET body='root' WHERE id=10")
            self.grant("work-0",2,root)
            self.bind("work-0",2,20)
            self.conn.execute("UPDATE members SET provider='changed' WHERE id=2")
            with self.assertRaises(ValueError):
                read_context(self.conn,20,member_id=2,now=4)
            self.conn.execute("UPDATE members SET provider='a' WHERE id=2")
            self.assertTrue(revoke_context(self.conn,self.run,"work-0",actor_id=1))
            with self.assertRaises(ValueError):
                read_context(self.conn,20,member_id=2,now=4)


if __name__ == "__main__":
    unittest.main()
