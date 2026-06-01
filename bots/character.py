"""Individual AI character: messaging, sleep, mood, relationships, bot-to-bot."""
import asyncio
import random
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from aiogram import Bot
from aiogram.types import Message

from database.models import Database
from ai.client import AIClient
from ai.personality import PersonalityGenerator

logger = logging.getLogger(__name__)


class CharacterBot:
    def __init__(self, character_id: int, bot: Bot, db: Database,
                 ai_client: AIClient, pg: PersonalityGenerator, group_id: int):
        self.character_id = character_id
        self.bot = bot
        self.db = db
        self.ai_client = ai_client
        self.pg = pg
        self.group_id = group_id  # ВСЕГДА группа, НЕ личка
        self.personality: Optional[Dict[str, Any]] = None
        self.is_running = False
        self.is_sleeping = False
        self.mood = "neutral"
        self._tasks: List[asyncio.Task] = []

    async def initialize(self):
        self.personality = await self.db.get_character(self.character_id)
        if not self.personality:
            raise ValueError(f"Character {self.character_id} not found")
        sm = await self.db.get_state(self.character_id, "mood")
        ss = await self.db.get_state(self.character_id, "is_sleeping")
        if sm:
            self.mood = sm
        if ss:
            self.is_sleeping = (ss == "true")

    async def start(self):
        self.is_running = True
        logger.info("▶ %s (ID %d)", self.personality["name"], self.character_id)
        self._tasks.append(asyncio.create_task(
            self._message_loop(), name=f"msg-{self.character_id}"))
        self._tasks.append(asyncio.create_task(
            self._mood_loop(), name=f"mood-{self.character_id}"))
        self._tasks.append(asyncio.create_task(
            self._rel_loop(), name=f"rel-{self.character_id}"))

    async def stop(self):
        self.is_running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        # Закрываем сессию бота
        try:
            await self.bot.session.close()
        except Exception:
            pass

    # ── Message loop ───────────────────────────────────────────
    async def _message_loop(self):
        while self.is_running:
            try:
                if self.is_sleeping:
                    await asyncio.sleep(300)
                    continue
                delay = random.uniform(120, 1800)  # 2–30 min
                await asyncio.sleep(delay)
                if random.random() < 0.15:
                    await self._generate_message()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("msg loop: %s", e)
                await asyncio.sleep(60)

    async def _generate_message(self):
        if self.is_sleeping or not self.is_running:
            return
        convs = await self.db.get_conversations(limit=10)
        mems = await self.db.get_memories(self.character_id, limit=5)
        prompt = (
            f"{self.pg.get_system_prompt(self.personality)}\n\n"
            f"Последние сообщения:\n{self._fmt_conv(convs)}\n\n"
            f"Воспоминания:\n{self._fmt_mem(mems)}\n\n"
            f"Настроение: {self.mood}\n\n"
            "Напиши короткое сообщение (1-3 предложения) в группу. Будь естественным."
        )
        try:
            resp = await self.ai_client.generate(
                model=self.personality["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150, temperature=0.8)
            tid = self.personality.get("topic_id")
            await self.bot.send_message(
                chat_id=self.group_id,
                text=resp.strip(),
                message_thread_id=tid if tid else None,
            )
            await self.db.add_conversation(
                fc=self.character_id, tc=None, tid=tid,
                msg=resp.strip(), is_bot=True)
            logger.info("💬 %s: %s", self.personality["name"], resp[:40])
        except Exception as e:
            logger.error("gen msg: %s", e)

    # ── Reply to human ─────────────────────────────────────────
    async def reply_to_message(self, message: Message, sender_name=None):
        if self.is_sleeping:
            if random.random() < 0.3:
                await self._wakeup()
            else:
                return
        if self.mood == "grumpy" and random.random() < 0.4:
            return

        convs = await self.db.get_conversations(limit=5)
        prompt = (
            f"{self.pg.get_system_prompt(self.personality)}\n\n"
            f"{sender_name or 'Кто-то'} написал: \"{message.text}\"\n\n"
            f"Контекст:\n{self._fmt_conv(convs)}\nНастроение: {self.mood}\n\n"
            "Ответь кратко (1-2 предложения). Будь естественным."
        )
        try:
            resp = await self.ai_client.generate(
                model=self.personality["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100, temperature=0.8)
            # Ответ в тот же thread/topic где написал пользователь
            await self.bot.send_message(
                chat_id=self.group_id,
                text=resp.strip(),
                message_thread_id=message.message_thread_id or self.personality.get("topic_id"),
            )
            tid = self.personality.get("topic_id")
            await self.db.add_conversation(
                fc=self.character_id, tc=None, tid=tid,
                msg=resp.strip(), is_bot=True)
            await self.db.add_memory(
                self.character_id,
                f"Ответил {sender_name}: {resp.strip()}", "conversation", 0.6)
        except Exception as e:
            logger.error("reply: %s", e)

    # ── Reply to ANOTHER CHARACTER (bot-to-bot!) ───────────────
    async def reply_to_character(self, other_char_id, other_name, other_message):
        if self.is_sleeping:
            if random.random() < 0.3:
                await self._wakeup()
            else:
                logger.info("💤 %s спит, игнорирует %s",
                            self.personality["name"], other_name)
                return None

        convs = await self.db.get_conversations(limit=5)
        prompt = (
            f"{self.pg.get_system_prompt(self.personality)}\n\n"
            f"{other_name} написал: \"{other_message}\"\n\n"
            f"Контекст:\n{self._fmt_conv(convs)}\n\n"
            f"Настроение: {self.mood}\n\n"
            f"Ответь {other_name}. Будь естественным (1-3 предложения)."
        )
        try:
            resp = await self.ai_client.generate(
                model=self.personality["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150, temperature=0.8)
            tid = self.personality.get("topic_id")
            await self.bot.send_message(
                chat_id=self.group_id,
                text=f"@{other_name} {resp.strip()}" if other_name else resp.strip(),
                message_thread_id=tid if tid else None,
            )
            await self.db.add_conversation(
                fc=self.character_id, tc=other_char_id, tid=tid,
                msg=resp.strip(), is_bot=True)
            await self.db.add_memory(
                self.character_id,
                f"{other_name}: {other_message} → Я: {resp.strip()[:50]}",
                "conversation", 0.7)
            logger.info("💬 %s → %s: %s",
                        self.personality["name"], other_name, resp[:40])
            return resp.strip()
        except Exception as e:
            logger.error("bot-to-bot reply: %s", e)
            return None

    # ── First message (start conversation) ─────────────────────
    async def send_first_message(self):
        prompt = (
            f"{self.pg.get_system_prompt(self.personality)}\n\n"
            "Ты в группе с друзьями. Напиши первое сообщение чтобы начать разговор. "
            "Это может быть приветствие, вопрос или комментарий. 1-2 предложения."
        )
        try:
            resp = await self.ai_client.generate(
                model=self.personality["model"],
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100, temperature=0.9)
            tid = self.personality.get("topic_id")
            await self.bot.send_message(
                chat_id=self.group_id, text=resp.strip(),
                message_thread_id=tid if tid else None)
            await self.db.add_conversation(
                fc=self.character_id, tc=None, tid=tid,
                msg=resp.strip(), is_bot=True)
            logger.info("🎬 %s начал: %s", self.personality["name"], resp[:40])
            return resp.strip()
        except Exception as e:
            logger.error("first msg: %s", e)
            return None

    # ── Mood / sleep ──────────────────────────────────────────
    async def _wakeup(self):
        self.is_sleeping = False
        await self.db.update_character(self.character_id, is_sleeping=False)
        await self.db.save_state(self.character_id, "is_sleeping", "false")
        logger.info("☀️ %s проснулся", self.personality["name"])

    async def _mood_loop(self):
        moods = ["happy", "neutral", "grumpy", "sleepy", "energetic"]
        while self.is_running:
            try:
                nm = random.choice(moods)
                self.mood = nm
                await self.db.update_character(self.character_id, mood=nm)
                await self.db.save_state(self.character_id, "mood", nm)
                if nm == "sleepy" and not self.is_sleeping:
                    self.is_sleeping = True
                    await self.db.update_character(
                        self.character_id, is_sleeping=True)
                    await self.db.save_state(
                        self.character_id, "is_sleeping", "true")
                    logger.info("😴 %s уснул", self.personality["name"])
                elif nm == "energetic" and self.is_sleeping:
                    await self._wakeup()
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("mood: %s", e)
                await asyncio.sleep(300)

    # ── Relationships ─────────────────────────────────────────
    async def _rel_loop(self):
        while self.is_running:
            try:
                for ch in await self.db.get_all_characters():
                    if ch["id"] == self.character_id:
                        continue
                    rel = await self.db.get_relationship(
                        self.character_id, ch["id"])
                    if rel:
                        nc = min(1.0, rel["closeness"] + random.uniform(0.01, 0.05))
                        await self.db.update_relationship(
                            self.character_id, ch["id"], cl=nc)
                    else:
                        await self.db.update_relationship(
                            self.character_id, ch["id"], rt="friend", cl=0.1)
                await asyncio.sleep(7200)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("rel: %s", e)
                await asyncio.sleep(600)

    # ── Helpers ────────────────────────────────────────────────
    @staticmethod
    def _fmt_conv(c):
        if not c:
            return "Нет сообщений"
        return "\n".join(
            f"{'Ты' if x.get('is_bot_message') else 'Кто-то'}: {x['message']}"
            for x in reversed(c[:5]))

    @staticmethod
    def _fmt_mem(m):
        if not m:
            return "Нет воспоминаний"
        return "\n".join(f"- {x['content']}" for x in m)
