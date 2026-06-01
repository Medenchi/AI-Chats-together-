"""Lifecycle — клонирование, отношения, bot-to-bot переписки."""
import asyncio
import random
import json
import logging
from typing import Dict, Any

from database.models import Database
from ai.client import AIClient
from ai.personality import PersonalityGenerator

logger = logging.getLogger(__name__)


class LifecycleManager:
    def __init__(self, db: Database, ai_client: AIClient, pg: PersonalityGenerator, master_bot):
        self.db = db
        self.ai_client = ai_client
        self.pg = pg
        self.master_bot = master_bot
        self.is_running = False

    async def start(self):
        self.is_running = True
        asyncio.create_task(self._clone_loop(), name="clone")
        asyncio.create_task(self._rel_loop(), name="rel")
        asyncio.create_task(self._bot_to_bot_loop(), name="bot2bot")
        logger.info("LifecycleManager started")

    async def stop(self):
        self.is_running = False
        logger.info("LifecycleManager stopped")

    # ── Clone loop ──
    async def _clone_loop(self):
        while self.is_running:
            try:
                for ch in await self.db.get_all_characters(active=True):
                    if await self._should_clone(ch):
                        await self._clone(ch)
                await asyncio.sleep(1800)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Clone loop: %s", e)
                await asyncio.sleep(300)

    async def _should_clone(self, ch: Dict[str, Any]) -> bool:
        clones = await self.db.get_all_characters()
        cnt = sum(
            1 for c in clones
            if c.get("parent_character_id") == ch["id"])
        if cnt >= self.master_bot.config.get("MAX_CLONES", 3):
            return False
        if len(clones) >= self.master_bot.config.get("MAX_BOTS", 10):
            return False
        mood_w = {
            "happy": 0.3, "energetic": 0.25, "neutral": 0.1,
            "grumpy": 0.05, "sleepy": 0.0
        }
        return random.random() < mood_w.get(ch.get("mood", "neutral"), 0.1)

    async def _clone(self, parent: Dict[str, Any]):
        pp = PersonalityGenerator.parse_personality_string(
            parent.get("personality", "{}"))
        if not pp:
            return
        cp = await self.pg.generate_personality(
            model=parent["model"], age=pp.get("age", 16))
        cp["traits"].append(f"похож на {pp.get('name', 'кого-то')}")

        try:
            cid = await self.db.add_character(
                name=cp["name"], age=cp["age"],
                personality=json.dumps(cp, ensure_ascii=False),
                model=parent["model"],
                parent_character_id=parent["id"],
            )
            try:
                await self.master_bot.master_bot.send_message(
                    chat_id=self.master_bot.group_id,
                    text=f"🎉 {pp.get('name', '?')} привёл нового друга — "
                         f"{cp['name']} ({cp['age']} лет)!",
                )
            except Exception:
                pass
            logger.info("🧬 Clone %s created (ID %d)", cp["name"], cid)
        except Exception as e:
            logger.error("Clone failed: %s", e)

    # ── Relationships loop ──
    async def _rel_loop(self):
        while self.is_running:
            try:
                for ch in await self.db.get_all_characters(active=True):
                    await self._check_rels(ch)
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Rel loop: %s", e)
                await asyncio.sleep(600)

    async def _check_rels(self, ch: Dict[str, Any]):
        for other in await self.db.get_all_characters(active=True):
            if other["id"] == ch["id"]:
                continue
            rel = await self.db.get_relationship(ch["id"], other["id"])
            if rel and rel["closeness"] > 0.8 and rel["relationship_type"] == "friend":
                await self.db.update_relationship(
                    ch["id"], other["id"], rt="close_friend")
                p1_name = PersonalityGenerator.parse_personality_string(
                    ch.get("personality", "{}")).get("name", "?")
                p2_name = PersonalityGenerator.parse_personality_string(
                    other.get("personality", "{}")).get("name", "?")
                logger.info("💕 %s & %s → close friends!", p1_name, p2_name)
                try:
                    await self.master_bot.master_bot.send_message(
                        chat_id=self.master_bot.group_id,
                        text=f"💕 {p1_name} и {p2_name} "
                             f"стали очень близкими друзьями!",
                    )
                except Exception:
                    pass
            if rel and rel["closeness"] > 0.6:
                await self._interact(ch, other)

    async def _interact(self, c1: Dict[str, Any], c2: Dict[str, Any]):
        if random.random() >= 0.2:
            return
        p1 = PersonalityGenerator.parse_personality_string(
            c1.get("personality", "{}"))
        bot1 = self.master_bot.bots.get(c1["id"])
        if not bot1:
            return
        try:
            name = p1.get("name", "кто-то")
            resp = await self.ai_client.generate(
                model=c1["model"],
                messages=[{
                    "role": "user",
                    "content": f"{name} говорит что-то дружеское "
                               f"(1-2 предложения, без эмодзи)."
                }],
                max_tokens=80, temperature=0.8,
            )
            await bot1.bot.send_message(
                chat_id=self.master_bot.group_id,
                text=f"{name}: {resp.strip()}",
                message_thread_id=c1.get("topic_id"),
            )
        except Exception as e:
            logger.error("Interact failed: %s", e)

    # ── Bot-to-bot conversation loop ──
    async def _bot_to_bot_loop(self):
        """Периодически два бота начинают переписку."""
        while self.is_running:
            try:
                active = [b for b in self.master_bot.bots.values()
                          if not b.is_sleeping]
                if len(active) >= 2 and random.random() < 0.35:
                    c1 = random.choice(active)
                    others = [b for b in active
                              if b.character_id != c1.character_id]
                    c2 = random.choice(others)

                    logger.info("🗣 Bot-to-bot: %s → %s",
                                c1.personality["name"],
                                c2.personality["name"])

                    first_msg = await c1.send_first_message()
                    if not first_msg:
                        await asyncio.sleep(300)
                        continue

                    await asyncio.sleep(random.uniform(5, 15))

                    reply = await c2.reply_to_character(
                        other_char_id=c1.character_id,
                        other_name=c1.personality["name"],
                        other_message=first_msg,
                    )

                    if reply:
                        turns = random.randint(2, 4)
                        for i in range(turns):
                            await asyncio.sleep(random.uniform(5, 20))
                            speaker = c1 if i % 2 == 0 else c2
                            other = c2 if i % 2 == 0 else c1
                            reply = await speaker.reply_to_character(
                                other_char_id=other.character_id,
                                other_name=other.personality["name"],
                                other_message=reply,
                            )
                            if not reply:
                                break

                await asyncio.sleep(300)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Bot-to-bot loop: %s", e)
                await asyncio.sleep(120)

    # ── Topic creation helper ──
    async def create_topic(self, character_id: int, name: str):
        try:
            topic = await self.master_bot.master_bot.create_forum_topic(
                chat_id=self.master_bot.group_id,
                name=f"💬 {name}",
                icon_color=random.choice(
                    [0x6FB9F0, 0xFFD67E, 0xCB86DB,
                     0x8EEE98, 0xFF93B0, 0xFB6F5F]),
            )
            await self.db.add_topic(
                topic.message_thread_id, name, character_id)
            await self.db.update_character_topic(
                character_id, topic.message_thread_id)
            logger.info("📂 Topic '%s' created (thread_id=%d)",
                        name, topic.message_thread_id)
            return topic.message_thread_id
        except Exception as e:
            logger.error("Topic creation: %s", e)
            return None
