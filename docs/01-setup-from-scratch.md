# Поднять проект с нуля

Инструкция для человека, у которого есть исходники и доступы из [07-keys-and-access.md](07-keys-and-access.md). Цель — locally или на новом VPS получить **работающий reply-бот**. Search-бот (`run_bot.py`) в проде не используется; шаги для него в конце, отдельно.

На действующем VPS проект уже развёрнут. Эту инструкцию выполняйте только на новой машине или для локальной отладки. Не копируйте команды `docker compose up -d` на живой сервер без чтения [05-vps-deploy.md](05-vps-deploy.md).

## 0. Что должно быть на руках

- Репозиторий (git clone).
- Python 3.12+ (локально) или Docker (как в проде).
- Ключи X Developer + DeepSeek (обязательно). xAI/Grok — только если нужен fallback search-бота.
- Видеофайл `bot/video/V23_1(2).mp4` (уже в репозитории, ~13 МБ).
- ID parent-твита, под которым отвечаем. Прод: `2079647800636428422` (@Rusia_HD).

Без платного X API с доступом к `GET /2/tweets/search/all` reply-бот **не находит** ответы (recent search для этой задачи не используется).

## 1. Код и окружение

```bash
git clone <url> twitter_agent
cd twitter_agent
python -m venv venv
```

Windows:

```bat
venv\Scripts\activate
pip install -r requirements.txt
```

Linux/VPS:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

Для выкладки с Windows-машины на VPS дополнительно: `pip install paramiko python-dotenv`.

## 2. Ключи: `.env`

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

Заполните минимум:

| Переменная | Откуда |
|---|---|
| `X_CONSUMER_KEY` / `X_SECRET_KEY` | X Developer Portal → приложение → Consumer Keys |
| `X_BEARER_TOKEN` | то же приложение, Bearer Token (App-only) |
| `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | User tokens того X-аккаунта, **с которого бот постит** |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com |

Если User tokens ещё не выпускались:

```bash
python generate_oauth_tokens.py
```

Скрипт откроет URL авторизации, попросит PIN, напечатает `X_ACCESS_TOKEN` и `X_ACCESS_TOKEN_SECRET`. Их нужно вписать в `.env`. Авторизуйтесь **тем аккаунтом, который должен публиковать** (не личным аккаунтом разработчика).

Проверьте, что в портале у приложения есть права **Read and Write**, а у проекта есть кредиты на Posts + Full-archive search.

## 3. Первый запуск без постинга

Reply-бот (то, что в проде):

```bash
python run_reply_bot.py --tweet-id 2079647800636428422 --once --dry-run
```

Что должно произойти:

1. Загрузка parent-твита через X API.
2. DeepSeek сгенерирует keyword-query (кэш в SQLite).
3. Одна страница поиска ответов.
4. Фильтры antiblock → geo → DeepSeek.
5. Поста в X **не будет**.

БД появится в `data/reply_bot_2079647800636428422.db`, лог — в `logs/reply_bot_2079647800636428422.log`.

Собрать очередь, потом крутить постинг:

```bash
python run_reply_bot.py --tweet-id 2079647800636428422 --discover-first --dry-run
python run_reply_bot.py --tweet-id 2079647800636428422
```

Без `--dry-run` бот начнёт публиковать по 1 посту за цикл.

## 4. Docker (как на VPS)

```bash
docker compose build
docker compose up -d reply-bot
docker compose logs -f reply-bot
```

Именно `reply-bot`, не `up -d` целиком: сервис `twitter-bot` в compose тоже описан и при полном `up` стартует search-бот.

Сменить parent-твит: правка `--tweet-id` в `docker-compose.yml`, затем:

```bash
docker compose up -d --build reply-bot
```

Появится новая БД `data/reply_bot_<новый_id>.db`. Старая не удаляется.

Тома: `./data` и `./logs` монтируются в контейнер. `.env` читается через `env_file`.

## 5. Что считать «бот работает»

В логе каждые несколько минут:

```
=== Reply цикл #N | queue=… | discovery=… | posts_24h=…/200 ===
```

И либо `OK posted https://x.com/i/status/…`, либо `Discovery отложен — в очереди N лидов`, либо `Нечего постить — очередь пуста`.

Если сразу `HTTP 402 credits depleted` — ключи верные, но на проекте X кончились кредиты. Это не баг кода: пополняют в Developer Portal.

Если `Your account is not permitted to access this feature` (403) на посте с видео — у приложения/аккаунта нет media-upload. Бот умеет ретраить, но без прав на медиа посты с видео не уйдут.

## 6. Search-бот (не прод, запасной режим)

Ищет лиды не под одним постом, а по keyword-паттернам за 30 дней:

```bash
python run_bot.py --once --dry-run
python run_bot.py
```

В Docker это сервис `twitter-bot` / контейнер `movetorussia_twitter_bot`. На текущем VPS он **остановлен с 8 июля 2026**. Не поднимайте его на той же машине, пока не решите, что два бота должны постить с одного аккаунта параллельно (они делят лимиты и могут дублировать outreach).

## 7. Чек-лист «с нуля до прода»

1. Ключи X + DeepSeek в `.env`, OAuth от нужного аккаунта.
2. Кредиты X API пополнены.
3. `--tweet-id` в compose = актуальный вирусный пост.
4. `docker compose up -d reply-bot`.
5. В логе есть discovery и/или `OK posted`.
6. В X виден пост от рабочего аккаунта с видео.
7. SQLite растёт в `data/reply_bot_<id>.db`.
8. `restart: unless-stopped` — контейнер поднимается после ребута VPS.
