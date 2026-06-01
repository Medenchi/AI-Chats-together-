# 🔬 FreeModel.dev — Полный список моделей

> Данные получены напрямую из `GET https://api.freemodel.dev/v1/models` + документация

## Доступные модели (4 шт.)

| # | Model ID | AKA | Контекст | Назначение |
|---|----------|-----|----------|------------|
| 1 | `gpt-5.5` | FRE-5.5 | **1,000,000** токенов | 🏆 Флагман. Сложный reasoning, agentic workflows, coding |
| 2 | `gpt-5.4` | FRE-5.4 | **1,000,000** токенов | General purpose, fallback с gpt-5.5 |
| 3 | `gpt-5.4-mini` | — | **200,000** токенов | ⚡ Быстрые/лёгкие задачи, subagents |
| 4 | `gpt-5.3-codex` | — | **200,000** токенов | 💻 Кодинг-специалист, agentic coding |

## Характеристики

### gpt-5.5 (FRE-5.5) — Флагман
- **Контекст:** 1M токенов (вход: 922K, выход: 128K)
- **Reasoning effort:** `low` / `medium` (default) / `high` / `xhigh`
- **Обучен:** до декабря 2025
- **Фичи:** Structured outputs, function calling, parallel tools, computer use
- **Для чего:** Сложные проекты, multi-step agentic задачи, research

### gpt-5.4 (FRE-5.4) — Предыдущий флагман
- **Контекст:** 1M токенов
- **Reasoning effort:** `low` / `medium` / `high`
- **Фичи:** Те же что gpt-5.5, чуть слабее в reasoning
- **Для чего:** Когда gpt-5.5 недоступен, general purpose

### gpt-5.4-mini — Облегчённая
- **Контекст:** 200K токенов
- **Быстрее и дешевле** gpt-5.4
- **Для чего:** Лёгкие задачи, subagents, интерактивные правки

### gpt-5.3-codex — Кодинг-специалист
- **Контекст:** 200K токенов
- **Заточен под код:** генерация, рефакторинг, отладка
- **Быстрее gpt-5.4** на ~25%
- **Для чего:** Code generation, code review, agentic coding

## Что НЕ работает на FreeModel.dev

Эти модели **недоступны** (чужие провайдеры):
- ❌ `gpt-4o`, `gpt-4o-mini`
- ❌ `claude-sonnet-4`, `claude-opus-4`
- ❌ `deepseek-chat`, `deepseek-v3`
- ❌ `gemini-2.5-flash`, `gemini-2.5-pro`
- ❌ `grok-3`

FreeModel.dev — это прокси к **OpenAI API**, только gpt-5.x модели.

## Ссылки

- Сайт: https://freemodel.dev
- Дашборд: https://freemodel.dev/dashboard  
- API base: `https://api.freemodel.dev/v1`
- OpenAI-совместимый API (drop-in замена)

---

*Обновлено: 2026-06-01*
