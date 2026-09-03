# Ручной деплой на VPS (SSH)

Стейджинга и CI нет. Выкладка — SSH + Docker Compose. Каталог на сервере:

```
/opt/movetorussia/twitter_agent
```

Хост (август 2026): `207.244.254.188`, hostname `vmi3380733` (Contabo-класс VPS), вход `root`. Актуальный пароль хранится у команды в `.env` как `VPS_PASS`, **не** в git.

## Критично: машина общая

`/opt/movetorussia/` — не только этот бот. На 31.08.2026 рядом:

| Что | Как запущено | Трогать? |
|---|---|---|
| `movetorussia_reply_bot` | docker compose этого репозитория | да, это наш прод |
| `movetorussia_twitter_bot` | тот же compose, **Exited** | не поднимать без задачи |
| `movetorussia_qdrant`, `movetorussia_qdrant_exp` | чужой compose | нет |
| `movetorussia-rag-bot.service`, `…-dev.service` | systemd | нет |
| `movetorussia_mail_agent_api` | чужой compose, exited | нет |
| cron `monitoring/collect_stats.sh` | crontab root каждые 5 мин | нет |
| `/opt/movetorussia/.env` | ключи почты, CRM, Voyage, DeepSeek | нет |

Запрещено на этой машине без явной задачи по **другим** проектам:

```bash
docker stop $(docker ps -q)
docker compose down          # если запускаете не из twitter_agent — уточните cwd
reboot
passwd                       # пока не согласован handover SSH
```

`docker compose down` **из** `/opt/movetorussia/twitter_agent` остановит только два контейнера этого compose (reply + twitter). RAG и Qdrant выживут. Но reply-бот при этом остановится — на проде этого обычно не нужно.

## Первичная установка (новый VPS, не текущий)

Предполагается Ubuntu, Docker + Compose plugin, Python в образ не нужен.

```bash
ssh root@<VPS_IP>
mkdir -p /opt/movetorussia/twitter_agent
```

С рабочей машины (после `pip install paramiko`):

```bash
python scripts/sync_to_vps.py
```

Либо rsync/scp **без** `.env` / `venv` / `.git`:

```bash
scp -r bot run_reply_bot.py run_bot.py reset_discovery.py \
    Dockerfile docker-compose.yml requirements.txt \
    root@<VPS_IP>:/opt/movetorussia/twitter_agent/
```

На сервере создать `.env` из `.env.example` (ключи не коммитятся). Проверить видео:

```bash
ls -l /opt/movetorussia/twitter_agent/bot/video/V23_1\(2\).mp4
```

Сборка и запуск **только reply-бота**:

```bash
cd /opt/movetorussia/twitter_agent
docker compose build
docker compose up -d reply-bot
docker compose ps
docker compose logs -f reply-bot
```

Не используйте `docker compose up -d` без имени сервиса: в yaml есть `twitter-bot`, и он стартует.

Автозапуск: в compose стоит `restart: unless-stopped`. Docker сам должен быть enabled:

```bash
systemctl is-enabled docker
```

## Что сейчас на живом сервере (не ломать)

Проверено 31.08.2026, только чтение.

```text
/opt/movetorussia/twitter_agent
  .env                 ключи (права 644)
  docker-compose.yml   tweet-id 2079647800636428422
  Dockerfile
  bot/                 код; video/V23_1(2).mp4
  data/                sqlite, том в контейнер
  logs/                reply_bot_2079647800636428422.log пишется
```

Контейнер `movetorussia_reply_bot`:

- image `twitter_agent-reply-bot`
- created/started **2026-07-30 07:49 UTC**, RestartCount **0**
- command `python -u run_reply_bot.py --tweet-id 2079647800636428422`

Код на диске и код в образе могут чуть расходиться (на диске есть `bot/prefilter.py`, в git его нет). Менять файлы на хосте **без rebuild не меняет** то, что крутится внутри контейнера, кроме томов `data/` и `logs/`.

`scripts/sync_to_vps.py` **намеренно не копирует** `Dockerfile` и `docker-compose.yml`. Смена tweet-id или зависимостей = правка на сервере + rebuild.

## Обновление кода (без смены tweet-id)

1. Залить файлы (`sync_to_vps.py` или scp).
2. На VPS:

```bash
cd /opt/movetorussia/twitter_agent
docker compose build reply-bot
docker compose up -d reply-bot
```

`up -d reply-bot` пересоздаст контейнер, если изменился image/command. Очередь в `data/` сохранится (том). Краткий простой — секунды. **Не делайте это «на всякий случай»**: текущий контейнер живёт без рестартов с 30 июля.

Если меняли только python-файлы и хотите избежать recreate — это всё равно требует rebuild образа: код копируется в image в `Dockerfile`, не монтируется.

## Смена parent-твита

На VPS, в `docker-compose.yml`:

```yaml
command:
  - python
  - -u
  - run_reply_bot.py
  - --tweet-id
  - "НОВЫЙ_ID"
```

```bash
cd /opt/movetorussia/twitter_agent
docker compose up -d --build reply-bot
```

Появится `data/reply_bot_НОВЫЙ_ID.db`. Старая БД останется. Параллельно два reply-бота из этого compose **не** предусмотрены.

## Логи и здоровье

```bash
# живой лог контейнера (ротация json-file 10m × 5)
docker logs -f --tail 100 movetorussia_reply_bot

# файл, который пишут и код, и том
tail -f /opt/movetorussia/twitter_agent/logs/reply_bot_2079647800636428422.log

docker inspect movetorussia_reply_bot --format '{{.State.Status}} {{.RestartCount}} {{.State.StartedAt}}'
```

Норма: цикл раз в ~4–12 минут, `OK posted` либо «очередь пуста».  
`402 credits depleted` — биллинг X, не рестарт.  
`403 not permitted` — права приложения/медиа.

## Остановка и старт (только этот бот)

```bash
cd /opt/movetorussia/twitter_agent
docker compose stop reply-bot
docker compose start reply-bot
```

Search-бот, если вдруг запущен по ошибке:

```bash
docker compose stop twitter-bot
```

## Бэкап перед любой заменой БД

```bash
cp /opt/movetorussia/twitter_agent/data/reply_bot_2079647800636428422.db \
   /opt/movetorussia/twitter_agent/data/reply_bot_2079647800636428422.db.bak-$(date +%F)
```

## Firewall / порты

Снаружи слушает только SSH `:22`. Qdrant на `127.0.0.1:6333–6336`. У twitter-бота нет HTTP. Открывать порты не нужно.
