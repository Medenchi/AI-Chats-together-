"""Master Bot — Managed Bots API, inline кнопки, топики В ГРУППЕ, start_chat."""
import os
import json
import random
import asyncio
import logging
import re
from typing import Dict, Optional
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext

from database.models import Database
from ai.client import AIClient
from ai.personality import PersonalityGenerator
from bots.character import CharacterBot

logger = logging.getLogger(__name__)


class MasterBot:
    def __init__(self, config: dict):
        self.config = config
        self.group_id = config["GROUP_ID"]
        self.db = Database(config.get("DATABASE_PATH", "data/bots.db"))
        self.ai_client = AIClient(
            api_key=config["FREEMODEL_API_KEY"],
            base_url=config.get("FREEMODEL_BASE_URL", "https://freemodel.dev/v1"),
        )
        self.pg = PersonalityGenerator(self.ai_client)
        self.master_token = config["MASTER_BOT_TOKEN"]
        self.master_bot = Bot(token=self.master_token)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.bots: Dict[int, CharacterBot] = {}
        # pending_creations: user_id -> {char_id, model, personality, suggested_username}
        self.pending_creations: Dict[int, Dict] = {}

    async def initialize(self):
        await self.db.init()
        Path("data").mkdir(exist_ok=True)

        # Load existing characters with bot tokens
        characters = await self.db.get_all_characters(active_only=True)
        for char in characters:
            if char.get("bot_token"):
                try:
                    bot = Bot(token=char["bot_token"])
                    me = await bot.get_me()
                    cb = CharacterBot(
                        character_id=char["id"], bot=bot, db=self.db,
                        ai_client=self.ai_client, pg=self.pg, group_id=self.group_id,
                    )
                    await cb.initialize()
                    self.bots[char["id"]] = cb
                    logger.info("Loaded: %s (@%s)", char["name"], me.username)
                except Exception as e:
                    logger.error("Failed to load bot for %s: %s", char.get("name"), e)
        logger.info("Master bot ready. %d characters loaded.", len(self.bots))

    async def start(self):
        self._setup_handlers()
        for bot in self.bots.values():
            await bot.start()
        logger.info("Master bot polling started.")
        await self.dp.start_polling(self.master_bot)

    async def stop(self):
        for bot in self.bots.values():
            await bot.stop()
        await self.master_bot.session.close()

    # ──────────────────────── HANDLERS ────────────────────────
    def _setup_handlers(self):

        # /start — главное меню с кнопками
        @self.dp.message(Command("start"))
        async def cmd_start(message: Message):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎭 Создать персонажа", callback_data="create_character")],
                [InlineKeyboardButton(text="📋 Список персонажей", callback_data="list_chars")],
                [InlineKeyboardButton(text="📊 Статус", callback_data="status_chars")],
                [InlineKeyboardButton(text="🗣 Начать переписку", callback_data="start_chat")],
                [InlineKeyboardButton(text="📂 Создать топики в группе", callback_data="create_topics")],
            ])
            await message.answer(
                "🤖 AI-Chats-Together v2\n\n"
                "Управляй персонажами через кнопки ниже.\n"
                "Создай персонажа — он получит свой характер, бот и топик в группе!",
                reply_markup=kb)

        # ══════════ СОЗДАНИЕ ПЕРСОНАЖА (inline кнопка) ══════════
        @self.dp.callback_query(lambda c: c.data == "create_character")
        async def cb_create(callback: CallbackQuery, state: FSMContext):
            await callback.answer()

            total = len(await self.db.get_all_characters())
            max_bots = self.config.get("MAX_BOTS", 10)
            if total >= max_bots:
                await callback.message.answer(f"❌ Максимум {max_bots} ботов достигнуто!")
                return

            model = random.choice(self.config.get("AVAILABLE_MODELS", ["gpt-4o"]))
            await callback.message.answer(f"🎭 Генерирую характер (модель: {model})... Подожди ~10 сек...")

            # Генерируем характер через AI
            personality = await self.pg.generate_personality(model=model)

            # Сохраняем в БД (пока без bot_token)
            char_id = await self.db.add_character(
                name=personality["name"], age=personality["age"],
                personality=str(personality), model=model)

            suggested_username = f"ai_{personality['name'].lower().replace(' ', '_')}_{char_id}_bot"
            # Clean username
            suggested_username = re.sub(r'[^a-z0-9_]', '_', suggested_username)[:32]

            self.pending_creations[callback.from_user.id] = {
                "char_id": char_id,
                "model": model,
                "personality": personality,
                "suggested_username": suggested_username,
            }

            # Managed Bot deep link
            me = await self.master_bot.get_me()
            deep_link = f"https://t.me/{me.username}?start=create_bot_{char_id}_{suggested_username}"

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Создать бота (откроет Telegram)", url=deep_link)],
            ])

            await callback.message.answer(
                f"✅ Персонаж сгенерирован!\n\n"
                f"👤 **{personality['name']}** ({personality['age']} лет)\n"
                f"🧠 Черты: {', '.join(personality['traits'])}\n"
                f"🎨 Хобби: {', '.join(personality['hobbies'])}\n"
                f"📖 {personality['backstory']}\n"
                f"💬 Стиль: {personality['communication_style']}\n"
                f"🤖 Модель: {model}\n\n"
                f"👇 Нажми кнопку ниже:\n"
                f"Откроется чат с ботом → подтверди создание → бот подключится автоматически!",
                parse_mode="Markdown",
                reply_markup=kb)

        # ══════════ HANDLE deep link: /start create_bot_{id}_{username} ══════════
        @self.dp.message(Command("start"))
        async def cmd_start_deeplink(message: Message, state: FSMContext):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                return

            start_param = args[1]
            if not start_param.startswith("create_bot_"):
                return

            # Parse: create_bot_{char_id}_{username}
            m = re.match(r"create_bot_(\d+)_(.+)", start_param)
            if not m:
                await message.answer("⚠️ Неправильный формат ссылки.")
                return

            char_id = int(m.group(1))
            suggested_username = m.group(2)

            pending = self.pending_creations.get(message.from_user.id)
            if not pending or pending.get("char_id") != char_id:
                await message.answer("⚠️ Сессия создания истекла. Начни заново через /start.")
                return

            personality = pending["personality"]
            model = pending["model"]

            await message.answer(f"⏳ Создаю бота для **{personality['name']}**...", parse_mode="Markdown")

            try:
                # Получаем токен через Managed Bots API
                bot_info = await self._get_managed_bot_token(
                    user_id=message.from_user.id,
                    suggested_username=suggested_username,
                )

                if bot_info and bot_info.get("token"):
                    bot_token = bot_info["token"]
                    bot_username = bot_info.get("username", suggested_username)
                    bot_id = bot_info.get("id")

                    # Обновляем БД
                    await self.db.update_bot_token(char_id, bot_token, bot_username, bot_id)

                    # Создаём CharacterBot
                    bot = Bot(token=bot_token)
                    cb = CharacterBot(
                        character_id=char_id, bot=bot, db=self.db,
                        ai_client=self.ai_client, pg=self.pg, group_id=self.group_id,
                    )
                    await cb.initialize()
                    self.bots[char_id] = cb
                    await cb.start()

                    # Создаём топик В ГРУППЕ
                    topic_id = await self.create_topic_in_group(char_id, personality["name"])
                    if topic_id:
                        personality["topic_id"] = topic_id
                        await self.db.update_character_topic(char_id, topic_id)
                        await self.db.update_character(char_id, personality=str(personality))

                    await message.answer(
                        f"🎉 **{personality['name']}** создан и подключён!\n"
                        f"📂 Топик в группе: {'✅' if topic_id else '❌ (создай через /topics)'}\n"
                        f"Теперь он начнёт общаться в группе!",
                        parse_mode="Markdown")
                else:
                    await message.answer(
                        f"⚠️ Бот {personality['name']} создан в базе, но токен не получен.\n"
                        f"Создай бота вручную через @BotFather и добавь токен.")

            except Exception as e:
                logger.error("Managed bot creation failed: %s", e)
                await message.answer(f"❌ Ошибка: {e}")

            self.pending_creations.pop(message.from_user.id, None)

        # ══════════ STATUS ══════════
        @self.dp.callback_query(lambda c: c.data == "status_chars")
        async def cb_status(callback: CallbackQuery):
            await callback.answer()
            chars = await self.db.get_all_characters(active_only=True)
            if not chars:
                await callback.message.answer("📋 Персонажей пока нет.")
                return

            emojis = {"happy":"😊","neutral":"😐","grumpy":"😠","sleepy":"😴","energetic":"⚡"}
            lines = ["📊 Статус персонажей:"]
            for c in chars:
                p = eval(c.get("personality", "{}"))
                icon = "😴" if c.get("is_sleeping") else "✅"
                mood = emojis.get(c.get("mood", "neutral"), "❓")
                has_bot = "🤖" if c.get("bot_token") else "⏳"
                lines.append(f"{icon} {mood} {p.get('name', c['name'])} ({c['age']} лет) — {c.get('model','?')} {has_bot}")
            await callback.message.answer("\n".join(lines))

        # ══════════ LIST ══════════
        @self.dp.callback_query(lambda c: c.data == "list_chars")
        async def cb_list(callback: CallbackQuery):
            await callback.answer()
            chars = await self.db.get_all_characters(active_only=True)
            if not chars:
                await callback.message.answer("📋 Пусто")
                return
            lines = ["👥 Персонажи:"]
            for c in chars:
                p = eval(c.get("personality", "{}"))
                lines.append(f"• {p.get('name', c['name'])} ({c['age']} лет) — {c.get('model','?')}")
            await callback.message.answer("\n".join(lines))

        # ══════════ START CHAT (первый пишет, второй отвечает) ══════════
        @self.dp.callback_query(lambda c: c.data == "start_chat")
        async def cb_start_chat(callback: CallbackQuery):
            await callback.answer()
            active = [b for b in self.bots.values() if not b.is_sleeping]
            if len(active) < 2:
                await callback.message.answer("❌ Нужно минимум 2 активных бота!")
                return

            first = random.choice(active)
            second = random.choice([b for b in active if b.character_id != first.character_id])

            await callback.message.answer(f"🗣 **{first.personality['name']}** начинает переписку...", parse_mode="Markdown")

            # Первый пишет
            first_msg = await first.send_first_message()
            if not first_msg:
                await callback.message.answer("❌ Не удалось сгенерировать сообщение")
                return

            # Второй отвечает с задержкой
            await callback.message.answer(f"💬 **{second.personality['name']}** отвечает...", parse_mode="Markdown")
            await asyncio.sleep(random.uniform(3, 10))

            reply = await second.reply_to_character(
                other_char_id=first.character_id,
                other_name=first.personality["name"],
                other_message=first_msg,
            )
            if reply:
                await callback.message.answer("✅ Переписка началась! Боты будут общаться сами.")
            else:
                await callback.message.answer(f"⚠️ {second.personality['name']} не в настроении 😅")

        # ══════════ CREATE TOPICS IN GROUP ══════════
        @self.dp.callback_query(lambda c: c.data == "create_topics")
        async def cb_create_topics(callback: CallbackQuery):
            await callback.answer()
            chars = await self.db.get_all_characters(active_only=True)
            if not chars:
                await callback.message.answer("❌ Нет персонажей")
                return

            created = 0
            for c in chars:
                p = eval(c.get("personality", "{}"))
                tid = await self.create_topic_in_group(c["id"], p.get("name", c["name"]))
                if tid:
                    await self.db.update_character_topic(c["id"], tid)
                    p["topic_id"] = tid
                    await self.db.update_character(c["id"], personality=str(p))
                    created += 1

            await callback.message.answer(f"✅ Создано топиков в группе: {created}/{len(chars)}")

        # ══════════ COMMAND ALIASES ══════════
        @self.dp.message(Command("sleep"))
        async def cmd_sleep(message: Message):
            name = " ".join(message.text.split()[1:]).lower()
            for cid, bot in self.bots.items():
                if bot.personality["name"].lower() == name:
                    bot.is_sleeping = True
                    await self.db.update_character(cid, is_sleeping=True)
                    await self.db.save_state(cid, "is_sleeping", "true")
                    await message.answer(f"😴 {bot.personality['name']} уснул")
                    return
            await message.answer(f"❌ '{name}' не найден")

        @self.dp.message(Command("wake"))
        async def cmd_wake(message: Message):
            name = " ".join(message.text.split()[1:]).lower()
            for cid, bot in self.bots.items():
                if bot.personality["name"].lower() == name:
                    bot.is_sleeping = False
                    bot.mood = "energetic"
                    await self.db.update_character(cid, is_sleeping=False, mood="energetic")
                    await self.db.save_state(cid, "is_sleeping", "false")
                    await message.answer(f"☀️ {bot.personality['name']} проснулся!")
                    return
            await message.answer(f"❌ '{name}' не найден")

        @self.dp.message(Command("mood"))
        async def cmd_mood(message: Message):
            name = " ".join(message.text.split()[1:]).lower()
            emojis = {"happy":"😊","neutral":"😐","grumpy":"😠","sleepy":"😴","energetic":"⚡"}
            for cid, bot in self.bots.items():
                if bot.personality["name"].lower() == name:
                    await message.answer(f"{emojis.get(bot.mood,'❓')} {bot.personality['name']}: {bot.mood}")
                    return
            await message.answer(f"❌ '{name}' не найден")

        # ══════════ HANDLE HUMAN MESSAGES (в группе) ══════════
        @self.dp.message()
        async def handle_human_messages(message: Message):
            if message.from_user.is_bot or not message.text:
                return
            for bot in self.bots.values():
                if bot.is_sleeping:
                    continue
                if random.random() < float(self.config.get("DEFAULT_REPLY_CHANCE", 0.7)):
                    await bot.reply_to_message(message, sender_name=message.from_user.first_name)

    # ──────────────────────── TOPIC CREATION ────────────────────────
    async def create_topic_in_group(self, char_id: int, name: str) -> Optional[int]:
        """Создаёт топик В ГРУППЕ (chat_id=GROUP_ID), НЕ в личке!"""
        try:
            topic = await self.master_bot.create_forum_topic(
                chat_id=self.group_id,  # ✅ ГРУППА
                name=f"💬 {name}",
                icon_color=random.choice([0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B0, 0xFB6F5F]),
            )
            await self.db.add_topic(
                topic_id=topic.message_thread_id,
                topic_name=name,
                created_by_character_id=char_id,
            )
            logger.info("📂 Topic '%s' created in GROUP (thread_id=%d)", name, topic.message_thread_id)
            return topic.message_thread_id
        except Exception as e:
            logger.error("Failed to create topic in GROUP: %s", e)
            return None

    # ──────────────────────── MANAGED BOT API ────────────────────────
    async def _get_managed_bot_token(self, user_id: int, suggested_username: str) -> Optional[dict]:
        """
        Вызывает Telegram Bot API getManagedBotToken после того как пользователь
        подтвердил создание бота через deep link.
        """
        import aiohttp

        url = f"https://api.telegram.org/bot{self.master_token}/getManagedBotToken"
        payload = {"user_id": user_id}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        result = data["result"]
                        return {
                            "token": result.get("token"),
                            "username": result.get("username"),
                            "id": result.get("id"),
                        }
                    else:
                        logger.error("getManagedBotToken failed: %s", data)
                        return None
        except Exception as e:
            logger.error("HTTP error getting managed bot token: %s", e)
            return None
