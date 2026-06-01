"""SQLite database for AI Chats Together."""
import os
import aiosqlite


class Database:
    def __init__(self, path: str = "data/bots.db"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS characters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    personality TEXT NOT NULL,
                    model TEXT NOT NULL,
                    bot_token TEXT,
                    bot_username TEXT,
                    bot_id INTEGER,
                    topic_id INTEGER,
                    is_active INTEGER DEFAULT 1,
                    is_sleeping INTEGER DEFAULT 0,
                    mood TEXT DEFAULT 'neutral',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    parent_character_id INTEGER,
                    FOREIGN KEY (parent_character_id) REFERENCES characters(id)
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    memory_type TEXT DEFAULT 'conversation',
                    importance REAL DEFAULT 0.5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (character_id) REFERENCES characters(id)
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_character_id INTEGER NOT NULL,
                    to_character_id INTEGER,
                    topic_id INTEGER,
                    message TEXT NOT NULL,
                    is_bot_message INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_character_id) REFERENCES characters(id),
                    FOREIGN KEY (to_character_id) REFERENCES characters(id)
                );
                CREATE TABLE IF NOT EXISTS relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character1_id INTEGER NOT NULL,
                    character2_id INTEGER NOT NULL,
                    relationship_type TEXT DEFAULT 'friend',
                    closeness REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(character1_id, character2_id),
                    FOREIGN KEY (character1_id) REFERENCES characters(id),
                    FOREIGN KEY (character2_id) REFERENCES characters(id)
                );
                CREATE TABLE IF NOT EXISTS bot_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character_id INTEGER NOT NULL,
                    state_key TEXT NOT NULL,
                    state_value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(character_id, state_key),
                    FOREIGN KEY (character_id) REFERENCES characters(id)
                );
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_id INTEGER NOT NULL UNIQUE,
                    topic_name TEXT NOT NULL,
                    created_by_character_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by_character_id) REFERENCES characters(id)
                );
            """)
            await db.commit()

    async def add_character(self, name, age, personality, model,
                            bot_token=None, bot_username=None, bot_id=None,
                            topic_id=None, parent_character_id=None):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "INSERT INTO characters "
                "(name,age,personality,model,bot_token,bot_username,bot_id,topic_id,parent_character_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (name, age, personality, model, bot_token, bot_username, bot_id,
                 topic_id, parent_character_id))
            await db.commit()
            return c.lastrowid

    async def get_character(self, cid):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            r = await (await db.execute(
                "SELECT * FROM characters WHERE id=?", (cid,))).fetchone()
            return dict(r) if r else None

    async def get_all_characters(self, active=True):
        """Получить всех персонажей. active=True — только активные."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            q = "SELECT * FROM characters"
            if active:
                q += " WHERE is_active=1"
            return [dict(r) for r in await (await db.execute(q)).fetchall()]

    async def update_character(self, cid, **kw):
        if not kw:
            return
        # Валидация имён колонок — защита от SQL injection
        valid_cols = {
            'name', 'age', 'personality', 'model', 'bot_token', 'bot_username',
            'bot_id', 'topic_id', 'is_active', 'is_sleeping', 'mood',
            'parent_character_id'
        }
        filtered = {k: v for k, v in kw.items() if k in valid_cols}
        if not filtered:
            return
        s = ", ".join(f"{k}=?" for k in filtered)
        v = list(filtered.values()) + [cid]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE characters SET {s} WHERE id=?", v)
            await db.commit()

    async def add_memory(self, cid, content, mt="conversation", imp=0.5):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO memories (character_id,content,memory_type,importance) VALUES (?,?,?,?)",
                (cid, content, mt, imp))
            await db.commit()

    async def get_memories(self, cid, limit=10, mt=None):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            q, p = "SELECT * FROM memories WHERE character_id=?", [cid]
            if mt:
                q += " AND memory_type=?"
                p.append(mt)
            q += " ORDER BY created_at DESC LIMIT ?"
            p.append(limit)
            return [dict(r) for r in await (await db.execute(q, p)).fetchall()]

    async def add_conversation(self, fc, tc, msg, tid=None, is_bot=False):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO conversations "
                "(from_character_id,to_character_id,topic_id,message,is_bot_message) "
                "VALUES (?,?,?,?,?)",
                (fc, tc, tid, msg, int(is_bot)))
            await db.commit()

    async def get_conversations(self, cid=None, tid=None, limit=20):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            q, p = "SELECT * FROM conversations WHERE 1=1", []
            if cid:
                q += " AND (from_character_id=? OR to_character_id=?)"
                p += [cid, cid]
            if tid:
                q += " AND topic_id=?"
                p.append(tid)
            q += " ORDER BY created_at DESC LIMIT ?"
            p.append(limit)
            return [dict(r) for r in await (await db.execute(q, p)).fetchall()]

    async def update_relationship(self, c1, c2, rt=None, cl=None):
        async with aiosqlite.connect(self.path) as db:
            r = await (await db.execute(
                "SELECT id,closeness FROM relationships "
                "WHERE character1_id=? AND character2_id=?", (c1, c2))).fetchone()
            if r:
                u, v = [], []
                if rt is not None:
                    u.append("relationship_type=?")
                    v.append(rt)
                if cl is not None:
                    u.append("closeness=?")
                    v.append(cl)
                if u:
                    v.append(r[0])
                    await db.execute(
                        f"UPDATE relationships SET {','.join(u)} WHERE id=?", v)
            else:
                await db.execute(
                    "INSERT INTO relationships "
                    "(character1_id,character2_id,relationship_type,closeness) "
                    "VALUES (?,?,?,?)",
                    (c1, c2, rt or "friend", cl or 0.0))
            await db.commit()

    async def get_relationship(self, c1, c2):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            r = await (await db.execute(
                "SELECT * FROM relationships "
                "WHERE character1_id=? AND character2_id=?", (c1, c2))).fetchone()
            return dict(r) if r else None

    async def save_state(self, cid, key, val):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO bot_states (character_id,state_key,state_value) "
                "VALUES (?,?,?) "
                "ON CONFLICT(character_id,state_key) DO UPDATE "
                "SET state_value=?, updated_at=CURRENT_TIMESTAMP",
                (cid, key, val, val))
            await db.commit()

    async def get_state(self, cid, key):
        async with aiosqlite.connect(self.path) as db:
            r = await (await db.execute(
                "SELECT state_value FROM bot_states "
                "WHERE character_id=? AND state_key=?", (cid, key))).fetchone()
            return r[0] if r else None

    async def add_topic(self, tid, tname, cbid=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO topics "
                "(topic_id,topic_name,created_by_character_id) VALUES (?,?,?)",
                (tid, tname, cbid))
            await db.commit()

    async def update_character_topic(self, cid, tid):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE characters SET topic_id=? WHERE id=?", (tid, cid))
            await db.commit()

    async def get_topic(self, tid):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            r = await (await db.execute(
                "SELECT * FROM topics WHERE topic_id=?", (tid,))).fetchone()
            return dict(r) if r else None

    async def update_bot_token(self, cid, token, username, bid=None):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE characters SET bot_token=?, bot_username=?, bot_id=? WHERE id=?",
                (token, username, bid, cid))
            await db.commit()

    async def get_character_by_username(self, username):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            r = await (await db.execute(
                "SELECT * FROM characters WHERE bot_username=?", (username,))).fetchone()
            return dict(r) if r else None
