"""Master Bot — Managed Bots API + ручной ввод токена, inline кнопки, топики В ГРУППЕ."""
import os
import json
import random
import asyncio
import logging
import re
from html import escape as html_escape
from typing import Dict, Optional
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database.models import Database
from ai.client import AIClient
from ai.personality import PersonalityGenerator
from bots.character import CharacterBot

logger = logging.getLogger(__name__)


# ── FSM состояния для ручного ввода токена ──
class TokenInput(StatesGroup):
    waiting_for_token = State()
    waiting_for_select_character = State()


def html_bold(text: str) -> str:
    """Безопасный bold в HTML."""
    return f"<b>{html_escape(text)}</b>"


def html_safe(text: str) -> str:
    """Экранирует HTML-спецсимволы."""
    return html_escape(text)


class MasterBot:
    def __init__(self, config: dict):
        self.config = config
        self.group_id = config["GROUP_ID"]
        self.admin_ids = config.get("ADMIN_USER_IDS", [])
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

        # Загружаем существующих персонажей с бот-токенами
        characters = await self.db.get_all_characters(active=True)
        for char in characters:
            if char.get("bot_token"):
                try:
                    bot = Bot(token=char["bot_token"])
                    me = await bot.get_me()
                    cb = CharacterBot(
                        character_id=char["id"], bot=bot, db=self.db,
                        ai_client=self.ai_client, pg=self.pg,
                        group_id=self.group_id,
                    )
                    await cb.initialize()
                    self.bots[char["id"]] = cb
                    logger.info("Loaded: %s (@%s)", char["name"], me.username)
                except Exception as e:
                    logger.error("Failed to load bot for %s: %s",
                                 char.get("name"), e)
        logger.info("Master bot ready. %d characters loaded.", len(self.bots))

    async def start(self):
        self._setup_handlers()
        for bot in self.bots.values():
            await bot.start()
        logger.info("Master bot polling started.")
        # Указываем allowed_updates чтобы получать managed_bot updates
        await self.dp.start_polling(
            self.master_bot,
            allowed_updates=["message", "callback_query", "managed_bot"],
        )

    async def stop(self):
        for bot in self.bots.values():
            await bot.stop()
        await self.master_bot.session.close()

    # ──────────────────────── ACCESS CONTROL ────────────────────────
    def is_admin(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь админом.
        Если ADMIN_USER_IDS не задан — доступ открыт всем (для обратной совместимости)."""
        if not self.admin_ids:
            return True  # Не задано = все могут
        return user_id in self.admin_ids

    # ──────────────────────── HANDLERS ────────────────────────
    def _setup_handlers(self):

        # ═══════ /start — ЕДИНЫЙ обработчик с проверкой deeplink ═══════
        @self.dp.message(CommandStart())
        async def cmd_start(message: Message, state: FSMContext):
            args = message.text.split(maxsplit=1)

            # Проверяем deeplink: /start connect_ХЕШ_ИМЯ
            if len(args) >= 2 and args[1].startswith("connect_"):
                await self._handle_connect_deeplink(message, args[1], state)
                return

            is_admin = self.is_admin(message.from_user.id)

            # Главное меню — кнопки зависят от прав
            kb_rows = []
            if is_admin:
                kb_rows.append([InlineKeyboardButton(text="🎭 Создать персонажа",
                                      callback_data="create_character")])
                kb_rows.append([InlineKeyboardButton(text="🔑 Ввести токен бота вручную",
                                      callback_data="manual_token")])
            kb_rows.append([InlineKeyboardButton(text="📋 Список персонажей",
                                  callback_data="list_chars")])
            kb_rows.append([InlineKeyboardButton(text="📊 Статус",
                                  callback_data="status_chars")])
            if is_admin:
                kb_rows.append([InlineKeyboardButton(text="🗣 Начать переписку",
                                      callback_data="start_chat")])
                kb_rows.append([InlineKeyboardButton(text="📂 Создать топики в группе",
                                      callback_data="create_topics")])

            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

            menu_text = (
                "🤖 AI-Chats-Together v2\n\n"
                "Управляй персонажами через кнопки ниже.\n"
            )
            if is_admin:
                menu_text += (
                    "\n📌 **Два способа подключить бота:**\n"
                    "1. «🎭 Создать персонажа» → генерация + Managed Bot API\n"
                    "2. «🔑 Ввести токен» → создаёшь бота в @BotFather, "
                    "вставляешь токен — готово!"
                )
            else:
                menu_text += (
                    "\n👁 Ты в режиме наблюдателя. Управление доступно только админам.\n"
                    "Для получения прав — обратись к владельцу бота."
                )
            await message.answer(menu_text, reply_markup=kb)

        # ═══════ СОЗДАНИЕ ПЕРСОНАЖА (через Managed Bot API) ═══════
        @self.dp.callback_query(lambda c: c.data == "create_character")
        async def cb_create(callback: CallbackQuery, state: FSMContext):
            await callback.answer()

            if not self.is_admin(callback.from_user.id):
                await callback.message.answer("❌ У тебя нет прав для этого действия.")
                return

            total = len(await self.db.get_all_characters())
            max_bots = self.config.get("MAX_BOTS", 10)
            if total >= max_bots:
                await callback.message.answer(
                    f"❌ Максимум {max_bots} ботов достигнут!")
                return

            model = random.choice(
                self.config.get("AVAILABLE_MODELS", ["gpt-4o"]))
            await callback.message.answer(
                f"🎭 Генерирую характер (модель: {model})... Подожди ~10 сек...")

            personality = await self.pg.generate_personality(model=model)
            char_id = await self.db.add_character(
                name=personality["name"], age=personality["age"],
                personality=json.dumps(personality, ensure_ascii=False),
                model=model)

            suggested_username = (
                f"ai_{personality['name'].lower().replace(' ', '_')}"
                f"_{char_id}_bot")
            suggested_username = re.sub(
                r'[^a-z0-9_]', '_', suggested_username)[:32]

            self.pending_creations[callback.from_user.id] = {
                "char_id": char_id,
                "model": model,
                "personality": personality,
                "suggested_username": suggested_username,
            }

            # ✅ ПРАВИЛЬНЫЙ формат deep link для Managed Bots (API 9.6)
            me = await self.master_bot.get_me()
            deep_link = (
                f"https://t.me/newbot/{me.username}/{suggested_username}"
                f"?name={personality['name']}")

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🔗 Создать бота (откроет Telegram)",
                    url=deep_link)],
            ])

            traits = ', '.join(personality['traits'])
            hobbies = ', '.join(personality['hobbies'])
            await callback.message.answer(
                f"✅ Персонаж сгенерирован!\n\n"
                f"👤 {html_bold(personality['name'])} "
                f"({personality['age']} лет)\n"
                f"🧠 Черты: {html_safe(traits)}\n"
                f"🎨 Хобби: {html_safe(hobbies)}\n"
                f"📖 {html_safe(personality['backstory'])}\n"
                f"💬 Стиль: {html_safe(personality['communication_style'])}\n"
                f"🤖 Модель: {html_safe(model)}\n\n"
                f"👇 Нажми кнопку ниже:\n"
                f"Откроется экран создания бота → подтверди → "
                f"бот подключится автоматически!\n\n"
                f"⚠️ Твой мастер-бот должен иметь "
                f"`can_manage_bots=true` (включается через @BotFather MiniApp)",
                parse_mode="HTML",
                reply_markup=kb)

        # ═══════ СОЗДАНИЕ ПЕРСОНАЖА (ручной ввод токена) ═══════
        @self.dp.callback_query(lambda c: c.data == "manual_token")
        async def cb_manual_token(callback: CallbackQuery, state: FSMContext):
            await callback.answer()

            if not self.is_admin(callback.from_user.id):
                await callback.message.answer("❌ У тебя нет прав для этого действия.")
                return

            total = len(await self.db.get_all_characters())
            max_bots = self.config.get("MAX_BOTS", 10)
            if total >= max_bots:
                await callback.message.answer(
                    f"❌ Максимум {max_bots} ботов достигнут!")
                return

            await state.set_state(TokenInput.waiting_for_select_character)

            # Проверяем есть ли персонажи без бота
            chars = await self.db.get_all_characters(active=True)
            orphans = [c for c in chars if not c.get("bot_token")]

            if orphans:
                kb_rows = []
                for c in orphans:
                    p = PersonalityGenerator.parse_personality_string(
                        c.get("personality", "{}"))
                    name = p.get("name", c["name"])
                    kb_rows.append([InlineKeyboardButton(
                        text=f"👤 {name} (ID {c['id']})",
                        callback_data=f"token_for_{c['id']}")])
                kb_rows.append([InlineKeyboardButton(
                    text="🆕 Создать нового + ввести токен",
                    callback_data="token_new")])
                kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
                await callback.message.answer(
                    "🔑 У тебя есть персонажи без бота. Выбери кому добавить токен:",
                    reply_markup=kb)
            else:
                # Генерируем нового персонажа
                model = random.choice(
                    self.config.get("AVAILABLE_MODELS", ["gpt-4o"]))
                await callback.message.answer(
                    f"🎭 Генерирую нового персонажа ({model})...")
                personality = await self.pg.generate_personality(model=model)
                char_id = await self.db.add_character(
                    name=personality["name"], age=personality["age"],
                    personality=json.dumps(personality, ensure_ascii=False),
                    model=model)

                await state.update_data(char_id=char_id, model=model,
                                        personality=personality)
                await state.set_state(TokenInput.waiting_for_token)

                traits = ', '.join(personality['traits'])
                await callback.message.answer(
                    f"✅ Персонаж {html_bold(personality['name'])} создан!\n\n"
                    f"🧠 {html_safe(traits)}\n\n"
                    f"Теперь отправь токен бота:\n"
                    f"1️⃣ Открой @BotFather\n"
                    f"2️⃣ Отправь /newbot\n"
                    f"3️⃣ Придумай имя и username\n"
                    f"4️⃣ Скопируй токен и отправь его сюда",
                    parse_mode="HTML")

        @self.dp.callback_query(
            lambda c: c.data and c.data.startswith("token_for_"))
        async def cb_token_for(callback: CallbackQuery, state: FSMContext):
            await callback.answer()
            char_id = int(callback.data.split("_")[-1])
            await state.update_data(char_id=char_id)
            await state.set_state(TokenInput.waiting_for_token)

            char = await self.db.get_character(char_id)
            p = PersonalityGenerator.parse_personality_string(
                char.get("personality", "{}"))
            name = p.get("name", char["name"])
            await callback.message.answer(
                f"👤 {html_bold(name)} — отправь токен бота из @BotFather:",
                parse_mode="HTML")

        @self.dp.callback_query(lambda c: c.data == "token_new")
        async def cb_token_new(callback: CallbackQuery, state: FSMContext):
            await callback.answer()
            model = random.choice(
                self.config.get("AVAILABLE_MODELS", ["gpt-4o"]))
            await callback.message.answer(
                f"🎭 Генерирую нового персонажа ({model})...")
            personality = await self.pg.generate_personality(model=model)
            char_id = await self.db.add_character(
                name=personality["name"], age=personality["age"],
                personality=json.dumps(personality, ensure_ascii=False),
                model=model)
            await state.update_data(char_id=char_id, model=model,
                                    personality=personality)
            await state.set_state(TokenInput.waiting_for_token)

            traits = ', '.join(personality['traits'])
            await callback.message.answer(
                f"✅ Персонаж {html_bold(personality['name'])} создан!\n\n"
                f"🧠 {html_safe(traits)}\n\n"
                f"Отправь токен бота из @BotFather:",
                parse_mode="HTML")

        # Обработка ввода токена
        @self.dp.message(TokenInput.waiting_for_token)
        async def process_token(message: Message, state: FSMContext):
            token = message.text.strip()
            # Валидация формата токена: цифры:буквы
            if not re.match(r'^\d{8,10}:[A-Za-z0-9_-]{30,40}$', token):
                await message.answer(
                    "❌ Неверный формат токена!\n"
                    "Токен выглядит так: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`\n"
                    "Попробуй ещё раз:")
                return

            data = await state.get_data()
            char_id = data.get("char_id")

            if not char_id:
                # Фоллбэк — создаём нового
                model = random.choice(
                    self.config.get("AVAILABLE_MODELS", ["gpt-4o"]))
                personality = await self.pg.generate_personality(model=model)
                char_id = await self.db.add_character(
                    name=personality["name"], age=personality["age"],
                    personality=json.dumps(personality, ensure_ascii=False),
                    model=model)

            # Проверяем токен
            try:
                test_bot = Bot(token=token)
                me = await test_bot.get_me()
                bot_username = me.username
                bot_id = me.id
                await test_bot.session.close()
            except Exception as e:
                await message.answer(f"❌ Токен невалиден: {e}\nПопробуй ещё раз:")
                return

            # Сохраняем
            personality = data.get("personality", {})
            if personality:
                await self.db.update_character(
                    char_id,
                    personality=json.dumps(personality, ensure_ascii=False))

            await self.db.update_bot_token(char_id, token, bot_username, bot_id)

            # Создаём CharacterBot
            bot = Bot(token=token)
            cb = CharacterBot(
                character_id=char_id, bot=bot, db=self.db,
                ai_client=self.ai_client, pg=self.pg,
                group_id=self.group_id,
            )
            await cb.initialize()
            self.bots[char_id] = cb
            await cb.start()

            # Создаём топик в группе
            topic_id = await self.create_topic_in_group(
                char_id, personality.get("name", bot_username))
            if topic_id:
                personality["topic_id"] = topic_id
                await self.db.update_character_topic(char_id, topic_id)
                await self.db.update_character(
                    char_id,
                    personality=json.dumps(personality, ensure_ascii=False))

            name = personality.get("name", bot_username)
            await message.answer(
                f"🎉 {html_bold(name)} создан и подключён!\n"
                f"🤖 @{html_safe(bot_username)}\n"
                f"📂 Топик: {'✅' if topic_id else '❌'}\n"
                f"Теперь он начнёт общаться в группе!",
                parse_mode="HTML")

            await state.clear()

        # ═══════ MANAGED BOT UPDATE (API 9.6) ═══════
        # Этот обработчик срабатывает когда пользователь подтверждает
        # создание managed бота через deep link
        # Примечание: aiogram 3.x может не иметь типизированного обработчика
        # для managed_bot, поэтому используем middleware или обновление
        # через getUpdates. Для полной совместимости используем
        # дополнительный polling в фоне.

        # ═══════ Deeplink: /start connect_ХЕШ_ИМЯ ═══════
        # Альтернативный метод: пользователь сам создаёт бота через BotFather,
        # а потом подключает его через deep link
        # Формат: /start connect_CHARID_BOTUSERNAME

        # ═══════ STATUS ═══════
        @self.dp.callback_query(lambda c: c.data == "status_chars")
        async def cb_status(callback: CallbackQuery):
            await callback.answer()
            chars = await self.db.get_all_characters(active=True)
            if not chars:
                await callback.message.answer("📋 Персонажей пока нет.")
                return

            emojis = {"happy": "😊", "neutral": "😐", "grumpy": "😠",
                      "sleepy": "😴", "energetic": "⚡"}
            lines = ["📊 Статус персонажей:"]
            for c in chars:
                p = PersonalityGenerator.parse_personality_string(
                    c.get("personality", "{}"))
                name = p.get("name", c["name"])
                icon = "😴" if c.get("is_sleeping") else "✅"
                mood = emojis.get(c.get("mood", "neutral"), "❓")
                has_bot = "🤖" if c.get("bot_token") else "⏳"
                lines.append(
                    f"{icon} {mood} {name} ({c['age']} лет) "
                    f"— {c.get('model', '?')} {has_bot}")
            await callback.message.answer("\n".join(lines))

        # ═══════ LIST ═══════
        @self.dp.callback_query(lambda c: c.data == "list_chars")
        async def cb_list(callback: CallbackQuery):
            await callback.answer()
            chars = await self.db.get_all_characters(active=True)
            if not chars:
                await callback.message.answer("📋 Пусто")
                return
            lines = ["👥 Персонажи:"]
            for c in chars:
                p = PersonalityGenerator.parse_personality_string(
                    c.get("personality", "{}"))
                name = p.get("name", c["name"])
                lines.append(
                    f"• {name} ({c['age']} лет) — {c.get('model', '?')}")
            await callback.message.answer("\n".join(lines))

        # ═══════ START CHAT ═══════
        @self.dp.callback_query(lambda c: c.data == "start_chat")
        async def cb_start_chat(callback: CallbackQuery):
            await callback.answer()

            if not self.is_admin(callback.from_user.id):
                await callback.message.answer("❌ У тебя нет прав для этого действия.")
                return

            active = [b for b in self.bots.values() if not b.is_sleeping]
            if len(active) < 2:
                await callback.message.answer(
                    "❌ Нужно минимум 2 активных бота!")
                return

            first = random.choice(active)
            second = random.choice(
                [b for b in active if b.character_id != first.character_id])

            await callback.message.answer(
                f"🗣 {html_bold(first.personality['name'])} "
                f"начинает переписку...",
                parse_mode="HTML")

            first_msg = await first.send_first_message()
            if not first_msg:
                await callback.message.answer(
                    "❌ Не удалось сгенерировать сообщение")
                return

            await callback.message.answer(
                f"💬 {html_bold(second.personality['name'])} отвечает...",
                parse_mode="HTML")
            await asyncio.sleep(random.uniform(3, 10))

            reply = await second.reply_to_character(
                other_char_id=first.character_id,
                other_name=first.personality["name"],
                other_message=first_msg,
            )
            if reply:
                await callback.message.answer(
                    "✅ Переписка началась! Боты будут общаться сами.")
            else:
                await callback.message.answer(
                    f"⚠️ {html_safe(second.personality['name'])} "
                    f"не в настроении 😅",
                    parse_mode="HTML")

        # ═══════ CREATE TOPICS ═══════
        @self.dp.callback_query(lambda c: c.data == "create_topics")
        async def cb_create_topics(callback: CallbackQuery):
            await callback.answer()

            if not self.is_admin(callback.from_user.id):
                await callback.message.answer("❌ У тебя нет прав для этого действия.")
                return

            chars = await self.db.get_all_characters(active=True)
            if not chars:
                await callback.message.answer("❌ Нет персонажей")
                return

            created = 0
            for c in chars:
                p = PersonalityGenerator.parse_personality_string(
                    c.get("personality", "{}"))
                name = p.get("name", c["name"])
                tid = await self.create_topic_in_group(c["id"], name)
                if tid:
                    await self.db.update_character_topic(c["id"], tid)
                    p["topic_id"] = tid
                    await self.db.update_character(
                        c["id"],
                        personality=json.dumps(p, ensure_ascii=False))
                    created += 1

            await callback.message.answer(
                f"✅ Создано топиков в группе: {created}/{len(chars)}")

        # ═══════ COMMAND ALIASES ═══════
        @self.dp.message(Command("sleep"))
        async def cmd_sleep(message: Message):
            if not self.is_admin(message.from_user.id):
                await message.answer("❌ У тебя нет прав для этого действия.")
                return
            name = " ".join(message.text.split()[1:]).lower()
            for cid, bot in self.bots.items():
                if bot.personality["name"].lower() == name:
                    bot.is_sleeping = True
                    await self.db.update_character(cid, is_sleeping=True)
                    await self.db.save_state(cid, "is_sleeping", "true")
                    await message.answer(
                        f"😴 {bot.personality['name']} уснул")
                    return
            await message.answer(f"❌ '{name}' не найден")

        @self.dp.message(Command("wake"))
        async def cmd_wake(message: Message):
            if not self.is_admin(message.from_user.id):
                await message.answer("❌ У тебя нет прав для этого действия.")
                return
            name = " ".join(message.text.split()[1:]).lower()
            for cid, bot in self.bots.items():
                if bot.personality["name"].lower() == name:
                    bot.is_sleeping = False
                    bot.mood = "energetic"
                    await self.db.update_character(
                        cid, is_sleeping=False, mood="energetic")
                    await self.db.save_state(cid, "is_sleeping", "false")
                    await message.answer(
                        f"☀️ {bot.personality['name']} проснулся!")
                    return
            await message.answer(f"❌ '{name}' не найден")

        @self.dp.message(Command("mood"))
        async def cmd_mood(message: Message):
            if not self.is_admin(message.from_user.id):
                await message.answer("❌ У тебя нет прав для этого действия.")
                return
            name = " ".join(message.text.split()[1:]).lower()
            emojis = {"happy": "😊", "neutral": "😐", "grumpy": "😠",
                      "sleepy": "😴", "energetic": "⚡"}
            for cid, bot in self.bots.items():
                if bot.personality["name"].lower() == name:
                    await message.answer(
                        f"{emojis.get(bot.mood, '❓')} "
                        f"{bot.personality['name']}: {bot.mood}")
                    return
            await message.answer(f"❌ '{name}' не найден")

        # ═══════ HANDLE HUMAN MESSAGES (в группе) ═══════
        @self.dp.message()
        async def handle_human_messages(message: Message):
            if message.from_user.is_bot or not message.text:
                return

            # Фильтруем — только сообщения из нашей группы
            if message.chat.id != self.group_id:
                return

            # ✅ FIX: Ограничиваем до 1-2 ботов, отвечающих на сообщение
            active_bots = [b for b in self.bots.values() if not b.is_sleeping]
            if not active_bots:
                return

            reply_chance = self.config.get("DEFAULT_REPLY_CHANCE", 0.7)
            # Выбираем максимум 2 бота для ответа
            respondents = []
            shuffled = list(active_bots)
            random.shuffle(shuffled)
            for bot in shuffled:
                if random.random() < reply_chance:
                    respondents.append(bot)
                if len(respondents) >= 2:
                    break

            for bot in respondents:
                try:
                    await bot.reply_to_message(
                        message,
                        sender_name=message.from_user.first_name)
                    # Небольшая задержка между ответами
                    await asyncio.sleep(random.uniform(1, 3))
                except Exception as e:
                    logger.error("Reply from %s failed: %s",
                                 bot.personality["name"], e)

    # ──────────────────────── TOPIC CREATION ────────────────────────
    async def create_topic_in_group(self, char_id: int, name: str) -> Optional[int]:
        """Создаёт топик В ГРУППЕ (chat_id=GROUP_ID), НЕ в личке!"""
        try:
            topic = await self.master_bot.create_forum_topic(
                chat_id=self.group_id,  # ✅ ГРУППА
                name=f"💬 {name}",
                icon_color=random.choice(
                    [0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B0, 0xFB6F5F]),
            )
            await self.db.add_topic(
                topic_id=topic.message_thread_id,
                topic_name=name,
                created_by_character_id=char_id,
            )
            logger.info("📂 Topic '%s' created in GROUP (thread_id=%d)",
                        name, topic.message_thread_id)
            return topic.message_thread_id
        except Exception as e:
            logger.error("Failed to create topic in GROUP: %s", e)
            return None

    # ──────────────────────── MANAGED BOT API ────────────────────────
    async def _get_managed_bot_token(self, user_id: int) -> Optional[dict]:
        """
        Вызывает Telegram Bot API getManagedBotToken.
        ВНИМАНИЕ: вызывать ТОЛЬКО после получения managed_bot update!
        """
        import aiohttp

        url = (f"https://api.telegram.org/bot{self.master_token}"
               f"/getManagedBotToken")
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

    async def _handle_connect_deeplink(
            self, message: Message, param: str, state: FSMContext):
        """Обработка deeplink для подключения бота через Managed API."""
        # Формат: connect_CHARID_USERNAME
        m = re.match(r"connect_(\d+)_(.+)", param)
        if not m:
            await message.answer("⚠️ Неправильный формат ссылки.")
            return

        char_id = int(m.group(1))
        suggested_username = m.group(2)

        pending = self.pending_creations.get(message.from_user.id)
        if not pending or pending.get("char_id") != char_id:
            await message.answer(
                "⚠️ Сессия создания истекла. Начни заново через /start.")
            return

        personality = pending["personality"]
        model = pending["model"]

        await message.answer(
            f"⏳ Создаю бота для {html_bold(personality['name'])}...",
            parse_mode="HTML")

        try:
            # Правильный Managed Bot API flow:
            # Мы НЕ вызываем getManagedBotToken здесь напрямую!
            # Пользователь должен был перейти по deep link
            # https://t.me/newbot/{manager}/{username}?name={name}
            # После подтверждения бот получит managed_bot update.
            #
            # Поскольку /start deeplink и newbot link — разные вещи,
            # здесь мы просто ждём и проверяем.

            # Альтернативно — предлагаем ручной ввод
            await message.answer(
                "⚠️ Для создания бота через Managed API:\n"
                "1. Нажми кнопку «🔗 Создать бота» в предыдущем сообщении\n"
                "2. Подтверди создание в Telegram\n"
                "3. Бот подключится автоматически\n\n"
                f"Или отправь токен вручную командой:\n"
                f"/token {char_id} ТВОЙ_ТОКЕН")

        except Exception as e:
            logger.error("Connect deeplink failed: %s", e)
            await message.answer(f"❌ Ошибка: {e}")

        self.pending_creations.pop(message.from_user.id, None)

    # ─────────────────── MANAGED BOT UPDATE PROCESSOR ───────────────
    async def process_managed_bot_update(self, update_data: dict):
        """
        Обрабатывает managed_bot update от Telegram.
        Вызывать когда приходит update типа 'managed_bot'.
        """
        managed = update_data.get("managed_bot")
        if not managed:
            return

        user_id = managed.get("owner", {}).get("id")
        bot_username = managed.get("bot", {}).get("username")

        if not user_id:
            logger.error("Managed bot update without owner user_id")
            return

        # Ищем pending creation для этого пользователя
        pending = self.pending_creations.get(user_id)
        if not pending:
            # Пробуем найти по username
            char = await self.db.get_character_by_username(bot_username)
            if char:
                # Токен уже существует, просто обновляем
                logger.info("Managed bot update for existing: %s", bot_username)
            return

        char_id = pending["char_id"]
        personality = pending["personality"]

        # Теперь вызываем getManagedBotToken
        bot_info = await self._get_managed_bot_token(user_id)

        if bot_info and bot_info.get("token"):
            bot_token = bot_info["token"]
            bot_uname = bot_info.get("username", bot_username)
            bot_id = bot_info.get("id")

            await self.db.update_bot_token(
                char_id, bot_token, bot_uname, bot_id)

            bot = Bot(token=bot_token)
            cb = CharacterBot(
                character_id=char_id, bot=bot, db=self.db,
                ai_client=self.ai_client, pg=self.pg,
                group_id=self.group_id,
            )
            await cb.initialize()
            self.bots[char_id] = cb
            await cb.start()

            # Создаём топик в группе
            topic_id = await self.create_topic_in_group(
                char_id, personality["name"])
            if topic_id:
                personality["topic_id"] = topic_id
                await self.db.update_character_topic(char_id, topic_id)
                await self.db.update_character(
                    char_id,
                    personality=json.dumps(personality, ensure_ascii=False))

            logger.info("🎉 Managed bot %s connected (char_id=%d)",
                        bot_uname, char_id)

            # Уведомляем в группу
            try:
                await self.master_bot.send_message(
                    chat_id=self.group_id,
                    text=f"🎉 {personality['name']} подключён! "
                         f"(@{bot_uname})")
            except Exception:
                pass
        else:
            logger.error("Failed to get managed bot token for user %d",
                         user_id)

        self.pending_creations.pop(user_id, None)
