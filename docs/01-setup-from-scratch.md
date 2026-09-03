# Поднять проект с нуля

Для новой машины или локальной отладки. На действующем VPS проект уже стоит — не копируйте `docker compose up -d` туда без [05-vps-deploy.md](05-vps-deploy.md).

## Что нужно

- Python 3.12+ или Docker
- Ключи X Developer + DeepSeek (см. [07-keys-and-access.md](07-keys-and-access.md))
- Видео `bot/video/V23_1(2).mp4` (в репозитории)
- ID parent-твита. В compose сейчас: `--tweet-id 2090277862335238409`, очередь в БД `--db-id 2079647800636428422`

Нужен платный X API с `GET /2/tweets/search/all`.

## Окружение

```bash
git clone <url> twitter_agent
cd twitter_agent
python -m venv venv
```

Windows: `venv\Scripts\activate`  
Linux: `source venv/bin/activate`

```bash
pip install -r requirements.txt
```

Для выкладки на VPS с Windows: `pip install paramiko`.

## `.env`

```bash
cp .env.example .env
```

| Переменная | Откуда |
|---|---|
| `X_CONSUMER_KEY` / `X_SECRET_KEY` | Developer Portal → App → Consumer Keys |
| `X_BEARER_TOKEN` | Bearer Token (App-only) |
| `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | User tokens аккаунта, **с которого бот постит** |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com |

Если user-токенов ещё нет:

```bash
python generate_oauth_tokens.py
```

Авторизуйтесь тем аккаунтом, который должен публиковать. В портале: Read and Write, кредиты на Posts + Full-archive search.

## Запуск без постинга

```bash
python run_reply_bot.py --tweet-id 2090277862335238409 --once --dry-run
```

Появится `data/reply_bot_<id>.db` и `logs/reply_bot_<id>.log`. Поста в X не будет.

Продолжить ту же очередь при новом parent:

```bash
python run_reply_bot.py --tweet-id НОВЫЙ --db-id 2079647800636428422 --dry-run --once
```

Daemon:

```bash
python run_reply_bot.py --tweet-id 2090277862335238409 --db-id 2079647800636428422
```

## Docker

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

В compose один сервис. Смена parent — правка `--tweet-id` / `--db-id` в `docker-compose.yml`, затем `docker compose up -d --build`.

## «Бот работает»

В логе цикл раз в несколько минут и одно из: `OK posted`, «очередь пуста», «Discovery отложен».  
`402 credits depleted` — биллинг X, не баг кода.  
`403 not permitted` на видео — нет прав media у приложения.
