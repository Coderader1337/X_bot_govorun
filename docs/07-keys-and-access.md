# Ключи и доступы

Значения секретов сюда не пишутся — только `.env` локально и `/opt/movetorussia/twitter_agent/.env` на VPS.

| # | Что | Зачем | Где |
|---|---|---|---|
| 1 | Логин developer.x.com | Кредиты, ротация, права приложения | https://developer.x.com |
| 2 | `X_CONSUMER_KEY` / `X_SECRET_KEY` | OAuth приложения | Portal → App |
| 3 | `X_BEARER_TOKEN` | Search + чтение твита | там же |
| 4 | `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | Постинг | Portal или `generate_oauth_tokens.py` |
| 5 | X-аккаунт постинга | Витрина | x.com, логин+2FA заказчика |
| 6 | Логин platform.deepseek.com | Баланс, новый ключ | https://platform.deepseek.com |
| 7 | `DEEPSEEK_API_KEY` | Квалификация и keywords | кабинет DeepSeek |
| 8 | SSH VPS | Деплой, логи | сейчас root @ `207.244.254.188` |
| 9 | GitHub | Исходники | сейчас `Coderader1337/twitter_agent` |

Шаблон переменных: `.env.example`. Корневой `/opt/movetorussia/.env` — чужие ключи (почта, CRM), к боту не относятся.

xAI/Grok reply-боту **не нужен**.

## Независимость от разработчика

Мало отдать `.env`. Нужны кабинеты заказчика: developer.x.com, аккаунт X, DeepSeek, SSH только к этому боту (или отдельный VPS), git на стороне заказчика.

Пока входы в кабинеты не подтверждены — независимости нет.

Ротация: новый ключ → `.env` на VPS → `docker compose up -d` → отозвать старый. В git-истории уже светились ключи и пароль VPS (см. [HANDOVER.md](HANDOVER.md)).
