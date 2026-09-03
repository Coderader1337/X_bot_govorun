# MoveToRussia X-бот (поиск лидов)

Python-бот, который ищет в X (Twitter) людей из недружественных России стран с личным интересом к переезду/визиту и отвечает коротким outreach-постом с видео. Это **не n8n**: в проде крутится Docker-контейнер на VPS.

Документация собрана под передачу заказчику. Начните с этой страницы, затем откройте нужный раздел.

| # | Тема | Файл |
|---|---|---|
| 2 | Поднять проект с нуля | [docs/01-setup-from-scratch.md](docs/01-setup-from-scratch.md) |
| 3 | Пайплайн данных | [docs/02-pipeline.md](docs/02-pipeline.md) |
| 4 | Архитектура кода | [docs/03-architecture.md](docs/03-architecture.md) |
| 5 | База данных | [docs/04-database.md](docs/04-database.md) |
| 6 | Ручной деплой на VPS | [docs/05-vps-deploy.md](docs/05-vps-deploy.md) |
| 7 | Что требует разработчика | [docs/06-developer-maintenance.md](docs/06-developer-maintenance.md) |
| 8–9 | Ключи и независимый доступ | [docs/07-keys-and-access.md](docs/07-keys-and-access.md) |
| — | Риски передачи, gitleaks, блокеры | [docs/HANDOVER.md](docs/HANDOVER.md) |

Устаревшие одноразовые скрипты и выгрузки лежат в [`archive/`](archive/README.md).

---

## Что считается «рабочим ботом» в проде

На VPS (`207.244.254.188`, каталог `/opt/movetorussia/twitter_agent`) сейчас работает **один** контейнер:

| Контейнер | Команда | Статус на 31.08.2026 |
|---|---|---|
| `movetorussia_reply_bot` | `python -u run_reply_bot.py --tweet-id 2079647800636428422` | **Up**, без рестартов с 30.07.2026 |
| `movetorussia_twitter_bot` | `python -u run_bot.py` | **Exited (137)** с 08.07.2026 — не запускать без задачи |

Reply-бот отвечает на **прямые ответы** под конкретным parent-твитом @Rusia_HD (`2079647800636428422`). Search-бот (`run_bot.py`) ищет лиды по всей ленте X — это запасной режим, в проде выключен.

Снимок очереди на 31.08.2026 (БД `data/reply_bot_2079647800636428422.db`):

- posted **6327**, rejected **3279**, failed **158**, duplicate_user **21**, blocked **4**, queued **0**
- последний успешный пост: **29.08.2026 00:33 UTC**
- с 27.08.2026 X API отвечает `402 credits depleted` — без пополнения кредитов постинг не возобновится
- бот жив, крутит пустые циклы (~7 мин), discovery помечен `done`

**Этот VPS общий.** Рядом крутятся RAG-боты, Qdrant, мониторинг, почтовый агент. Нельзя отдать машину целиком и нельзя делать `docker compose down` / `docker stop $(docker ps -q)` — это заденет чужие сервисы. Подробности: [docs/05-vps-deploy.md](docs/05-vps-deploy.md) и [docs/HANDOVER.md](docs/HANDOVER.md).

---

## Как устроен поток (коротко)

```
X API search  →  antiblock / geo / DeepSeek  →  SQLite-очередь  →  1 пост за цикл (+ видео)
```

Текст поста зашит в `bot/config.py` (`DEFAULT_REPLY_BODY`): упоминание визы, «Link in Bio», без генерации ответа LLM. DeepSeek **не пишет** твиты, он только отсеивает кандидатов.

Лимит: `DAILY_POST_LIMIT` постов за скользящие 24 часа (на VPS сейчас 200). Интервал reply-бота — случайный вокруг `86400 / DAILY_POST_LIMIT`.

---

## Быстрый локальный запуск (без постинга)

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux
pip install -r requirements.txt
copy .env.example .env         # заполнить ключи
python run_reply_bot.py --tweet-id 2079647800636428422 --once --dry-run
```

Полная инструкция: [docs/01-setup-from-scratch.md](docs/01-setup-from-scratch.md).

---

## Правила, чтобы ничего не сломать

1. Не рестартовать `movetorussia_reply_bot`, пока не нужно сменить `--tweet-id` или образ.
2. Не запускать `docker compose up -d` без имени сервиса — это **поднимет и остановленный search-бот**. Нужен только reply: `docker compose up -d reply-bot`.
3. Не вызывать `scripts/reset_vps_db.py --yes` на живой очереди.
4. `scripts/sync_to_vps.py` по умолчанию **не** перезаписывает SQLite и **не** кладёт `docker-compose.yml` / `Dockerfile`. Смена tweet-id — только правка compose на VPS и rebuild.
5. Пароли и ключи только в `.env`, не в git.
