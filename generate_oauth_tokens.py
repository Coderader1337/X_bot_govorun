#!/usr/bin/env python3
"""
Одноразовый OAuth 1.0a PIN-flow для получения X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET.

Запуск:
  python generate_oauth_tokens.py

Добавьте выданные токены в .env — они нужны для постинга через X API.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session

REQUEST_TOKEN_URL = "https://api.twitter.com/oauth/request_token"
AUTHORIZE_URL = "https://api.twitter.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://api.twitter.com/oauth/access_token"


def main() -> None:
    load_dotenv()
    consumer_key = os.getenv("X_CONSUMER_KEY", "").strip()
    consumer_secret = os.getenv("X_SECRET_KEY", "").strip()
    if not consumer_key or not consumer_secret:
        raise SystemExit("Нужны X_CONSUMER_KEY и X_SECRET_KEY в .env")

    oauth = OAuth1Session(consumer_key, client_secret=consumer_secret, callback_uri="oob")
    tokens = oauth.fetch_request_token(REQUEST_TOKEN_URL)
    auth_url = oauth.authorization_url(AUTHORIZE_URL)

    print("1. Откройте в браузере:")
    print(auth_url)
    print()
    print("   Нажмите 'Authorize app' — X покажет 7-значный PIN на странице.")
    print()
    pin = input("2. Введите PIN: ").strip()

    oauth = OAuth1Session(
        consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=tokens["oauth_token"],
        resource_owner_secret=tokens["oauth_token_secret"],
        verifier=pin,
    )
    access = oauth.fetch_access_token(ACCESS_TOKEN_URL)

    print()
    print("Добавьте в .env:")
    print(f"X_ACCESS_TOKEN={access['oauth_token']}")
    print(f"X_ACCESS_TOKEN_SECRET={access['oauth_token_secret']}")


if __name__ == "__main__":
    main()
