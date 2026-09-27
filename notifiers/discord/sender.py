"""Envoi Discord par webhook, independant des collectors et de X.

API :
    from notifiers.discord import send
    send("spotify-charts", kind="rank_record", key="rank_record_2026-09-25_global_x",
         posts=[(texte, [image, ...]), ...])

- `kind` -> niveau de priorite via config.json (channels.<salon>.kinds) ;
  `priority=` force un niveau. Kind inconnu = "normal".
- Mentions cumulatives : un post de niveau L mentionne le role de L et ceux
  de tous les niveaux plus bas (un membre avec le role "Tout" recoit tout,
  "Urgent" ne recoit que l'urgent). Mentions seulement sur le 1er message.
- `key` = anti-doublon inter-process (lock atomique sous
  runtime/social/discord/sent/<salon>/). Deja envoye => rien. Echec => lock
  retire pour permettre un retry.
- Style (config.json `embed`, proprietaire 2026-09-26) : chaque post est une
  carte (embed) facon repost de tweet — auteur + logo, texte, image dans la
  carte, pied = icone + label du salon + heure, barre de couleur du salon.
  Les mentions de roles restent dans le texte du message (un embed ne ping pas).
- Ne leve jamais ; renvoie True si envoye (ou deja envoye).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Iterable

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
SENT_DIR = REPO_ROOT / "runtime" / "social" / "discord" / "sent"
SENT_LOG = REPO_ROOT / "runtime" / "social" / "discord" / "sent_log.jsonl"
TIMEOUT_SECONDS = 20
CONTENT_LIMIT = 2000
MAX_FILES = 10
EMBED_DESCRIPTION_LIMIT = 4096
GALLERY_MAX = 4  # Discord groups up to 4 images of embeds sharing the same `url`

_env_loaded = False


def _log(msg: str) -> None:
    print(f"[DISCORD] {msg}", flush=True)


def _load_env() -> None:
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env", override=False)
    except ImportError:
        pass


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def env_suffix(channel: str) -> str:
    return channel.strip().upper().replace("-", "_")


def webhook_url(channel: str) -> str:
    _load_env()
    return os.getenv("DISCORD_WEBHOOK_" + env_suffix(channel), "").strip()


def enabled() -> bool:
    _load_env()
    return os.getenv("DISCORD_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def resolve_level(config: dict, channel: str, kind: str | None, priority: str | None) -> str:
    levels = config["levels"]
    if priority:
        if priority not in levels:
            raise ValueError(f"priorite inconnue {priority!r} (attendu: {levels})")
        return priority
    kinds = config["channels"].get(channel, {}).get("kinds", {})
    level = kinds.get(kind or "", "normal")
    if kind and kind not in kinds:
        _log(f"kind {kind!r} absent de config.json pour #{channel} -> 'normal'")
    return level if level in levels else "normal"


def mention_role_ids(config: dict, channel: str, level: str) -> list[str]:
    levels = config["levels"]
    roles = config["channels"].get(channel, {}).get("roles", {})
    idx = levels.index(level)
    # niveau du post + tous les niveaux moins prioritaires (roles cumulatifs)
    return [str(roles[lv]) for lv in levels[idx:] if roles.get(lv)]


# Aliases so every pipeline can pass its own region code (uk daily uses "uk",
# worldwide uses Spotify's "gb").
_THREAD_ALIASES = {"uk": "gb", "glob": "global"}


def thread_key(thread: str | None) -> str | None:
    if not thread:
        return None
    key = str(thread).strip().lower()
    return _THREAD_ALIASES.get(key, key) or None


def resolve_thread_id(config: dict, channel: str, thread: str | None) -> str | None:
    """Discord thread (fil) of `thread` (region code) in `channel`, from
    config.json `channels.<channel>.threads`. None = main channel (fallback:
    a region without a configured thread still posts, in the channel)."""
    key = thread_key(thread)
    if not key:
        return None
    tid = str((config["channels"].get(channel, {}).get("threads") or {}).get(key) or "").strip()
    return tid or None


API = "https://discord.com/api/v10"


def bot_headers() -> dict | None:
    """Bot API key (DISCORD_BOT_TOKEN, .env). The bot never runs, it is only
    used as an API key (threads, roles). None = no bot configured."""
    _load_env()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    return {"Authorization": f"Bot {token}"} if token else None


def thread_role_ids(config: dict, channel: str, thread: str | None) -> list[str]:
    """Role of the thread's region (country role), mentioned IN ADDITION to
    the importance roles (owner 2026-09-26)."""
    key = thread_key(thread)
    if not key:
        return []
    rid = str((config["channels"].get(channel, {}).get("thread_roles") or {}).get(key) or "").strip()
    return [rid] if rid else []


def ensure_thread_open(thread_id: str | None) -> None:
    """Discord auto-archives a thread after a week without messages: the bot
    re-opens it before posting. No bot / any error = silent no-op."""
    headers = bot_headers()
    if not thread_id or not headers:
        return
    try:
        resp = requests.get(f"{API}/channels/{thread_id}", headers=headers, timeout=TIMEOUT_SECONDS)
        if resp.ok and (resp.json().get("thread_metadata") or {}).get("archived"):
            requests.patch(f"{API}/channels/{thread_id}", headers=headers,
                           json={"archived": False}, timeout=TIMEOUT_SECONDS)
            _log(f"fil {thread_id} desarchive")
    except Exception as exc:
        _log(f"desarchivage fil {thread_id} ignore: {exc}")


def _thread_params(thread_id: str | None) -> dict:
    return {"thread_id": thread_id} if thread_id else {}


def _lock_path(channel: str, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("_")[:180]
    return SENT_DIR / channel / f"{safe}.lock"


# A lock records the send state: "sending <ts>" while posting, "done <ts>"
# once every message went out. A process killed mid-send used to leave the
# lock forever with nothing logged -> every rerun printed "deja envoye" and
# the post never arrived (cards thread 2026-09-25). Now a "sending" lock
# older than LOCK_STALE_SECONDS is taken back and the send RESUMES: each
# message is logged as soon as it is sent, with its (target, index), and
# already-logged messages are skipped - no gap, no duplicate.
LOCK_STALE_SECONDS = 15 * 60


def _write_lock(lock: Path, state: str) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{state} {time.strftime('%Y-%m-%dT%H:%M:%S')}", encoding="utf-8")


def _lock_abandoned(lock: Path, channel: str, key: str) -> bool:
    try:
        state = lock.read_text(encoding="utf-8").strip()
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    if state.startswith("done"):
        return False
    if not state.startswith("sending") and _key_entries(channel, key):
        return False  # old-format lock (bare timestamp) with logged messages = sent
    return age > LOCK_STALE_SECONDS


def _claim(lock: Path, channel: str | None = None, key: str | None = None) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if channel and key and _lock_abandoned(lock, channel, key):
            _log(f"#{channel} {key}: envoi interrompu detecte, reprise")
            _write_lock(lock, "sending")
            return True
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"sending {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    return True


def attachment_names(images: tuple[Path, ...]) -> list[str]:
    """Upload filenames, unique and URL-safe (an embed references its image as
    `attachment://<name>`; two cards can share a basename)."""
    names = []
    for i, path in enumerate(images[:MAX_FILES]):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(path).name).strip("_") or "image.png"
        names.append(f"{i}_{safe}")
    return names


def _color(value) -> int | None:
    try:
        return int(str(value).lstrip("#"), 16) if value else None
    except ValueError:
        return None


def embed_style(config: dict) -> dict | None:
    style = config.get("embed") or {}
    return style if style.get("enabled") else None


def build_embeds(config: dict, channel: str, text: str, images: list[str],
                 timestamp: str | None = None) -> list[dict] | None:
    """Post as a card (owner 2026-09-26, "like a tweet repost"): author (name +
    logo) on top, the text, the first image inside the card (up to 4 as a
    gallery), footer = channel icon + label + time, channel color bar.
    `images` = image URLs ("attachment://<upload name>" or an existing CDN URL).
    None = embeds disabled in config.json (plain text messages)."""
    style = embed_style(config)
    if style is None:
        return None
    chan = config["channels"].get(channel, {})
    embed: dict = {"timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    color = _color(chan.get("color"))
    if color is not None:
        embed["color"] = color
    if style.get("author_name"):
        author = {"name": style["author_name"]}
        if style.get("author_icon_url"):
            author["icon_url"] = style["author_icon_url"]
        if style.get("author_url"):
            author["url"] = style["author_url"]
        embed["author"] = author
    if text:
        embed["description"] = text[:EMBED_DESCRIPTION_LIMIT]
    footer = {"text": chan.get("label") or channel}
    if chan.get("avatar_url"):
        footer["icon_url"] = chan["avatar_url"]
    embed["footer"] = footer
    embeds = [embed]
    if images:
        embed["image"] = {"url": images[0]}
    if len(images) > 1:
        # same `url` on every embed = Discord shows the images as one gallery
        gallery = style.get("author_url") or "https://thetsmuseum.app"
        embed["url"] = gallery
        for image in images[1:GALLERY_MAX]:
            embeds.append({"url": gallery, "image": {"url": image}})
    return embeds


def render_payload(config: dict, channel: str, text: str, mention_line: str, images: list[str],
                   *, role_ids: list[str] | None = None, timestamp: str | None = None) -> dict:
    """Message body in the configured style. Role mentions stay in `content`
    (embeds never ping); `role_ids` = roles allowed to ping (None = no ping)."""
    payload: dict = {"allowed_mentions": {"roles": role_ids} if role_ids else {"parse": []}}
    embeds = build_embeds(config, channel, text, images, timestamp)
    if embeds is None:
        content = f"{text}\n\n{mention_line}" if text and mention_line else (text or mention_line)
        payload["content"] = content[:CONTENT_LIMIT]
    else:
        payload["content"] = mention_line[:CONTENT_LIMIT]
        payload["embeds"] = embeds
    return payload


def _post_message(url: str, payload: dict, images: tuple[Path, ...], thread_id: str | None = None,
                  names: list[str] | None = None) -> str | None:
    """Envoie un message ; renvoie son ID Discord (None si echec)."""
    names = names or attachment_names(images)
    for attempt in (1, 2, 3):
        handles = []
        try:
            files = {}
            for i, path in enumerate(images[:MAX_FILES]):
                fh = open(path, "rb")
                handles.append(fh)
                files[f"files[{i}]"] = (names[i], fh)
            resp = requests.post(
                url,
                params={"wait": "true", **_thread_params(thread_id)},
                data={"payload_json": json.dumps(payload)},
                files=files or None,
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            _log(f"erreur reseau (essai {attempt}): {exc}")
            time.sleep(3)
            continue
        finally:
            for fh in handles:
                fh.close()
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get("retry_after", 2))
            except Exception:
                wait = 2.0
            time.sleep(min(wait, 30.0))
            continue
        if resp.ok:
            try:
                return str(resp.json()["id"])
            except Exception:
                return "?"
        _log(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    return None


def _record(channel: str, key: str | None, kind: str | None, level: str,
            message_ids: list[str], first_text: str, thread_id: str | None = None,
            index: int | None = None) -> None:
    """Journal des envois (runtime, gitignored) : permet `recent` / `delete`."""
    entry = {
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "channel": channel,
        "key": key,
        "kind": kind,
        "level": level,
        "message_ids": message_ids,
        "text": first_text[:120],
        "thread_id": thread_id,
        "index": index,
    }
    try:
        SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log(f"journal non ecrit (ignore): {exc}")


def read_log() -> list[dict]:
    if not SENT_LOG.exists():
        return []
    out = []
    for line in SENT_LOG.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _key_entries(channel: str, key: str) -> list[dict]:
    """Log entries of `key` after its last deletion (delete_key writes a
    `deleted` marker so a deleted post can be sent again)."""
    entries: list[dict] = []
    for e in read_log():
        if e.get("channel") != channel or e.get("key") != key:
            continue
        if e.get("deleted"):
            entries = []
        else:
            entries.append(e)
    return entries


def _sent_positions(channel: str, key: str) -> set[tuple[str | None, int]]:
    """(thread_id, message index) already sent for `key` (resume support)."""
    return {
        (e.get("thread_id") or None, int(e["index"]))
        for e in _key_entries(channel, key)
        if e.get("index") is not None and e.get("message_ids")
    }


def logged_thread_id(channel: str, message_id: str) -> str | None:
    """Thread of an already sent message (from the send log), None = channel."""
    for e in reversed(read_log()):
        if e.get("channel") == channel and message_id in (e.get("message_ids") or []):
            return e.get("thread_id") or None
    return None


def delete_message(channel: str, message_id: str) -> bool:
    url = webhook_url(channel)
    if not url:
        _log(f"pas de webhook pour #{channel}")
        return False
    resp = requests.delete(f"{url}/messages/{message_id}",
                           params=_thread_params(logged_thread_id(channel, message_id)),
                           timeout=TIMEOUT_SECONDS)
    if resp.status_code in (204, 404):
        return True  # 404 = deja supprime
    _log(f"suppression {message_id} HTTP {resp.status_code}: {resp.text[:200]}")
    return False


def _patch_message(url: str, message_id: str, payload: dict, images: tuple[Path, ...],
                   thread_id: str | None = None, names: list[str] | None = None) -> bool:
    """PATCH d'un message du webhook (texte et/ou pieces jointes)."""
    names = names or attachment_names(images)
    for attempt in (1, 2, 3):
        handles = []
        try:
            files = {}
            for i, path in enumerate(images[:MAX_FILES]):
                fh = open(path, "rb")
                handles.append(fh)
                files[f"files[{i}]"] = (names[i], fh)
            resp = requests.patch(
                f"{url}/messages/{message_id}",
                params=_thread_params(thread_id),
                data={"payload_json": json.dumps(payload)},
                files=files or None,
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            _log(f"erreur reseau edit (essai {attempt}): {exc}")
            time.sleep(3)
            continue
        finally:
            for fh in handles:
                fh.close()
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get("retry_after", 2))
            except Exception:
                wait = 2.0
            time.sleep(min(wait, 30.0))
            continue
        if resp.ok:
            return True
        _log(f"edit {message_id} HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    return False


_MENTION_ONLY_RE = re.compile(r"^(?:\s*<@&\d+>)+\s*$")


def _fetch_message(url: str, message_id: str, thread_id: str | None) -> tuple[int, dict | None]:
    resp = requests.get(f"{url}/messages/{message_id}", params=_thread_params(thread_id),
                        timeout=TIMEOUT_SECONDS)
    return resp.status_code, (resp.json() if resp.ok else None)


def _split_message(config: dict, message: dict) -> tuple[str, str, bool]:
    """(text, role mention line, already in card style) of a sent message.
    Card style: text = embed description, mentions = content. Plain (older
    messages): mention-only lines of the content are the role line."""
    style = embed_style(config) or {}
    content = str(message.get("content") or "")
    embeds = message.get("embeds") or []
    author = (embeds[0].get("author") or {}).get("name") if embeds else None
    if author and author == style.get("author_name", author):
        return str(embeds[0].get("description") or ""), content.strip(), True
    lines = content.splitlines()
    mentions = [line.strip() for line in lines if _MENTION_ONLY_RE.match(line)]
    text = "\n".join(line for line in lines if not _MENTION_ONLY_RE.match(line)).strip()
    return text, " ".join(mentions), False


def _download_embed_images(message: dict) -> tuple[Path, ...]:
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix="discord_embed_"))
    out = []
    for i, embed in enumerate(message.get("embeds") or []):
        url = (embed.get("image") or {}).get("url")
        if not url:
            continue
        resp = requests.get(url, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()  # never rewrite a card with its image silently dropped
        name = re.sub(r"^\d+_", "", url.split("?", 1)[0].rsplit("/", 1)[-1]) or f"image{i}.png"
        path = folder / name
        path.write_bytes(resp.content)
        out.append(path)
    return tuple(out)


def rewrite_message(channel: str, message_id: str, *, text: str | None = None, images=None) -> str:
    """Re-reads a sent message from Discord and rewrites it in the CURRENT style
    (card), without its link lines. `text` replaces the text (None = keep it),
    `images` replaces the images (None = keep them). The role line is kept,
    never re-pinged, and the card keeps the original post time.
    Returns "edited", "unchanged", "missing" or "error". Never raises."""
    try:
        url = webhook_url(channel)
        if not url:
            _log(f"pas de webhook pour #{channel}")
            return "error"
        thread_id = logged_thread_id(channel, message_id)
        status, current = _fetch_message(url, message_id, thread_id)
        if status == 404:
            return "missing"
        if current is None:
            _log(f"lecture {message_id} HTTP {status}")
            return "error"
        config = load_config()
        old_text, mention_line, is_card = _split_message(config, current)
        new_text = strip_links(old_text if text is None else text)
        wants_card = embed_style(config) is not None
        if text is None and images is None and new_text == old_text.strip() and is_card == wants_card:
            return "unchanged"
        paths: tuple[Path, ...] = ()
        names: list[str] = []
        if images is not None:
            normalized = _normalize([("", images)])
            paths = normalized[0][1] if normalized else ()
            if images and not paths:
                _log(f"edit {message_id}: aucune image trouvee, message inchange")
                return "error"  # never drop the images because of a wrong path
            names = attachment_names(paths)
            refs = [f"attachment://{n}" for n in names]
            attachments = [{"id": i, "filename": n} for i, n in enumerate(names)]
        else:
            # kept images. Plain message: its attachments, re-referenced by name.
            # Card: Discord moved them into the embeds (attachments list empty)
            # and can't re-attach them by id; pointing the embed at their CDN URL
            # leaves an unresolved 0x0 image -> download and re-upload them.
            existing = current.get("attachments") or []
            refs = [f"attachment://{a.get('filename', '')}" for a in existing]
            attachments = [{"id": a["id"]} for a in existing]
            if not existing and is_card:
                paths = _download_embed_images(current)
                names = attachment_names(paths)
                refs = [f"attachment://{n}" for n in names]
                attachments = [{"id": i, "filename": n} for i, n in enumerate(names)]
        payload = render_payload(config, channel, new_text, mention_line, refs,
                                 timestamp=current.get("timestamp"))
        payload["attachments"] = attachments
        if not wants_card:
            payload["embeds"] = []
        ok = _patch_message(url, message_id, payload, paths, thread_id, names=names if paths else None)
        return "edited" if ok else "error"
    except Exception as exc:
        _log(f"reecriture {message_id} echec (ignore): {exc}")
        return "error"


def edit_message(channel: str, message_id: str, *, text: str | None = None, images=None) -> bool:
    """Modifie un message deja envoye par le webhook : `text` remplace le texte
    (liens retires comme a l'envoi ; ligne de roles d'origine conservee, SANS
    re-ping) ; `images` remplace les images (None = inchangees)."""
    if text is None and images is None:
        _log("edit: rien a modifier (ni texte ni image)")
        return False
    status = rewrite_message(channel, message_id, text=text, images=images)
    if status in ("edited", "unchanged"):
        _log(f"#{channel} message {message_id} modifie")
        return True
    return False


def strip_links_message(channel: str, message_id: str) -> str:
    """Rewrites an already sent message without its link lines (owner
    2026-09-26), in the current card style. Role line kept, no re-ping,
    images kept. Returns "edited", "unchanged", "missing" or "error"."""
    return rewrite_message(channel, message_id)


def edit_key(channel: str, key: str, *, text: str | None = None, images=None) -> bool:
    """Modifie le PREMIER message envoye sous `key` (celui qui porte la ligne de roles)."""
    ids = [mid for e in _key_entries(channel, key)
           for mid in e.get("message_ids", []) if mid and mid != "?"]
    if not ids:
        _log(f"aucun message journalise pour #{channel} {key}")
        return False
    return edit_message(channel, ids[0], text=text, images=images)


def delete_key(channel: str, key: str) -> bool:
    """Supprime tous les messages envoyes sous `key` et libere son lock."""
    ids = [mid for e in _key_entries(channel, key)
           for mid in e.get("message_ids", []) if mid and mid != "?"]
    if not ids:
        _log(f"aucun message journalise pour #{channel} {key}")
        return False
    ok = all(delete_message(channel, mid) for mid in ids)
    if ok:
        _lock_path(channel, key).unlink(missing_ok=True)
        try:
            with SENT_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"sent_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "channel": channel,
                                     "key": key, "deleted": True, "message_ids": []}) + "\n")
        except Exception as exc:
            _log(f"marqueur de suppression non ecrit (ignore): {exc}")
    return ok


def _normalize(posts: Iterable) -> list[tuple[str, tuple[Path, ...]]]:
    out = []
    for text, images in posts:
        if images is None:
            images = ()
        elif isinstance(images, (str, Path)):
            images = (images,)
        paths = []
        for p in images:
            if not p:
                continue
            p = Path(p)
            if p.exists():
                paths.append(p)
            else:
                _log(f"image introuvable, ignoree: {p}")
        text = str(text or "").strip()
        if text or paths:
            out.append((text, tuple(paths)))
    return out


_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def strip_links(text: str | None) -> str:
    """Discord copy of an X post, without the link parts (owner 2026-09-26):
    every line containing a URL is dropped (e.g. "🔗 See full update here :
    https://thetsmuseum.app/...", "Full history: https://..."), then blank
    lines are collapsed."""
    if not text:
        return ""
    lines = [line for line in str(text).splitlines() if not _URL_RE.search(line)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def send(
    channel: str,
    posts: Iterable,
    *,
    kind: str | None = None,
    key: str | None = None,
    priority: str | None = None,
    thread: str | None = None,
) -> bool:
    """Envoie un ou plusieurs messages (texte, images) dans le salon, ou dans
    le fil `thread` (code region : global, us, gb, fr...) s'il est configure
    (`channels.<salon>.threads` de config.json). Jamais d'exception."""
    lock = None
    try:
        if not enabled():
            return False
        url = webhook_url(channel)
        if not url:
            return False
        config = load_config()
        chan = config["channels"].get(channel, {})
        posts = _normalize(posts)
        if not posts:
            return False
        level = resolve_level(config, channel, kind, priority)
        thread_id = resolve_thread_id(config, channel, thread)

        if key:
            lock = _lock_path(channel, key)
            if not _claim(lock, channel, key):
                _log(f"#{channel} {key}: deja envoye, skip")
                return True
        already = _sent_positions(channel, key) if key else set()

        # Main channel = the "Overall" feed (owner 2026-09-26): EVERY post, as
        # before, mentioning the importance roles + the Overall role. When the
        # region has a thread, the post is ALSO published there, mentioning
        # only that region's role (no double ping of the importance roles).
        main_roles = mention_role_ids(config, channel, level)
        overall = str((chan.get("thread_roles") or {}).get("overall") or "").strip()
        if overall and overall not in main_roles:
            main_roles.append(overall)
        targets: list[tuple[str | None, list[str]]] = [(None, main_roles)]
        if thread_id:
            targets.append((thread_id, thread_role_ids(config, channel, thread)))

        main_ok = False
        all_ok = True
        for target_thread, role_ids in targets:
            if target_thread:
                ensure_thread_open(target_thread)
            mention_line = " ".join(f"<@&{rid}>" for rid in role_ids)
            target_ok = True
            sent_now = 0
            for i, (text, images) in enumerate(posts):
                if (target_thread, i) in already:
                    continue  # resumed send: this message already went out
                text = strip_links(text)
                if not text and not images and not (i == 0 and mention_line):
                    continue  # nothing left once the link lines are removed
                names = attachment_names(images)
                first = i == 0 and bool(mention_line)
                payload = render_payload(config, channel, text, mention_line if first else "",
                                         [f"attachment://{n}" for n in names],
                                         role_ids=role_ids if first else None)
                if not payload.get("content"):
                    payload.pop("content", None)
                if names:
                    payload["attachments"] = [{"id": n, "filename": name} for n, name in enumerate(names)]
                if chan.get("username"):
                    payload["username"] = chan["username"]
                if chan.get("avatar_url"):
                    payload["avatar_url"] = chan["avatar_url"]
                message_id = _post_message(url, payload, images, target_thread, names=names)
                if message_id is None:
                    target_ok = False
                    break
                sent_now += 1
                # logged per message (resume support), lock refreshed
                _record(channel, key, kind, level, [message_id], text, target_thread, index=i)
                if lock is not None:
                    try:
                        os.utime(lock)
                    except OSError:
                        pass
            where = f" (fil {thread_key(thread)})" if target_thread else ""
            if target_ok:
                _log(f"#{channel}{where} [{level}] {kind or '-'}: {sent_now} message(s) envoye(s)")
            else:
                all_ok = False
                _log(f"#{channel}{where} [{level}] {kind or '-'}: echec d'envoi ({sent_now} envoye(s) avant)")
            if target_thread is None:
                main_ok = target_ok
                if not main_ok:
                    break
        if lock is not None:
            if main_ok and all_ok:
                _write_lock(lock, "done")
            else:
                # released: a rerun resumes, skipping the messages already logged
                lock.unlink(missing_ok=True)
        # Success for the caller = the main feed went out.
        return main_ok
    except Exception as exc:
        _log(f"echec (ignore): {exc}")
        if lock is not None:
            try:
                lock.unlink(missing_ok=True)
            except Exception:
                pass
        return False
