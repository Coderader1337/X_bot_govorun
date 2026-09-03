# MoveToRussia reply-bot

Daemon, который ищет **прямые ответы** под выбранным постом в X и отвечает outreach-текстом с видео. DeepSeek только фильтрует кандидатов, текст поста константа в `bot/config.py`.

В Docker Compose один сервис: `reply-bot`. `docker compose up -d` поднимает только его.

| Тема | Файл |
|---|---|
| Поднять с нуля | [docs/01-setup-from-scratch.md](docs/01-setup-from-scratch.md) |
| Пайплайн | [docs/02-pipeline.md](docs/02-pipeline.md) |
| Код | [docs/03-architecture.md](docs/03-architecture.md) |
| SQLite | [docs/04-database.md](docs/04-database.md) |
| Деплой на VPS | [docs/05-vps-deploy.md](docs/05-vps-deploy.md) |
| Поддержка | [docs/06-developer-maintenance.md](docs/06-developer-maintenance.md) |
| Ключи | [docs/07-keys-and-access.md](docs/07-keys-and-access.md) |
| Передача, риски | [docs/HANDOVER.md](docs/HANDOVER.md) |

## Прод

VPS `207.244.254.188`, каталог `/opt/movetorussia/twitter_agent`. Контейнер `movetorussia_reply_bot`. Машина **общая** (рядом RAG, Qdrant, почта) — не делать `docker stop $(docker ps -q)` и не отдавать root целиком.

Текущий parent задаётся в `docker-compose.yml` (`--tweet-id` / `--db-id`). Очередь живёт в `data/reply_bot_<db-id>.db`.

```
X API search/all  →  antiblock → geo → DeepSeek  →  очередь  →  1 пост/цикл + видео
```

## Локально без постинга

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run_reply_bot.py --tweet-id 2090277862335238409 --once --dry-run
```

Docker: `docker compose up -d` (сервис один). Логи: `docker compose logs -f`.

Пароли только в `.env`. На живой очереди не гонять `scripts/reset_vps_db.py --yes`.
