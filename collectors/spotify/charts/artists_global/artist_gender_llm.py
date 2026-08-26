from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
_DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
_DEFAULT_LOCAL_MODEL = "llama3.1:8b"
_TIMEOUT_SECONDS = 18
_VALID_GENDERS = {"F", "NF"}
_VALID_TYPES = {"solo", "GROUP"}


def _env(name: str, fallback: str = "") -> str:
    return str(os.getenv(name) or fallback or "").strip()


def _normalize_gender(value: object) -> str:
    text = str(value or "").strip()
    if text in _VALID_GENDERS:
        return text
    lowered = text.lower()
    if lowered in {"male", "man", "m", "group", "band", "duo", "collective", "non female", "non-female", "nf"}:
        return "NF"
    if lowered in {"female", "woman", "f"}:
        return "F"
    return ""


def _normalize_artist_type(value: object, gender: str = "") -> str:
    text = str(value or "").strip()
    if text in _VALID_TYPES:
        return text
    lowered = text.lower()
    if lowered in {"solo", "person", "artist", "individual"}:
        return "solo"
    if lowered in {"group", "band", "duo", "collective", "fictional group", "mixed group"}:
        return "GROUP"
    if gender == "F":
        return "solo"
    return ""


def normalize_artist_classification(gender: object, artist_type: object = "") -> tuple[str, str]:
    legacy_gender = str(gender or "").strip().lower()
    normalized_gender = _normalize_gender(gender)
    normalized_type = _normalize_artist_type(artist_type, normalized_gender)
    if normalized_gender == "F" and not normalized_type:
        normalized_type = "solo"
    if normalized_gender == "NF" and not normalized_type and legacy_gender in {"male", "man", "m"}:
        normalized_type = "solo"
    if normalized_gender == "NF" and not normalized_type and legacy_gender in {"group", "band", "duo", "collective"}:
        normalized_type = "GROUP"
    if normalized_type == "GROUP":
        normalized_gender = "NF"
    return normalized_gender, normalized_type


def _parse_gender_response(text: str) -> tuple[str, str]:
    clean = text.strip()
    if not clean:
        return "", ""
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            return normalize_artist_classification(clean)
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return "", ""
    if isinstance(payload, dict):
        return normalize_artist_classification(payload.get("gender"), payload.get("type"))
    return normalize_artist_classification(payload)


def _build_prompt(artist_name: str) -> str:
    return (
        "Classify this Spotify chart artist for a gender-filtered artist chart.\n"
        "Return JSON only, exactly like {\"gender\":\"F\",\"type\":\"solo\"}.\n"
        "Allowed gender values:\n"
        "- F: solo female artist\n"
        "- NF: not a solo female artist\n"
        "Allowed type values:\n"
        "- solo: one individual artist\n"
        "- GROUP: band, duo, collective, fictional group, or mixed/unknown-members group\n"
        "Do not return male, non-binary, unknown, or any other gender label. Use NF for anything that is not a solo female artist.\n\n"
        f"Artist: {artist_name}"
    )


def _call_local_llm(prompt: str) -> str:
    base_url = _env("ARTIST_GENDER_LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = _env("ARTIST_GENDER_LLM_MODEL", _DEFAULT_LOCAL_MODEL)
    if base_url.endswith("/v1"):
        url = f"{base_url}/chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 40,
        }
        extractor = lambda payload: str((payload.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    else:
        url = f"{base_url}/api/generate"
        body = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }
        extractor = lambda payload: str(payload.get("response") or "")

    req = UrlRequest(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=_TIMEOUT_SECONDS) as res:
        return extractor(json.loads(res.read().decode("utf-8")))


def _call_gemini(prompt: str) -> str:
    api_key = _env("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    model = _env("ARTIST_GENDER_GEMINI_MODEL", _env("STUDIO_LLM_MODEL", _DEFAULT_GEMINI_MODEL))
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 40,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "gender": {"type": "string", "enum": ["F", "NF"]},
                    "type": {"type": "string", "enum": ["solo", "GROUP"]},
                },
                "required": ["gender", "type"],
            },
        },
    }
    url = _GEMINI_ENDPOINT.format(model=quote(model, safe=""), key=quote(api_key, safe=""))
    req = UrlRequest(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=_TIMEOUT_SECONDS) as res:
        payload = json.loads(res.read().decode("utf-8"))
    text = ""
    for candidate in payload.get("candidates") or []:
        for part in ((candidate.get("content") or {}).get("parts") or []):
            if isinstance(part.get("text"), str):
                text += part["text"]
    return text


def classify_artist_gender(artist_name: str) -> tuple[str, str, str]:
    """Return (gender, type, provider). Empty gender means the caller should keep it blank."""
    prompt = _build_prompt(artist_name)
    provider = _env("ARTIST_GENDER_LLM_PROVIDER", "auto").lower()
    chain = ["local", "gemini"] if provider == "auto" else [provider]
    errors: list[str] = []
    for current in chain:
        try:
            if current in {"local", "ollama", "openai-compatible"}:
                gender, artist_type = _parse_gender_response(_call_local_llm(prompt))
                source = "local"
            elif current == "gemini":
                gender, artist_type = _parse_gender_response(_call_gemini(prompt))
                source = "gemini"
            else:
                errors.append(f"{current}: unknown provider")
                continue
        except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError, OSError) as exc:
            errors.append(f"{current}: {exc}")
            continue
        if gender and artist_type:
            return gender, artist_type, source
        errors.append(f"{current}: invalid response")
    if errors:
        print(f"[WARN] Artist gender LLM unavailable for {artist_name}: {' | '.join(errors)}")
    return "", "", ""
