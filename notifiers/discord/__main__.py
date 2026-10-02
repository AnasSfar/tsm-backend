"""CLI : python -m notifiers.discord <commande> (depuis la racine du repo).

  show                                  resume salons / webhooks / roles / kinds
  send --channel C (--kind K | --priority P) [--key X] [--text T] [--image PNG ...]
  setup-roles [--channel C ...]         cree les roles manquants (bot token requis)
  set-role --channel C --level L --id ID  enregistre un role cree a la main
  recent [--channel C] [-n 20]          derniers envois journalises (avec ID)
  delete --channel C (--key K | --message-id ID ...)  supprime des messages envoyes
  edit --channel C (--key K | --message-id ID) [--text T] [--image PNG ...]
                                        modifie un message envoye (texte et/ou image ;
                                        --image remplace les images, omis = inchangees)
  restyle [--channel C]                 remet les messages deja envoyes au style actuel
                                        (carte/embed, sans liens) ; alias : strip-links
  setup-community                       onboarding profil + roles + #rules/#roles (community.json)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

from .sender import (
    API,
    bot_headers,
    CONFIG_PATH,
    _load_env,
    delete_key,
    delete_message,
    edit_key,
    edit_message,
    load_config,
    strip_links_message,
    thread_key,
    read_log,
    send,
    webhook_url,
)



def _save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_show(_args) -> int:
    config = load_config()
    for name, chan in config["channels"].items():
        hook = "webhook OK" if webhook_url(name) else "PAS de webhook"
        roles = ", ".join(f"{lv}={'OK' if chan.get('roles', {}).get(lv) else '-'}" for lv in config["levels"])
        print(f"#{name:16} {hook:15} roles[{roles}]")
        for kind, level in chan.get("kinds", {}).items():
            print(f"    {kind:24} {level}")
    return 0


def cmd_send(args) -> int:
    ok = send(
        args.channel,
        [(args.text, args.image)],
        kind=args.kind,
        key=args.key,
        priority=args.priority,
        thread=args.thread,
    )
    return 0 if ok else 1


def cmd_set_thread(args) -> int:
    """Enregistre le fil Discord d'une region (code : global, us, gb, fr...)."""
    config = load_config()
    key = thread_key(args.thread)
    config["channels"][args.channel].setdefault("threads", {})[key] = args.id
    _save_config(config)
    print(f"#{args.channel} fil {key} -> {args.id}")
    return 0


def role_name(config: dict, channel: str, level: str) -> str:
    return f"{config['channels'][channel]['label']} · {config['level_labels'][level]}"


def cmd_setup_roles(args) -> int:
    _load_env()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not token or not guild:
        print("DISCORD_BOT_TOKEN et DISCORD_GUILD_ID requis dans .env", file=sys.stderr)
        return 2
    headers = {"Authorization": f"Bot {token}"}
    resp = requests.get(f"{API}/guilds/{guild}/roles", headers=headers, timeout=20)
    resp.raise_for_status()
    existing = {r["name"]: r["id"] for r in resp.json()}

    config = load_config()
    channels = args.channel or list(config["channels"])
    for channel in channels:
        chan = config["channels"][channel]
        if chan.get("level_roles") is False:
            print(f"- #{channel} : pas de roles d'importance (level_roles=false), ignore")
            continue
        chan.setdefault("roles", {})
        for level in config["levels"]:
            name = role_name(config, channel, level)
            if name in existing:
                role_id = existing[name]
                print(f"= {name} (existe deja: {role_id})")
            else:
                r = requests.post(
                    f"{API}/guilds/{guild}/roles",
                    headers=headers,
                    json={"name": name, "mentionable": False},
                    timeout=20,
                )
                r.raise_for_status()
                role_id = r.json()["id"]
                print(f"+ {name} (cree: {role_id})")
            chan["roles"][level] = role_id
    _save_config(config)
    print(f"IDs enregistres dans {CONFIG_PATH}")
    return 0


def _bot_or_exit():
    headers = bot_headers()
    guild = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not headers or not guild:
        print("DISCORD_BOT_TOKEN et DISCORD_GUILD_ID requis dans .env", file=sys.stderr)
        return None, None
    return headers, guild


def cmd_setup_threads(args) -> int:
    """Cree (bot) les fils de `thread_specs` manquants dans le salon et
    enregistre leurs ID dans `threads`. Idempotent : un fil deja enregistre
    et existant, ou un fil existant du meme nom (actif ou archive), est reutilise."""
    _load_env()
    headers, guild = _bot_or_exit()
    if not headers:
        return 2
    config = load_config()
    chan = config["channels"][args.channel]
    specs = chan.get("thread_specs") or {}
    if not specs:
        print(f"aucun thread_specs pour #{args.channel} dans config.json")
        return 1
    channel_id = requests.get(webhook_url(args.channel), timeout=20).json().get("channel_id")
    existing: dict[str, str] = {}
    active = requests.get(f"{API}/guilds/{guild}/threads/active", headers=headers, timeout=20).json()
    for t in active.get("threads", []):
        if t.get("parent_id") == channel_id:
            existing[t["name"]] = t["id"]
    archived = requests.get(f"{API}/channels/{channel_id}/threads/archived/public", headers=headers, timeout=20)
    if archived.ok:
        for t in archived.json().get("threads", []):
            existing.setdefault(t["name"], t["id"])
    threads = chan.setdefault("threads", {})
    for key, spec in specs.items():
        name = spec["name"]
        current = threads.get(key)
        if current and requests.get(f"{API}/channels/{current}", headers=headers, timeout=20).ok:
            print(f"= {key}: {name} (deja enregistre: {current})")
            continue
        if name in existing:
            threads[key] = existing[name]
            print(f"= {key}: {name} (existe deja: {existing[name]})")
            continue
        r = requests.post(
            f"{API}/channels/{channel_id}/threads",
            headers=headers,
            json={"name": name, "type": 11, "auto_archive_duration": 10080},
            timeout=20,
        )
        if not r.ok:
            print(f"! {key}: {name} echec HTTP {r.status_code}: {r.text[:200]}")
            continue
        threads[key] = r.json()["id"]
        print(f"+ {key}: {name} (cree: {threads[key]})")
    _save_config(config)
    print(f"IDs enregistres dans {CONFIG_PATH}")
    return 0


def cmd_setup_country_roles(args) -> int:
    """Cree (bot) un role par fil (`thread_specs.<key>.role`) et enregistre
    son ID dans `thread_roles` ; il est mentionne dans les posts de ce fil,
    en plus des roles d'importance. Idempotent (par nom)."""
    _load_env()
    headers, guild = _bot_or_exit()
    if not headers:
        return 2
    config = load_config()
    chan = config["channels"][args.channel]
    specs = chan.get("thread_specs") or {}
    existing = {r["name"]: r["id"] for r in
                requests.get(f"{API}/guilds/{guild}/roles", headers=headers, timeout=20).json()}
    roles = chan.setdefault("thread_roles", {})
    wanted = list(specs.items())
    if chan.get("overall_role"):
        # "Overall" = main-channel feed, every post (owner 2026-09-26)
        wanted.append(("overall", {"role": chan["overall_role"]}))
    for key, spec in wanted:
        name = spec.get("role")
        if not name:
            continue
        if name in existing:
            roles[key] = existing[name]
            print(f"= {name} (existe deja: {existing[name]})")
            continue
        r = requests.post(f"{API}/guilds/{guild}/roles", headers=headers,
                          json={"name": name, "mentionable": False}, timeout=20)
        if not r.ok:
            print(f"! {name} echec HTTP {r.status_code}: {r.text[:200]}")
            continue
        roles[key] = r.json()["id"]
        print(f"+ {name} (cree: {roles[key]})")
    _save_config(config)
    print(f"IDs enregistres dans {CONFIG_PATH}")
    return 0


def _new_snowflake(offset: int = 0) -> str:
    """Placeholder ID for a new onboarding prompt/option (Discord expects a
    unique snowflake-shaped ID from the client when creating them)."""
    import time as _time
    return str(((int(_time.time() * 1000) - 1420070400000) << 22) + offset)


def cmd_setup_onboarding(args) -> int:
    """Ajoute/met a jour (bot) dans l'Onboarding du serveur une question « pays »
    a choix multiples : une option par role de fil (`thread_roles`). Les autres
    questions et reglages de l'Onboarding sont renvoyes tels quels. Idempotent
    (la question est retrouvee par son titre). Exige « Gerer le serveur »."""
    _load_env()
    headers, guild = _bot_or_exit()
    if not headers:
        return 2
    config = load_config()
    chan = config["channels"][args.channel]
    specs = chan.get("thread_specs") or {}
    roles = chan.get("thread_roles") or {}
    title = args.title or f"{chan['label']} — which charts?"
    current = requests.get(f"{API}/guilds/{guild}/onboarding", headers=headers, timeout=20)
    current.raise_for_status()
    onboarding = current.json()
    prompts = onboarding.get("prompts", [])
    prompt = next((p for p in prompts if p.get("title") == title), None)
    old_options = {tuple(o.get("role_ids") or []): o for o in (prompt or {}).get("options", [])}
    options = []
    ordered = list(specs.items())
    if chan.get("overall_role"):
        ordered.insert(0, ("overall", {"role": chan["overall_role"], "name": f"#{args.channel} : tous les posts"}))
    for i, (key, spec) in enumerate(ordered):
        rid = roles.get(key)
        if not rid:
            continue
        label = spec.get("role", key).split(" - ", 1)[-1]  # "🇺🇸 US"
        emoji, _, name = label.partition(" ")
        old = old_options.get((str(rid),))
        options.append({
            "id": old["id"] if old else _new_snowflake(10 + i),
            "title": name or label,
            "description": spec.get("name", ""),
            "emoji_name": emoji if name else None,
            "role_ids": [str(rid)],
            "channel_ids": [],
        })
    new_prompt = {
        "id": prompt["id"] if prompt else _new_snowflake(1),
        "type": 0,
        "title": title,
        "single_select": False,
        "required": False,
        "in_onboarding": False,
        "options": options,
    }
    prompts = [p for p in prompts if p.get("title") != title] + [new_prompt]
    # Every question of this channel (title starting with its label, e.g.
    # "Spotify Charts Notifications?") stays OUT of the arrival flow: nobody
    # gets a notification by default, they are picked in « Salons & roles »
    # (owner 2026-09-26). Option titles are trimmed ("Normal " -> "Normal").
    for prompt_obj in prompts:
        if str(prompt_obj.get("title", "")).startswith(chan["label"]):
            prompt_obj["in_onboarding"] = False
    _fix_option_emojis(prompts)
    for prompt_obj in prompts:
        for option in prompt_obj.get("options", []):
            for restore_title, restore_emoji in (args.restore_emoji or []):
                if option.get("title", "").strip() == restore_title and not option.get("emoji_name"):
                    option["emoji_name"] = restore_emoji
    body = {
        "prompts": prompts,
        "default_channel_ids": onboarding.get("default_channel_ids", []),
        "enabled": onboarding.get("enabled", False),
        "mode": onboarding.get("mode", 0),
    }
    resp = requests.put(f"{API}/guilds/{guild}/onboarding", headers=headers, json=body, timeout=20)
    if not resp.ok:
        print(f"echec HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code == 403:
            print("-> le bot a besoin de la permission « Gerer le serveur » (MANAGE_GUILD)")
        return 1
    print(f"Onboarding: question « {title} » avec {len(options)} choix "
          f"({', '.join(o['title'] for o in options)})")
    return 0


COMMUNITY_PATH = Path(__file__).resolve().parent / "community.json"
# Onboarding option limits, measured on the API 2026-09-26 (TOO_MANY_ONBOARDING_OPTIONS)
MULTIPLE_CHOICE_MAX = 12
DROPDOWN_MAX = 50

# permission bits (https://discord.com/developers/docs/topics/permissions)
P_VIEW, P_SEND, P_EMBED, P_ATTACH, P_HISTORY, P_SEND_THREADS = 1 << 10, 1 << 11, 1 << 14, 1 << 15, 1 << 16, 1 << 38


def _flag(code: str) -> str:
    return "".join(chr(0x1F1E6 + ord(c) - ord("a")) for c in code.lower())


def _api(method: str, path: str, headers: dict, **kwargs) -> requests.Response:
    """Discord REST call with rate-limit retry (role creation is throttled)."""
    for _ in range(5):
        resp = requests.request(method, f"{API}{path}", headers=headers, timeout=20, **kwargs)
        if resp.status_code != 429:
            return resp
        try:
            wait = float(resp.json().get("retry_after", 2))
        except Exception:
            wait = 2.0
        time.sleep(min(wait, 60.0) + 0.5)
    return resp


def _fix_option_emojis(prompts: list[dict]) -> None:
    """GET returns option emojis as `emoji: {id, name, animated}` but PUT expects
    `emoji_id` / `emoji_name` / `emoji_animated`: re-sending the GET shape
    silently DROPS every emoji (incident 2026-09-26). Titles are trimmed."""
    for prompt in prompts:
        for option in prompt.get("options", []):
            option["title"] = str(option.get("title", "")).strip()
            emoji = option.pop("emoji", None) or {}
            if emoji.get("name") and not option.get("emoji_name"):
                option["emoji_name"] = emoji["name"]
            if emoji.get("id") and not option.get("emoji_id"):
                option["emoji_id"] = emoji["id"]
                option["emoji_animated"] = bool(emoji.get("animated"))


def _profile_options(spec: dict) -> list[dict]:
    """Options of a profile question from community.json (countries: flag +
    name, sorted as listed, then « Other »)."""
    if "countries" not in spec:
        return list(spec.get("options", []))
    options = [{"key": code, "title": name, "role": f"{_flag(code)} {name}", "emoji": _flag(code)}
               for code, name in spec["countries"]]
    if spec.get("other"):
        options.append(spec["other"])
    return options


def _upsert_bot_message(channel_id: str, stored_id: str | None, body: dict, headers: dict) -> str | None:
    """Edits the bot message if it still exists, else posts it. Returns its ID."""
    if stored_id:
        resp = _api("PATCH", f"/channels/{channel_id}/messages/{stored_id}", headers, json=body)
        if resp.ok:
            return stored_id
        if resp.status_code != 404:
            print(f"! edition message {stored_id} HTTP {resp.status_code}: {resp.text[:200]}")
            return stored_id
    resp = _api("POST", f"/channels/{channel_id}/messages", headers, json=body)
    if not resp.ok:
        print(f"! envoi message dans {channel_id} HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()["id"]


def cmd_setup_community(args) -> int:
    """Arrival flow of the server (owner 2026-09-26), from community.json:
    1. profile roles (age, country, pronouns, gender) created if missing;
    2. Onboarding = those profile questions ONLY; the notification questions of
       the channels stay but move out of onboarding (page « Salons & roles »),
       so nobody gets a notification role by default; #roles becomes a default
       channel. A country with a Spotify Charts thread also gives its role;
    3. rules screening (native) with the rules - Discord refuses it to bots on
       some servers: then printed, to paste by hand;
    4. #rules / #roles: bot allowed to post, #roles read-only, bot messages
       (rules + « choose your roles » button) posted or edited in place.
    Idempotent: rerun after editing community.json."""
    _load_env()
    headers, guild = _bot_or_exit()
    if not headers:
        return 2
    community = json.loads(COMMUNITY_PATH.read_text(encoding="utf-8"))
    config = load_config()
    customize_url = f"https://discord.com/channels/{guild}/customize-community"

    # 1. profile roles -------------------------------------------------------
    existing = {r["id"]: r["name"] for r in _api("GET", f"/guilds/{guild}/roles", headers).json()}
    by_name = {name: rid for rid, name in existing.items()}
    role_ids = community.setdefault("role_ids", {})
    created = 0
    for spec in community["profile_prompts"]:
        for opt in _profile_options(spec):
            slot = f"{spec['key']}:{opt['key']}"
            rid = role_ids.get(slot)
            if rid in existing:
                if existing[rid] != opt["role"]:
                    _api("PATCH", f"/guilds/{guild}/roles/{rid}", headers, json={"name": opt["role"]})
                continue
            if opt["role"] in by_name:
                role_ids[slot] = by_name[opt["role"]]
                continue
            resp = _api("POST", f"/guilds/{guild}/roles", headers,
                        json={"name": opt["role"], "permissions": "0", "mentionable": False})
            if not resp.ok:
                print(f"! role {opt['role']} HTTP {resp.status_code}: {resp.text[:200]}")
                return 1
            role_ids[slot] = resp.json()["id"]
            created += 1
    # roles of options removed from community.json (only the ones this command manages)
    wanted = {f"{spec['key']}:{opt['key']}" for spec in community["profile_prompts"]
              for opt in _profile_options(spec)}
    for slot in [slot for slot in role_ids if slot not in wanted]:
        rid = role_ids.pop(slot)
        if rid in existing:
            resp = _api("DELETE", f"/guilds/{guild}/roles/{rid}", headers)
            print(f"- role {existing[rid]} supprime (retire de community.json)"
                  if resp.ok else f"! suppression role {existing[rid]} HTTP {resp.status_code}")
    COMMUNITY_PATH.write_text(json.dumps(community, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"roles profil : {created} cree(s), {len(role_ids) - created} deja la")

    # 2. onboarding ----------------------------------------------------------
    current = _api("GET", f"/guilds/{guild}/onboarding", headers)
    current.raise_for_status()
    onboarding = current.json()
    old_prompts = onboarding.get("prompts", [])
    profile_titles = {spec["title"] for spec in community["profile_prompts"]}
    prompts = []
    for i, spec in enumerate(community["profile_prompts"]):
        old = next((p for p in old_prompts if p.get("title") == spec["title"]), None)
        old_ids = {tuple(sorted(o.get("role_ids") or [])): o["id"] for o in (old or {}).get("options", [])}
        thread_map = spec.get("thread_roles") or {}
        thread_roles = config["channels"].get(thread_map.get("channel", ""), {}).get("thread_roles", {})
        options = []
        for j, opt in enumerate(_profile_options(spec)):
            rids = [role_ids[f"{spec['key']}:{opt['key']}"]]
            thread_key_ = (thread_map.get("map") or {}).get(opt["key"])
            if thread_key_ and thread_roles.get(thread_key_):
                rids.append(str(thread_roles[thread_key_]))  # country with a Spotify thread
            option = {
                "id": old_ids.get(tuple(sorted(rids))) or _new_snowflake(1000 + 100 * i + j),
                "title": opt["title"],
                "role_ids": rids,
                "channel_ids": [],
            }
            if opt.get("description"):
                option["description"] = opt["description"]
            if opt.get("emoji"):
                option["emoji_name"] = opt["emoji"]
            options.append(option)
        if len(options) > DROPDOWN_MAX:
            print(f"! « {spec['title']} » : {len(options)} choix, Discord en accepte {DROPDOWN_MAX} au plus")
            return 1
        prompts.append({
            "id": old["id"] if old else _new_snowflake(10 + i),
            # multiple choice = 12 options max; beyond, only a dropdown is accepted
            "type": 1 if len(options) > MULTIPLE_CHOICE_MAX else 0,
            "title": spec["title"],
            "single_select": bool(spec.get("single_select")),
            "required": bool(spec.get("required")),
            "in_onboarding": True,
            "options": options,
        })
    others = [p for p in old_prompts if p.get("title") not in profile_titles]
    for p in others:
        p["in_onboarding"] = False  # notifications: « Salons & roles » page only, never at arrival
    _fix_option_emojis(others)
    defaults = list(onboarding.get("default_channel_ids", []))
    for cid in (community.get("rules_channel_id"), community.get("roles_channel_id")):
        if cid and cid not in defaults:
            defaults.append(cid)
    body = {"prompts": prompts + others, "default_channel_ids": defaults,
            "enabled": onboarding.get("enabled", True), "mode": onboarding.get("mode", 0)}
    resp = _api("PUT", f"/guilds/{guild}/onboarding", headers, json=body)
    if not resp.ok:
        print(f"! onboarding HTTP {resp.status_code}: {resp.text[:500]}")
        return 1
    for p in prompts:
        print(f"onboarding : « {p['title']} » {len(p['options'])} choix"
              f"{' (obligatoire)' if p['required'] else ''}")
    for p in others:
        print(f"salons & roles (hors onboarding) : « {p['title']} »")

    # 3. rules screening -----------------------------------------------------
    rules = community.get("rules") or []
    form = {"enabled": True, "description": community.get("rules_disclaimer", "")[:300],
            "form_fields": [{"field_type": "TERMS", "label": "Read and agree to the server rules",
                             "values": [r[:300] for r in rules[:16]], "required": True}]}
    current_form = _api("GET", f"/guilds/{guild}/member-verification", headers)
    same = False
    if current_form.ok:
        cur = current_form.json()
        fields = cur.get("form_fields") or [{}]
        same = (fields[0].get("values") == form["form_fields"][0]["values"]
                and (cur.get("description") or "") == form["description"]
                and "MEMBER_VERIFICATION_GATE_ENABLED" in _api("GET", f"/guilds/{guild}", headers).json().get("features", []))
    # only re-saved when the rules change (every save creates a new form version)
    resp = current_form if same else _api("PATCH", f"/guilds/{guild}/member-verification", headers, json=form)
    if same:
        print(f"filtrage du reglement : deja a jour ({len(rules)} regles)")
    elif resp.ok:
        print(f"filtrage du reglement : actif ({len(rules)} regles)")
    else:
        print(f"! filtrage du reglement refuse au bot (HTTP {resp.status_code}) : a activer a la main "
              "(Parametres du serveur > Securite > Filtrage des regles), regles a coller :")
        for n, rule in enumerate(rules, 1):
            print(f"   {n}. {rule}")

    # 4. #rules / #roles ------------------------------------------------------
    bot_role = community.get("bot_role_id")
    rules_cid, roles_cid = community.get("rules_channel_id"), community.get("roles_channel_id")
    bot_allow = str(P_VIEW | P_SEND | P_EMBED | P_ATTACH | P_HISTORY)
    for cid in (rules_cid, roles_cid):
        if cid and bot_role:
            r = _api("PUT", f"/channels/{cid}/permissions/{bot_role}", headers,
                     json={"type": 0, "allow": bot_allow, "deny": "0"})
            if not r.ok:
                print(f"! permission bot dans {cid} HTTP {r.status_code}: {r.text[:200]}")
    if roles_cid:  # read-only for members (pick roles with the button)
        r = _api("PUT", f"/channels/{roles_cid}/permissions/{guild}", headers,
                 json={"type": 0, "allow": "0", "deny": str(P_SEND | P_SEND_THREADS)})
        if not r.ok:
            print(f"! #roles lecture seule HTTP {r.status_code}: {r.text[:200]}")
    color = 0xC9A227
    button = {"type": 1, "components": [{"type": 2, "style": 5, "label": "Choose your roles",
                                         "emoji": {"name": "🔔"}, "url": customize_url}]}
    rules_text = "\n".join(f"**{n}.** {rule}" for n, rule in enumerate(rules, 1))
    level_labels = {"urgent": "🚨 **Urgent only**", "high": "📋 **Important**",
                    "normal": "☑️ **Normal**", "low": "⚠️ **Everything**"}
    level_help = {"urgent": "new release debuts and huge news",
                  "high": "+ daily Global chart, records, chart entries",
                  "normal": "+ US / UK charts, country cards, artist charts",
                  "low": "every post"}
    chan = config["channels"].get("spotify-charts", {})
    hook = webhook_url("spotify-charts")
    main_cid = requests.get(hook, timeout=20).json().get("channel_id") if hook else None
    chart_roles = [f"⭐ **Overall**: every post in <#{main_cid}>" if main_cid else "⭐ **Overall**: every post"]
    for key, spec in (chan.get("thread_specs") or {}).items():
        label = str(spec.get("role", key)).split(" - ", 1)[-1]
        thread_id = (chan.get("threads") or {}).get(key)
        chart_roles.append(f"{label}: pings in <#{thread_id}>" if thread_id else label)
    messages = community.setdefault("message_ids", {})
    if rules_cid:
        messages["rules"] = _upsert_bot_message(rules_cid, messages.get("rules"), {
            "allowed_mentions": {"parse": []},
            "embeds": [
                {"title": "📜 Server rules", "description": rules_text, "color": color,
                 "footer": {"text": community.get("rules_disclaimer", "")}},
                {"title": "🔔 Next step: choose your notifications", "color": color,
                 "description": ("By default you get **no notifications**. Pick exactly what you want in "
                                 f"<id:customize> (button below), more details in <#{roles_cid}>.")},
            ],
            "components": [button],
        }, headers)
    if roles_cid:
        levels_text = "\n".join(f"{level_labels[lv]}: {level_help[lv]}" for lv in config["levels"]
                                if chan.get("roles", {}).get(lv))
        how_often = f"**Spotify Charts: how often?**\n{levels_text}\n\n" if levels_text else ""
        messages["roles"] = _upsert_bot_message(roles_cid, messages.get("roles"), {
            "allowed_mentions": {"parse": []},
            "embeds": [{
                "title": "🔔 Choose your notifications",
                "color": color,
                "description": (
                    "**By default you get no notifications.** Everything is opt-in: open "
                    "<id:customize> (button below) and pick what you want.\n\n"
                    f"{how_often}"
                    "**Spotify Charts: which charts?**\n" + "\n".join(chart_roles) + "\n\n"
                    "You can change your choices and your profile (age, country, pronouns, gender) "
                    "anytime in <id:customize>."
                ),
            }],
            "components": [button],
        }, headers)
    COMMUNITY_PATH.write_text(json.dumps(community, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"messages : #rules {messages.get('rules')} | #roles {messages.get('roles')}")
    return 0


def cmd_set_role(args) -> int:
    config = load_config()
    if args.level not in config["levels"]:
        print(f"niveau inconnu: {args.level}", file=sys.stderr)
        return 2
    config["channels"][args.channel].setdefault("roles", {})[args.level] = args.id
    _save_config(config)
    print(f"#{args.channel} {args.level} -> {args.id}")
    return 0


def cmd_recent(args) -> int:
    entries = [e for e in read_log() if not args.channel or e.get("channel") == args.channel]
    for e in entries[-args.n:]:
        ids = ",".join(e.get("message_ids", []))
        print(f"{e.get('sent_at')}  #{e.get('channel'):15} [{e.get('level')}] key={e.get('key') or '-'}  ids={ids}")
        print(f"    {e.get('text', '')!r}")
    if not entries:
        print("aucun envoi journalise")
    return 0


def cmd_edit(args) -> int:
    if args.text is None and args.image is None:
        print("rien a modifier : donner --text et/ou --image")
        return 1
    if args.key:
        ok = edit_key(args.channel, args.key, text=args.text, images=args.image)
    else:
        ok = edit_message(args.channel, args.message_id, text=args.text, images=args.image)
    print("modifie" if ok else "echec (voir logs)")
    return 0 if ok else 1


def cmd_strip_links(args) -> int:
    """Remet tous les messages deja envoyes (journal) au style actuel : carte
    (embed) sans lignes de lien, ligne de roles gardee sans re-ping, images
    gardees, heure d'origine gardee. Idempotent (« unchanged » si deja bon)."""
    counts: dict[str, int] = {}
    for e in read_log():
        channel = e.get("channel")
        if args.channel and channel != args.channel:
            continue
        for mid in e.get("message_ids", []):
            if not mid or mid == "?":
                continue
            status = strip_links_message(channel, mid)
            counts[status] = counts.get(status, 0) + 1
            if status in ("edited", "error"):
                print(f"  {status:<9} #{channel} {mid}  key={e.get('key') or '-'}")
    print("resultat:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "aucun message")
    return 0 if not counts.get("error") else 1


def cmd_delete(args) -> int:
    if args.key:
        return 0 if delete_key(args.channel, args.key) else 1
    ok = all(delete_message(args.channel, mid) for mid in args.message_id)
    print("supprime" if ok else "echec (voir logs)")
    return 0 if ok else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    config = load_config()
    channels = list(config["channels"])
    parser = argparse.ArgumentParser(prog="python -m notifiers.discord")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show").set_defaults(func=cmd_show)

    p = sub.add_parser("send")
    p.add_argument("--channel", required=True, choices=channels)
    p.add_argument("--kind")
    p.add_argument("--priority", choices=config["levels"])
    p.add_argument("--key", help="anti-doublon ; omis = pas de dedupe")
    p.add_argument("--text", default="")
    p.add_argument("--image", nargs="*", default=[])
    p.add_argument("--thread", help="code region (global, us, gb...) : poste dans son fil s'il est configure")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("setup-threads", help="cree (bot) les fils de thread_specs manquants")
    p.add_argument("--channel", required=True, choices=channels)
    p.set_defaults(func=cmd_setup_threads)

    p = sub.add_parser("setup-country-roles", help="cree (bot) un role par fil (thread_specs.<key>.role)")
    p.add_argument("--channel", required=True, choices=channels)
    p.set_defaults(func=cmd_setup_country_roles)

    p = sub.add_parser("setup-onboarding", help="question « pays » de l'Onboarding avec les roles des fils")
    p.add_argument("--channel", required=True, choices=channels)
    p.add_argument("--title", help="titre de la question (defaut : « <label> — which charts? »)")
    p.add_argument("--restore-emoji", nargs=2, action="append", metavar=("TITRE", "EMOJI"),
                   help="remet l'emoji d'une option existante (titre exact), repetable")
    p.set_defaults(func=cmd_setup_onboarding)

    p = sub.add_parser("setup-community", help="onboarding profil + reglement + #rules/#roles (community.json)")
    p.set_defaults(func=cmd_setup_community)

    p = sub.add_parser("set-thread", help="enregistre le fil Discord d'une region")
    p.add_argument("--channel", required=True, choices=channels)
    p.add_argument("--thread", required=True, help="code region : global, us, gb (ou uk), fr, ca, worldwide, artists...")
    p.add_argument("--id", required=True, help="ID du fil (clic droit sur le fil > Copier l'identifiant)")
    p.set_defaults(func=cmd_set_thread)

    p = sub.add_parser("setup-roles")
    p.add_argument("--channel", nargs="*", choices=channels)
    p.set_defaults(func=cmd_setup_roles)

    p = sub.add_parser("set-role")
    p.add_argument("--channel", required=True, choices=channels)
    p.add_argument("--level", required=True)
    p.add_argument("--id", required=True)
    p.set_defaults(func=cmd_set_role)

    p = sub.add_parser("recent")
    p.add_argument("--channel", choices=channels)
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser("edit", help="modifier un message deja envoye (texte et/ou image)")
    p.add_argument("--channel", required=True, choices=channels)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", help="modifie le 1er message envoye sous cette cle")
    g.add_argument("--message-id")
    p.add_argument("--text", help="nouveau texte (liens retires, ligne de roles remise sans re-ping)")
    p.add_argument("--image", nargs="*", help="nouvelle(s) image(s) : REMPLACE les images ; omis = inchangees")
    p.set_defaults(func=cmd_edit)

    for name in ("restyle", "strip-links"):
        p = sub.add_parser(name, help="remet les messages deja envoyes au style actuel (carte, sans liens)")
        p.add_argument("--channel", choices=channels, help="omis = tous les salons")
        p.set_defaults(func=cmd_strip_links)

    p = sub.add_parser("delete")
    p.add_argument("--channel", required=True, choices=channels)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--key")
    g.add_argument("--message-id", nargs="+")
    p.set_defaults(func=cmd_delete)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
