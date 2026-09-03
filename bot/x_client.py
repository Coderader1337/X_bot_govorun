"""X API: load tweets and post mentions."""

from __future__ import annotations

import time
from typing import Any

import requests
from requests_oauthlib import OAuth1Session

from bot.config import Config

API_BASES = ("https://api.x.com/2", "https://api.twitter.com/2")
POST_URLS = ("https://api.x.com/2/tweets", "https://api.twitter.com/2/tweets")


def oauth_session(config: Config) -> OAuth1Session:
    return OAuth1Session(
        config.x_consumer_key,
        client_secret=config.x_consumer_secret,
        resource_owner_key=config.x_access_token,
        resource_owner_secret=config.x_access_token_secret,
    )


def _bearer_get(
    path: str,
    bearer_token: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    last_error: str | None = None
    for base in API_BASES:
        url = f"{base}{path}"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {bearer_token}"},
                params=params,
                timeout=(15, 90),
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            time.sleep(max(reset - int(time.time()), 15))
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {bearer_token}"},
                params=params,
                timeout=(15, 90),
            )
        if not resp.ok:
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            continue
        return resp.json()
    raise RuntimeError(f"GET {path} failed: {last_error}")


def fetch_tweet(
    config: Config,
    tweet_id: str,
    *,
    extra_fields: str = "author_id,created_at,text,conversation_id",
) -> dict[str, Any] | None:
    """Загружает твит по ID (Bearer)."""
    params = {
        "tweet.fields": extra_fields,
        "expansions": "author_id",
        "user.fields": "username",
    }
    data = _bearer_get(f"/tweets/{tweet_id}", config.x_bearer_token, params=params)
    if not data or "data" not in data:
        return None
    tweet = data["data"]
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    author = users.get(tweet.get("author_id"), {})
    tweet["author_username"] = author.get("username", "")
    return tweet


def post_mention(
    oauth: OAuth1Session,
    text: str,
    *,
    media_ids: list[str] | None = None,
) -> str:
    body: dict[str, Any] = {"text": text}
    if media_ids:
        body["media"] = {"media_ids": media_ids}
    last_error: str | None = None
    for url in POST_URLS:
        resp = oauth.post(url, json=body, timeout=90)
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            time.sleep(max(reset - int(time.time()), 30))
            resp = oauth.post(url, json=body, timeout=90)
        if resp.ok:
            posted_id = resp.json().get("data", {}).get("id")
            if not posted_id:
                raise RuntimeError(f"POST ok but no id: {resp.text[:300]}")
            return str(posted_id)
        last_error = f"{url}: HTTP {resp.status_code} — {resp.text[:500]}"
    raise RuntimeError(last_error or "POST /2/tweets failed")
