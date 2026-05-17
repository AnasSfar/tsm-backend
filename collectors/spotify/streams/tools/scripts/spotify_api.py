from __future__ import annotations

import json
import random
import subprocess
import threading
import time
from pathlib import Path

import requests as _requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright

from history_store import get_last_history_total, has_real_update

STREAMS_DIR = Path(__file__).resolve().parents[2]
_SESSION_FILE = STREAMS_DIR.parent / "charts/global/tools/json/spotify_session.json"
BROWSER_CACHE_DIR = STREAMS_DIR / "tools" / "browser_cache"
_TOKEN_CACHE_PATH = STREAMS_DIR / "tools" / ".token_cache.json"
_WARP_CLI = Path(r"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe")

HEADLESS = True
MAX_PARALLEL_PAGES = 10
HILL_WINDOW = 12
HILL_429_THRESHOLD = 0.15
HILL_MIN_WORKERS = 2
LOG_MODE = "normal"

GRAPHQL_URL = "https://api-partner.spotify.com/pathfinder/v2/query"
GETTRACK_HASH = "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
APP_VERSION = "1.2.87.30.gc764ebf1"

class AdaptiveWorkerState:
    """
    Partagé entre tous les workers de update_streams.py.
    Suit le taux de 429 sur des fenêtres glissantes et ajuste
    le nombre cible de workers actifs (hill climbing).
    """

    def __init__(self, initial: int) -> None:
        self.target     = initial
        self.lock       = threading.Lock()
        self._win_done  = 0
        self._win_429   = 0
        self._win_start = time.time()

    def record(self, got_429: bool) -> None:
        with self.lock:
            self._win_done += 1
            if got_429:
                self._win_429 += 1

            if self._win_done >= HILL_WINDOW:
                elapsed  = max(time.time() - self._win_start, 0.001)
                rate_429 = self._win_429 / self._win_done
                rate_sps = self._win_done / elapsed

                if rate_429 > HILL_429_THRESHOLD and self.target > HILL_MIN_WORKERS:
                    self.target -= 1
                    if LOG_MODE == "verbose":
                        print(f"  [hill] 429={rate_429:.0%}  {rate_sps:.2f}/s  -> workers: {self.target}")
                elif rate_429 == 0 and self.target < MAX_PARALLEL_PAGES:
                    self.target += 1
                    if LOG_MODE == "verbose":
                        print(f"  [hill] 0 429s  {rate_sps:.2f}/s  -> workers: {self.target}")

                self._win_done  = 0
                self._win_429   = 0
                self._win_start = time.time()

def _fetch_tokens_via_http() -> dict:
    """
    Récupère Bearer + client-token via requêtes HTTP directes (requests, sans Playwright).
    Utilise un retry adapter (3 tentatives, backoff) comme Apple Music.
    Retourne un dict avec 'bearer' et 'client_token', ou {} si échec.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9",
    }

    cookies: dict = {}
    if _SESSION_FILE.exists():
        try:
            session_data = json.loads(_SESSION_FILE.read_text(encoding="utf-8-sig"))
            for cookie in session_data.get("cookies", []):
                cookies[cookie["name"]] = cookie["value"]
        except Exception:
            pass

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        raise_on_status=False,
    )
    session = _requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(headers)
    if cookies:
        session.cookies.update(cookies)

    try:
        resp = session.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            timeout=30,
        )
        bearer = resp.json().get("accessToken", "") if resp.ok else ""
    except Exception:
        bearer = ""
    finally:
        pass

    if not bearer:
        session.close()
        return {}

    try:
        ct_resp = session.post(
            "https://clienttoken.spotify.com/v1/clienttoken",
            json={
                "client_data": {
                    "client_version": APP_VERSION,
                    "client_id": "d8a5ed958d274c2e8ee717e6a4b0971d",
                    "js_sdk_data": {},
                }
            },
            headers={"content-type": "application/json"},
            timeout=30,
        )
        body = ct_resp.json() if ct_resp.ok else {}
        client_token = body.get("granted_token", {}).get("token", "")
    except Exception:
        client_token = ""
    finally:
        session.close()

    if bearer and client_token:
        return {"bearer": bearer, "client_token": client_token}
    return {}

def _test_tokens(tokens: dict) -> bool:
    """Vérifie si les tokens sont encore valides via un appel GraphQL léger."""
    body = {
        "operationName": "getTrack",
        "variables": {"uri": "spotify:track:0V3wPSX9ygBnCm8psDIegu"},
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": GETTRACK_HASH}},
    }
    headers = {
        "authorization": f"Bearer {tokens['bearer']}",
        "client-token": tokens["client_token"],
        "app-platform": "WebPlayer",
        "spotify-app-version": APP_VERSION,
        "content-type": "application/json",
    }
    try:
        resp = _requests.post(GRAPHQL_URL, json=body, headers=headers, timeout=(5, 10))
        return resp.status_code == 200
    except Exception:
        return False

def _warp_connect() -> None:
    cli = str(_WARP_CLI) if _WARP_CLI.exists() else "warp-cli"
    try:
        res = subprocess.run([cli, "status"], timeout=5, capture_output=True, text=True, check=False)
        if "Connected" in (res.stdout or ""):
            print("TokenManager: WARP deja connecte")
            return
        subprocess.run([cli, "connect"], timeout=15, check=False, capture_output=True)
        # Poll warp-cli status until "Connected" — up to 15s
        for _ in range(15):
            res = subprocess.run([cli, "status"], timeout=5, capture_output=True, text=True, check=False)
            if "Connected" in (res.stdout or ""):
                break
            time.sleep(1)
        else:
            time.sleep(3)  # fallback if status never confirmed
        print("TokenManager: WARP connecté")
    except Exception as e:
        print(f"TokenManager: impossible de connecter WARP ({e})")

def _warp_disconnect() -> None:
    print("TokenManager: WARP garde connecte")

class TokenManager:
    """
    Capture Bearer + client-token depuis Spotify une seule fois via Playwright.
    Thread-safe : sur 401, un seul thread re-capture, les autres attendent.
    Les tokens sont mis en cache sur disque pour éviter Playwright si encore valides.
    """

    def __init__(self) -> None:
        self._tokens: dict = {}
        self._lock = threading.Lock()
        self._recapture_lock = threading.Lock()  # atomic owner election

    def _save_cache(self, tokens: dict) -> None:
        try:
            _TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_CACHE_PATH.write_text(json.dumps(tokens), encoding="utf-8")
        except Exception:
            pass

    def _try_cached(self) -> bool:
        """Charge les tokens depuis le cache disque et vérifie leur validité. Retourne True si réutilisables."""
        if not _TOKEN_CACHE_PATH.exists():
            return False
        try:
            tokens = json.loads(_TOKEN_CACHE_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        if not tokens.get("bearer") or not tokens.get("client_token"):
            return False
        if LOG_MODE != "quiet":
            print("TokenManager: test des tokens en cache…")
        if _test_tokens(tokens):
            with self._lock:
                self._tokens = tokens
            if LOG_MODE != "quiet":
                print(f"TokenManager: tokens en cache valides ({tokens['bearer'][:20]}…)")
            return True
        if LOG_MODE != "quiet":
            print("TokenManager: tokens en cache expirés")
        return False

    def _try_via_http(self) -> bool:
        """Essaie de récupérer les tokens via HTTP pur (sp_dc + endpoints Spotify). Pas de Playwright."""
        if LOG_MODE != "quiet":
            print("TokenManager: tentative HTTP directe…")
        tokens = _fetch_tokens_via_http()
        if tokens.get("bearer"):
            with self._lock:
                self._tokens = tokens
            self._save_cache(tokens)
            if LOG_MODE != "quiet":
                print(f"TokenManager: Bearer capturé via HTTP ({tokens['bearer'][:20]}…)")
            return True
        if LOG_MODE != "quiet":
            print("TokenManager: échec HTTP direct")
        return False

    def capture(self) -> bool:
        """Essaie dans l'ordre : cache disque → HTTP direct → Playwright (x5)."""
        if self._try_cached():
            return True
        _warp_connect()
        try:
            if self._try_via_http():
                return True

            MAX_ATTEMPTS = 5
            for attempt in range(1, MAX_ATTEMPTS + 1):
                tokens: dict = {}

                def on_request(req):
                    if "api-partner.spotify.com" in req.url and not tokens.get("bearer"):
                        auth = req.headers.get("authorization", "")
                        ct   = req.headers.get("client-token", "")
                        if auth.startswith("Bearer ") and ct:
                            tokens["bearer"]       = auth[7:]
                            tokens["client_token"] = ct

                if LOG_MODE != "quiet":
                    print(f"TokenManager: capture des tokens Spotify via Playwright… (tentative {attempt})")
                ctx_kwargs: dict = {
                    "user_agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
                    ),
                }
                if _SESSION_FILE.exists():
                    ctx_kwargs["storage_state"] = str(_SESSION_FILE)

                url = "https://open.spotify.com/track/0V3wPSX9ygBnCm8psDIegu"

                p = sync_playwright().start()
                browser = None
                try:
                    browser = p.chromium.launch(
                        headless=HEADLESS,
                        args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--no-proxy-server"],
                    )
                    ctx  = browser.new_context(**ctx_kwargs)
                    page = ctx.new_page()
                    page.on("request", on_request)
                    try:
                        page.goto(url, wait_until="commit", timeout=30_000)
                    except Exception as goto_err:
                        print(f"TokenManager: goto échoué — {goto_err!s:.200}")
                        raise

                    final_url = page.url
                    if "accounts.spotify.com" in final_url or "login" in final_url:
                        print(f"TokenManager: redirect login detecte ({final_url})")
                        print("TokenManager: session expiree - lance scripts/refresh_spotify_session.py")
                        return False

                    print(f"TokenManager: page chargee: {final_url}")
                    deadline = time.time() + 30
                    while not tokens.get("bearer") and time.time() < deadline:
                        page.wait_for_timeout(500)

                    if not tokens.get("bearer"):
                        print(f"TokenManager: aucun appel api-partner intercepte apres 30s (URL: {page.url})")
                except Exception as e:
                    if "goto échoué" not in str(e):
                        print(f"TokenManager: erreur capture (tentative {attempt}): {e}")
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        p.stop()
                    except Exception:
                        pass

                if tokens.get("bearer"):
                    with self._lock:
                        self._tokens = tokens
                    self._save_cache(tokens)
                    if LOG_MODE != "quiet":
                        print(f"TokenManager: Bearer capturé ({tokens['bearer'][:20]}…)")
                    return True
                if LOG_MODE != "quiet":
                    print(f"TokenManager: tentative {attempt} échouée")
                time.sleep(3)

            if LOG_MODE != "quiet":
                print("TokenManager: échec — tous les modes de capture ont échoué")
            return False
        finally:
            _warp_disconnect()

    def get(self) -> dict:
        with self._lock:
            return dict(self._tokens)

    def mark_expired(self) -> None:
        """Appelé par un worker sur 401 — déclenche une re-capture (un seul thread à la fois)."""
        if not self._recapture_lock.acquire(blocking=False):
            # Another thread is already recapturing — wait for it then return
            with self._recapture_lock:
                pass
            return
        try:
            self.capture()
        finally:
            self._recapture_lock.release()

    @property
    def available(self) -> bool:
        with self._lock:
            return bool(self._tokens.get("bearer"))

def fetch_playcount_api(
    track_id: str,
    token_mgr: TokenManager,
    session: "_requests.Session",
    metrics: dict | None = None,
) -> int | None:
    """
    Récupère le playcount via l'API GraphQL Spotify.
    Retourne un int, ou None si la track n'est pas trouvée / erreur.
    Sur 401, déclenche une re-capture des tokens et retente une fois.
    """
    tokens = token_mgr.get()
    if not tokens.get("bearer"):
        return None

    body = {
        "variables":     {"uri": f"spotify:track:{track_id}"},
        "operationName": "getTrack",
        "extensions":    {
            "persistedQuery": {
                "version":    1,
                "sha256Hash": GETTRACK_HASH,
            }
        },
    }

    # Retry policy:
    # - 401: recapture tokens once
    # - 429: honor Retry-After when present + exponential backoff
    # - 5xx / network errors: retry with backoff
    # - other 4xx: do not retry
    for token_attempt in range(2):
        headers = {
            "Authorization":       f"Bearer {tokens['bearer']}",
            "client-token":        tokens["client_token"],
            "spotify-app-version": APP_VERSION,
            "app-platform":        "WebPlayer",
            "Accept":              "application/json",
            "Content-Type":        "application/json;charset=UTF-8",
            "Origin":              "https://open.spotify.com",
            "Referer":             "https://open.spotify.com/",
            "User-Agent":          (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/133.0.0.0 Safari/537.36"
            ),
        }
        backoff = 1.0
        for attempt in range(5):
            try:
                resp = session.post(GRAPHQL_URL, json=body, headers=headers, timeout=(5, 15))
            except Exception:
                # Network hiccup — retry
                time.sleep(min(3.0, backoff) + random.random() * 0.1)
                backoff *= 1.5
                continue

            code = resp.status_code

            if code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = {}
                track_union = (data.get("data") or {}).get("trackUnion") or {}
                pc = track_union.get("playcount")
                if pc is not None:
                    try:
                        return int(pc)
                    except Exception:
                        return None
                m = re.search(r'"playcount":\s*"(\d+)"', json.dumps(data))
                return int(m.group(1)) if m else None

            if code == 401 and token_attempt == 0:
                if LOG_MODE != "quiet":
                    print("  [API] 401 — re-capture des tokens…")
                token_mgr.mark_expired()
                tokens = token_mgr.get()
                if not tokens.get("bearer"):
                    return None
                break  # restart with refreshed tokens

            if code == 429:
                if metrics is not None:
                    metrics["had_429"] = True
                ra = (resp.headers.get("Retry-After") or "").strip()
                try:
                    wait_s = float(ra)
                except Exception:
                    wait_s = backoff
                wait_s = min(15.0, max(0.5, wait_s)) + random.random() * 0.1
                time.sleep(wait_s)
                backoff = min(15.0, backoff * 1.5)
                continue

            if code in {408, 500, 502, 503, 504}:
                time.sleep(min(5.0, backoff) + random.random() * 0.1)
                backoff *= 1.5
                continue

            # Other errors are not transient.
            return None
    return None

def _probe_via_api(probe_tracks: list[dict], token_mgr: TokenManager) -> dict | None:
    """
    Probe via API GraphQL. Retourne le même dict que _probe_on_page,
    ou None si l'API n'est pas disponible.
    Logique séquentielle : 1ère chanson OK updatée → check 2ème → si updatée → can_start=True.
    """
    if not token_mgr.available:
        return None

    session = _requests.Session()
    successful_probes = 0
    updated_probes = 0
    results = []
    can_start = False

    try:
        for track in probe_tracks:
            pc = fetch_playcount_api(track["track_id"], token_mgr, session)
            last_total = get_last_history_total(track["track_id"])

            if pc is not None:
                updated = has_real_update(last_total, pc)
                successful_probes += 1
                if updated:
                    updated_probes += 1
                results.append({
                    "title":            track["title"],
                    "status":           "ok",
                    "streams":          pc,
                    "previous_streams": last_total,
                    "updated":          updated,
                    "raw":              str(pc),
                })
                if successful_probes == 1 and not updated:
                    break  # 1ère chanson pas updatée → inutile de continuer
                if successful_probes == 2:
                    can_start = updated
                    break  # 2ème chanson OK → décision finale
            else:
                results.append({
                    "title":            track["title"],
                    "status":           "not_found",
                    "streams":          None,
                    "previous_streams": last_total,
                    "updated":          False,
                    "raw":              None,
                })
    finally:
        session.close()

    return {
        "can_start_full_run":  can_start,
        "successful_probes":   successful_probes,
        "updated_probes":      updated_probes,
        "results":             results,
    }
