from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from requests import Session

from .config import APPLE_MUSIC_HOME, TOKEN_CACHE_PATH

JWT_RE = re.compile(r"eyJ[A-Za-z0-9._-]{50,}")
INDEX_JS_RE = re.compile(r'(/assets/index[~-][A-Za-z0-9-]+\.js)')
META_PATTERNS = [
    re.compile(r'JWTToken["\s:=]+["\'](eyJ[A-Za-z0-9._-]+)'),
    re.compile(r'["\']token["\']\s*:\s*["\'](eyJ[A-Za-z0-9._-]+)'),
]


def _is_jwt(candidate: str) -> bool:
    return candidate.count(".") == 2



def _extract_token_from_text(text: str) -> Optional[str]:
    for pattern in META_PATTERNS:
        match = pattern.search(text)
        if match and _is_jwt(match.group(1)):
            return match.group(1)
    for candidate in JWT_RE.findall(text):
        if _is_jwt(candidate):
            return candidate
    return None



def _load_cached_token() -> Optional[str]:
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    token = payload.get("token")
    return token if isinstance(token, str) and _is_jwt(token) else None



def _save_cached_token(token: str) -> None:
    payload = {
        "token": token,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def fetch_musickit_token(session: Session, refresh: bool = False) -> Optional[str]:
    if not refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    resp = session.get(APPLE_MUSIC_HOME)
    resp.raise_for_status()
    html = resp.text

    token = _extract_token_from_text(html)
    if token:
        _save_cached_token(token)
        return token

    js_match = INDEX_JS_RE.search(html)
    if js_match:
        js_url = f"https://music.apple.com{js_match.group(1)}"
        js_resp = session.get(js_url)
        js_resp.raise_for_status()
        token = _extract_token_from_text(js_resp.text)
        if token:
            _save_cached_token(token)
            return token

    return None



def build_auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Origin": "https://music.apple.com",
    }
