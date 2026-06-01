# AI-Chats-Together v2

Автономные AI-персонажи в Telegram-группе с Managed Bots API 9.6 + ручной ввод токена.

## Как работает

### Способ 1: Managed Bots API (автоматически)
1. Нажми «🎭 Создать персонажа» → бот генерирует характер
2. Нажми кнопку «🔗 Создать бота» → откроется Telegram → подтверди
3. Бот подключается автоматически

### Способ 2: Ручной ввод токена (альтернативный)
1. Нажми «🔑 Ввести токен бота вручную»
2. Открой @BotFather → /newbot → создай бота
3. Скопируй токен и отправь его в чат
4. Готово — персонаж подключён!

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env  # заполни ключи
python main.py
```

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Меню с кнопками |
| `/status` | Статус персонажей |
| `/sleep [имя]` | Уложить спать |
| `/wake [имя]` | Разбудить |
| `/mood [имя]` | Настроение |

## Исправленные баги (v2 fixed)

- ✅ Дублирующийся `/start` handler — объединён в один
- ✅ Managed Bots API flow — правильная последовательность (deep link → managed_bot update → getManagedBotToken)
- ✅ Deep link формат — исправлен на `t.me/newbot/{manager}/{username}`
- ✅ `get_all_characters(active_only=True)` → `active=True`
- ✅ `eval()` заменён на `ast.literal_eval()` + `json.loads()`
- ✅ Ограничение на количество отвечающих ботов (макс. 2)
- ✅ Markdown экранирование (MarkdownV2)
- ✅ Закрытие сессий ботов при остановке
- ✅ Personality хранится как JSON (не Python repr)
- ✅ Добавлен альтернативный метод ввода токена (FSM)
