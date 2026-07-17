#!/usr/bin/env python3
"""Post Twitter via Playwright (profil Chrome persistant) - partage Fr + Global."""
import json
import hashlib
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TWITTER_COORD_DIR = Path(tempfile.gettempdir()) / "tsm_twitter_posts"
TWITTER_COORD_LOCK = TWITTER_COORD_DIR / "coordinator.lock"
TWITTER_POST_LOCK_TIMEOUT = 30 * 60
TWITTER_ACCOUNT_SPACING_SECONDS = int(os.getenv("TWITTER_ACCOUNT_SPACING_SECONDS", "60"))
TWITTER_MAX_ACTIVE_ACCOUNTS = int(os.getenv("TWITTER_MAX_ACTIVE_ACCOUNTS", "2"))
TWITTER_FILE_UPLOAD_TIMEOUT_MS = int(os.getenv("TWITTER_FILE_UPLOAD_TIMEOUT_MS", "120000"))
TWITTER_COMPOSE_EDITOR_TIMEOUT_MS = int(os.getenv("TWITTER_COMPOSE_EDITOR_TIMEOUT_MS", "60000"))
TWITTER_TEXT_LIMIT = 280
TWITTER_BROWSER_LAUNCH_ATTEMPTS = int(os.getenv("TWITTER_BROWSER_LAUNCH_ATTEMPTS", "3"))
TWITTER_BROWSER_LAUNCH_RETRY_DELAY = int(os.getenv("TWITTER_BROWSER_LAUNCH_RETRY_DELAY", "10"))
TWITTER_LOCK_STALE_SECONDS = int(os.getenv("TWITTER_LOCK_STALE_SECONDS", str(TWITTER_POST_LOCK_TIMEOUT)))
LAST_POST_ERROR = ""


def get_last_post_error() -> str:
    return LAST_POST_ERROR


def _set_last_post_error(message: str) -> None:
    global LAST_POST_ERROR
    LAST_POST_ERROR = str(message or "")


def _lock_pid_alive(path: Path) -> bool:
    try:
        raw = path.read_text(encoding="ascii").strip()
        pid = int(raw)
    except Exception:
        return True
    if pid <= 0 or pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(ok) and exit_code.value == STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _profile_dir(session_file: Path) -> Path:
    """Dossier du profil Chrome persistant, a cote du fichier de session."""
    return Path(session_file).parent / "chrome_profile"


def _account_key(session_file: Path) -> str:
    session_key = str(Path(session_file).resolve()).casefold()
    return hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:16]


def _exclusive_file(path: Path, *, timeout: int, stale_after: int | None = None):
    start = time.time()
    fd = None
    while fd is None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
        except FileExistsError:
            try:
                if not _lock_pid_alive(path):
                    if not _safe_unlink(path):
                        time.sleep(2)
                        continue
                    continue
                max_age = stale_after or timeout
                if time.time() - path.stat().st_mtime > max_age:
                    if not _safe_unlink(path):
                        time.sleep(2)
                        continue
                    continue
            except FileNotFoundError:
                continue
            if time.time() - start > timeout:
                raise TimeoutError(f"Twitter post lock timeout: {path}")
            time.sleep(2)
    return fd


def _safe_unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        return False


@contextmanager
def _coordinator_lock(timeout: int = TWITTER_POST_LOCK_TIMEOUT):
    fd = _exclusive_file(TWITTER_COORD_LOCK, timeout=timeout, stale_after=60)
    try:
        yield
    finally:
        os.close(fd)
        _safe_unlink(TWITTER_COORD_LOCK)


def _active_account_markers() -> list[Path]:
    return [path for path in TWITTER_COORD_DIR.glob("active_*.lock") if path.is_file()]


def _acquire_active_account(account_key: str, *, timeout: int, stale_after: int) -> Path:
    marker = TWITTER_COORD_DIR / f"active_{account_key}.lock"
    start = time.time()
    while True:
        with _coordinator_lock(timeout=timeout):
            active = _active_account_markers()
            for active_marker in active:
                try:
                    if time.time() - active_marker.stat().st_mtime > stale_after:
                        _safe_unlink(active_marker)
                except FileNotFoundError:
                    pass
            active = _active_account_markers()
            if marker.exists() or len(active) < TWITTER_MAX_ACTIVE_ACCOUNTS:
                marker.write_text(str(os.getpid()), encoding="ascii")
                return marker
        if time.time() - start > timeout:
            raise TimeoutError("Twitter active-account slot timeout")
        time.sleep(2)


def _last_post_path(account_key: str) -> Path:
    return TWITTER_COORD_DIR / f"last_post_{account_key}.txt"


def _wait_account_spacing(account_key: str) -> None:
    last_post_path = _last_post_path(account_key)
    try:
        last_post_at = float(last_post_path.read_text(encoding="ascii").strip())
    except Exception:
        return
    wait_s = TWITTER_ACCOUNT_SPACING_SECONDS - (time.time() - last_post_at)
    if wait_s > 0:
        print(f"Waiting {int(wait_s)}s before next X post for this account...")
        time.sleep(wait_s)


def _mark_account_posted(account_key: str) -> None:
    TWITTER_COORD_DIR.mkdir(parents=True, exist_ok=True)
    _last_post_path(account_key).write_text(str(time.time()), encoding="ascii")


@contextmanager
def _twitter_account_slot(session_file: Path, timeout: int = TWITTER_POST_LOCK_TIMEOUT):
    """Serialize one X account, allow up to two accounts to post at once."""
    account_key = _account_key(session_file)
    account_lock = TWITTER_COORD_DIR / f"account_{account_key}.lock"
    stale_after = max(60, TWITTER_LOCK_STALE_SECONDS)
    account_fd = _exclusive_file(account_lock, timeout=timeout, stale_after=stale_after)
    active_marker = None
    try:
        active_marker = _acquire_active_account(account_key, timeout=timeout, stale_after=stale_after)
        yield account_key
    finally:
        if active_marker is not None:
            _safe_unlink(active_marker)
        os.close(account_fd)
        _safe_unlink(account_lock)


def _clean_editor_text(text: str) -> str:
    """Normalize the text Playwright reads from X's contenteditable composer."""
    return (
        (text or "")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .strip()
    )


def _validate_tweet_lengths(tweets: list[str], *, label: str) -> bool:
    for index, tweet in enumerate(tweets, 1):
        length = len(str(tweet or ""))
        if length > TWITTER_TEXT_LIMIT:
            print(
                f"X {label} trop long: post {index}/{len(tweets)} "
                f"fait {length} caracteres (limite {TWITTER_TEXT_LIMIT})."
            )
            print(str(tweet or ""))
            return False
    return True


def _visible_text(locator) -> str:
    try:
        if locator.count() and locator.first.is_visible(timeout=500):
            return locator.first.inner_text(timeout=1_000)
    except Exception:
        pass
    return ""


def _post_feedback_state(page) -> str | None:
    """Return 'success', 'error', or None from transient X feedback."""
    feedback = "\n".join(
        text for text in [
            _visible_text(page.locator("[data-testid='toast']")),
            _visible_text(page.locator("[role='alert']")),
            _visible_text(page.locator("[aria-live='assertive']")),
            _visible_text(page.locator("[aria-live='polite']")),
        ]
        if text
    ).lower()
    if not feedback:
        return None

    error_markers = [
        "already sent",
        "duplicate",
        "error",
        "failed",
        "something went wrong",
        "try again",
        "erreur",
        "echoue",
        "échoué",
        "réessayez",
    ]
    if any(marker in feedback for marker in error_markers):
        print(f"X feedback erreur: {feedback}")
        return "error"

    success_markers = [
        "your post was sent",
        "your tweet was sent",
        "post was sent",
        "tweet was sent",
        "post sent",
        "tweet sent",
        "posté",
        "publie",
        "publié",
        "envoye",
        "envoyé",
    ]
    if any(marker in feedback for marker in success_markers):
        return "success"

    return None


def _composer_text(page) -> str | None:
    """Return composer text, '' when cleared, or None when no visible composer exists."""
    try:
        editor = page.locator("[data-testid='tweetTextarea_0']").first
        if not editor.count() or not editor.is_visible(timeout=500):
            return None
        return _clean_editor_text(editor.inner_text(timeout=1_000))
    except Exception:
        return None


def _wait_post_submitted(page, expected_text: str = "", timeout_ms: int = 45_000) -> bool:
    """Return True only when X gives a positive signal that the post was accepted."""
    expected_text = _clean_editor_text(expected_text)
    deadline = time.time() + timeout_ms / 1000
    last_editor_text = None

    while time.time() < deadline:
        if "/status/" in page.url:
            return True

        feedback_state = _post_feedback_state(page)
        if feedback_state == "success":
            return True
        if feedback_state == "error":
            return False

        editor_text = _composer_text(page)
        if editor_text is None:
            # Compose route/modal disappeared. Give X a brief chance to surface an error toast.
            time.sleep(1)
            feedback_state = _post_feedback_state(page)
            return feedback_state != "error"

        last_editor_text = editor_text
        if expected_text and expected_text in editor_text:
            time.sleep(1)
            continue
        if not editor_text:
            # Home composer can stay visible after a successful post; the useful signal is that it cleared.
            time.sleep(1)
            feedback_state = _post_feedback_state(page)
            return feedback_state != "error"

        time.sleep(1)

    print(f"X Post non confirme. URL actuelle: {page.url}. Texte editeur restant: {last_editor_text!r}")
    return False


def _wait_post_scheduled(page, expected_text: str = "", timeout_ms: int = 45_000) -> bool:
    """Return True when X accepts a scheduled post."""
    expected_text = _clean_editor_text(expected_text)
    deadline = time.time() + timeout_ms / 1000
    last_editor_text = None

    while time.time() < deadline:
        feedback = "\n".join(
            text for text in [
                _visible_text(page.locator("[data-testid='toast']")),
                _visible_text(page.locator("[role='alert']")),
                _visible_text(page.locator("[aria-live='assertive']")),
                _visible_text(page.locator("[aria-live='polite']")),
                _visible_text(page.locator("body")),
            ]
            if text
        ).lower()

        if any(marker in feedback for marker in [
            "post scheduled",
            "tweet scheduled",
            "your post was scheduled",
            "your tweet was scheduled",
            "scheduled for",
            "post programme",
            "post programm",
            "tweet programme",
            "tweet programm",
        ]):
            return True
        if any(marker in feedback for marker in [
            "error",
            "failed",
            "something went wrong",
            "try again",
            "erreur",
            "echoue",
            "r\u00e9essayez",
        ]):
            print(f"X feedback programmation erreur: {feedback[:500]}")
            return False

        editor_text = _composer_text(page)
        if editor_text is None:
            time.sleep(1)
            return _post_feedback_state(page) != "error"

        last_editor_text = editor_text
        if expected_text and expected_text in editor_text:
            time.sleep(1)
            continue
        if not editor_text:
            time.sleep(1)
            return _post_feedback_state(page) != "error"

        time.sleep(1)

    print(f"X programmation non confirmee. URL actuelle: {page.url}. Texte editeur restant: {last_editor_text!r}")
    return False


def _coerce_schedule_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("scheduled_at is required")

    normalized = raw.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "scheduled_at must be an ISO datetime or 'YYYY-MM-DD HH:MM'"
        ) from exc
    if parsed.tzinfo:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _visible_controls(root, selector: str):
    controls = []
    locator = root.locator(selector)
    try:
        count = locator.count()
    except Exception:
        return controls
    for index in range(count):
        control = locator.nth(index)
        try:
            if control.is_visible(timeout=500):
                controls.append(control)
        except Exception:
            pass
    return controls


def _dialog_control_by_label(dialog, labels: list[str]):
    for label in labels:
        control = dialog.get_by_label(re.compile(label, re.I)).first
        try:
            if control.count() and control.is_visible(timeout=500):
                return control
        except Exception:
            pass
    return None


def _choose_schedule_value(page, control, labels: list[str], values: list[str] | None = None) -> bool:
    values = values or []
    for label in labels:
        try:
            control.select_option(label=label, timeout=1_000)
            return True
        except Exception:
            pass
    for value in values:
        try:
            control.select_option(value=value, timeout=1_000)
            return True
        except Exception:
            pass
    for label in labels:
        try:
            control.click(timeout=2_000)
            option = page.get_by_role("option", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
            option.wait_for(state="visible", timeout=2_000)
            option.click(timeout=2_000)
            return True
        except Exception:
            pass
    for label in labels:
        try:
            control.fill(label, timeout=1_000)
            return True
        except Exception:
            pass
    return False


def _click_first_visible(page, selectors: list[str], *, timeout_ms: int = 5_000) -> bool:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible(timeout=500):
                    locator.click(timeout=2_000)
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _open_schedule_dialog(page):
    selectors = [
        "[data-testid='scheduleOption']",
        "[aria-label='Schedule post']",
        "[aria-label='Schedule Tweet']",
        "[aria-label='Schedule']",
        "[aria-label='Programmer le post']",
        "[aria-label='Programmer le Tweet']",
        "[aria-label='Programmer']",
    ]
    if not _click_first_visible(page, selectors, timeout_ms=8_000):
        raise RuntimeError("X bouton de programmation introuvable.")

    dialog = page.locator("[role='dialog']").filter(
        has_text=re.compile("Schedule|Programmer|Programmation|Date|Time|Heure", re.I)
    ).last
    dialog.wait_for(state="visible", timeout=10_000)
    return dialog


def _set_schedule_dialog(page, scheduled_at: datetime | str) -> None:
    scheduled_at = _coerce_schedule_datetime(scheduled_at)
    if scheduled_at <= datetime.now():
        raise ValueError(f"scheduled_at must be in the future: {scheduled_at}")

    dialog = _open_schedule_dialog(page)

    month_names_en = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    month_names_fr = [
        "janvier", "fevrier", "février", "mars", "avril", "mai", "juin",
        "juillet", "aout", "août", "septembre", "octobre", "novembre", "decembre", "décembre",
    ]
    month = scheduled_at.month
    day = scheduled_at.day
    year = scheduled_at.year
    hour_24 = scheduled_at.hour
    minute = scheduled_at.minute
    hour_12 = hour_24 % 12 or 12
    ampm = "AM" if hour_24 < 12 else "PM"

    controls = _visible_controls(dialog, "select, [role='combobox'], input")
    month_control = _dialog_control_by_label(dialog, ["month", "mois"]) or (controls[0] if len(controls) > 0 else None)
    day_control = _dialog_control_by_label(dialog, ["day", "jour"]) or (controls[1] if len(controls) > 1 else None)
    year_control = _dialog_control_by_label(dialog, ["year", "annee", "année"]) or (controls[2] if len(controls) > 2 else None)
    hour_control = _dialog_control_by_label(dialog, ["hour", "heure"]) or (controls[3] if len(controls) > 3 else None)
    minute_control = _dialog_control_by_label(dialog, ["minute"]) or (controls[4] if len(controls) > 4 else None)
    ampm_control = _dialog_control_by_label(dialog, ["am", "pm"]) or (controls[5] if len(controls) > 5 else None)

    required = [month_control, day_control, year_control, hour_control, minute_control]
    if any(control is None for control in required):
        body = _visible_text(dialog)[:800]
        raise RuntimeError(f"X modal programmation incomplet. Contenu: {body!r}")

    month_labels = [
        month_names_en[month - 1],
        month_names_fr[month - 1],
        str(month),
        f"{month:02d}",
    ]
    if not _choose_schedule_value(page, month_control, month_labels, [str(month), str(month - 1), f"{month:02d}"]):
        raise RuntimeError("X impossible de choisir le mois.")
    if not _choose_schedule_value(page, day_control, [str(day), f"{day:02d}"]):
        raise RuntimeError("X impossible de choisir le jour.")
    if not _choose_schedule_value(page, year_control, [str(year)]):
        raise RuntimeError("X impossible de choisir l'annee.")

    hour_labels = [str(hour_12), f"{hour_12:02d}", str(hour_24), f"{hour_24:02d}"]
    if not _choose_schedule_value(page, hour_control, hour_labels):
        raise RuntimeError("X impossible de choisir l'heure.")
    if not _choose_schedule_value(page, minute_control, [f"{minute:02d}", str(minute)]):
        raise RuntimeError("X impossible de choisir les minutes.")
    if ampm_control is not None:
        _choose_schedule_value(page, ampm_control, [ampm])

    confirm_selectors = [
        "[data-testid='scheduledConfirmationPrimaryAction']",
        "[data-testid='scheduleButton']",
        "[role='button']:has-text('Confirm')",
        "[role='button']:has-text('Schedule')",
        "[role='button']:has-text('Confirmer')",
        "[role='button']:has-text('Programmer')",
    ]
    if not _click_first_visible(page, confirm_selectors, timeout_ms=8_000):
        raise RuntimeError("X bouton de confirmation programmation introuvable.")


def _wait_visible_editor(page, index: int = 0, timeout_ms: int = 15_000):
    selectors = [
        f"[data-testid='tweetTextarea_{index}']",
        f"[role='textbox'][data-testid='tweetTextarea_{index}']",
    ]
    if index == 0:
        selectors.extend([
            "[data-testid^='tweetTextarea_']",
            "div[role='textbox'][contenteditable='true']",
        ])
    last_error = None
    per_selector_timeout = max(1_000, min(timeout_ms, 10_000))
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in selectors:
            editor = page.locator(selector).first
            try:
                editor.wait_for(state="visible", timeout=per_selector_timeout)
                return editor
            except PlaywrightTimeout as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
        time.sleep(1)
    if last_error:
        raise last_error
    raise TimeoutError("X compose editor not visible")


def _open_compose_and_wait_editor(page, session_file: Path, index: int = 0, timeout_ms: int = TWITTER_COMPOSE_EDITOR_TIMEOUT_MS):
    """Open X compose and recover from slow loads or expired sessions."""
    session_file = Path(session_file)
    credentials = None
    attempted_login = False

    for attempt in range(1, 4):
        if "compose/post" not in page.url:
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30_000)
        time.sleep(2)

        if _looks_logged_out(page):
            credentials = credentials or _load_credentials(session_file)
            if credentials and not attempted_login:
                print("Session expiree. Reconnexion automatique...")
                _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                attempted_login = True
                page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30_000)
                time.sleep(2)
            else:
                raise RuntimeError(f"X session non connectee, URL actuelle: {page.url}")

        try:
            return _wait_visible_editor(page, index, timeout_ms=timeout_ms)
        except Exception as exc:
            # La redirection vers le login peut arriver APRES le check du haut de
            # boucle (SPA : l'URL reste compose/post pendant ~2 s) — re-vérifier ici,
            # sinon l'auto-relogin n'est jamais tenté (incident fr-post 15/07/2026).
            if _looks_logged_out(page):
                credentials = credentials or _load_credentials(session_file)
                if credentials and not attempted_login:
                    print("Session expiree (redirect login). Reconnexion automatique...")
                    _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                    attempted_login = True
                    page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(2)
                    continue
                if attempt >= 3 or attempted_login:
                    raise RuntimeError(f"X session non connectee, URL actuelle: {page.url}") from exc
            if attempt >= 3:
                title = ""
                body_text = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                try:
                    body_text = page.locator("body").inner_text(timeout=2_000)[:500]
                except Exception:
                    pass
                raise RuntimeError(
                    f"X compose editor introuvable apres {attempt} essais. "
                    f"URL: {page.url}. Title: {title!r}. Body: {body_text!r}"
                ) from exc
            print("X compose: editeur introuvable, rechargement...")
            page.reload(wait_until="domcontentloaded", timeout=30_000)
            time.sleep(3)


def _click_thread_add_button(page) -> bool:
    candidates = [
        "[data-testid='addButton']",
        "[aria-label='Add another post']",
        "[aria-label='Add another Tweet']",
        "[aria-label='Ajouter un autre post']",
        "[aria-label='Ajouter un autre Tweet']",
        "[aria-label='Add']",
        "[aria-label='Ajouter']",
    ]
    for selector in candidates:
        button = page.locator(selector).first
        try:
            if button.count() and button.is_visible(timeout=1_000):
                button.click(timeout=5_000)
                return True
        except Exception:
            pass
    return False


def _post_compose_text_thread(page, tweets: list[str]) -> bool:
    page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
    time.sleep(2)

    _wait_visible_editor(page, 0).click(timeout=10_000)
    _wait_visible_editor(page, 0).fill(tweets[0])
    time.sleep(1)

    for i, tweet in enumerate(tweets[1:], 1):
        if not _click_thread_add_button(page):
            print("X bouton d'ajout au thread introuvable.")
            return False
        editor = _wait_visible_editor(page, i)
        editor.click(timeout=10_000)
        editor.fill(tweet)
        time.sleep(0.5)

    page.locator(
        "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
    ).first.click(timeout=10_000)
    return _wait_post_submitted(page, "\n".join(tweets), timeout_ms=60_000)


# Depuis ~2026-07-15, le conteneur commun éditeur/toolbar du composer modal est à
# ancestor::div[17] (le DOM X s'est approfondi) : chercher au-delà de 16 est requis.
# Le conteneur englobant les DEUX composers (modal + inline home) est vers 38, donc
# 30 reste sans risque de fuite vers l'autre composer.
TWITTER_COMPOSER_SCOPE_MAX_DEPTH = 30

MEDIA_BUTTON_SELECTOR = (
    "[aria-label='Add media'], "
    "[aria-label='Ajouter des médias'], "
    "[aria-label='Ajouter du contenu multimédia'], "
    "[aria-label='Add photos or video'], "
    "[aria-label='Ajouter des photos ou une vidéo'], "
    "[aria-label='Ajouter des photos ou une video']"
)


def _composer_scope(editor):
    for depth in range(1, TWITTER_COMPOSER_SCOPE_MAX_DEPTH):
        scope = editor.locator(f"xpath=ancestor::div[{depth}]")
        try:
            if (
                scope.locator("input[type='file'][accept*='image']").count()
                or scope.locator(MEDIA_BUTTON_SELECTOR).count()
            ):
                return scope
        except Exception:
            pass
    return None


def _strict_composer_scope(editor):
    """Nearest composer block for one post, used to avoid counting another post's image."""
    candidate = None
    for depth in range(1, TWITTER_COMPOSER_SCOPE_MAX_DEPTH):
        scope = editor.locator(f"xpath=ancestor::div[{depth}]")
        try:
            # role='textbox' exclut tweetTextarea_0_label et
            # tweetTextarea_0RichTextInputContainer, qui matchent aussi le préfixe
            # et plafonnaient le scope sous le vrai bloc composer (scope=0 garanti).
            if scope.locator("div[role='textbox'][data-testid^='tweetTextarea_']").count() == 1:
                candidate = scope
        except Exception:
            pass
    return candidate


def _media_button_candidates(root):
    selectors = [
        "[aria-label='Add media']",
        "[aria-label='Ajouter des médias']",
        "[aria-label='Ajouter du contenu multimédia']",
        "[aria-label='Add photos or video']",
        "[aria-label='Ajouter des photos ou une vidéo']",
        "[aria-label='Ajouter des photos ou une video']",
        "[data-testid='fileInput']",
    ]
    for selector in selectors:
        locator = root.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue
        for i in range(count):
            yield locator.nth(i)


def _attach_with_file_chooser(page, root, image_path: Path) -> bool:
    for button in _media_button_candidates(root):
        try:
            if not button.is_visible(timeout=500):
                continue
            with page.expect_file_chooser(timeout=5_000) as chooser_info:
                button.click(timeout=5_000)
            chooser_info.value.set_files(str(image_path))
            return True
        except Exception:
            pass
    return False


def _attach_image_to_composer(page, editor, image_path: Path, index: int = 0):
    scope = _composer_scope(editor)
    root = scope or page
    # La preview doit être vérifiée dans le bloc composer de CET éditeur :
    # avec un scope page entière, une preview apparue dans un AUTRE composer
    # (ex. composer inline du home derrière le modal) validait l'upload et le
    # tweet partait sans image.
    verify_scope = scope or _strict_composer_scope(editor)
    if verify_scope is None:
        raise RuntimeError("X composer scope introuvable — impossible de vérifier l'upload d'image")
    before = _attached_image_count(verify_scope)
    if not _attach_with_file_chooser(page, root, image_path):
        print("X file chooser introuvable, fallback input[type=file]")
        file_inputs = root.locator("input[type='file'][accept*='image']")
        count = file_inputs.count()
        if count:
            file_inputs.last.set_input_files(
                str(image_path),
                timeout=TWITTER_FILE_UPLOAD_TIMEOUT_MS,
            )
        else:
            page.locator("input[type='file'][accept*='image']").nth(index).set_input_files(
                str(image_path),
                timeout=TWITTER_FILE_UPLOAD_TIMEOUT_MS,
            )
    _wait_for_attached_image(verify_scope, before + 1, page=page, editor=editor, image_path=image_path)
    return verify_scope


def _attached_image_count(root) -> int:
    selectors = [
        "[data-testid='attachments'] img",
        "[data-testid='tweetPhoto'] img",
        "div[aria-label='Image'] img",
        "img[src^='blob:']",
    ]
    seen: set[str] = set()
    total = 0
    for selector in selectors:
        locator = root.locator(selector)
        try:
            count = locator.count()
        except Exception:
            continue
        for i in range(count):
            try:
                el = locator.nth(i)
                src = el.get_attribute("src", timeout=500) or f"{selector}:{i}"
                key = src
                if key not in seen and el.is_visible(timeout=500):
                    seen.add(key)
                    total += 1
            except Exception:
                pass
    return total


def _locator_count(root, selector: str) -> int:
    try:
        return root.locator(selector).count()
    except Exception:
        return -1


def _visible_locator_count(root, selector: str) -> int:
    try:
        locator = root.locator(selector)
        count = locator.count()
    except Exception:
        return -1
    total = 0
    for index in range(count):
        try:
            if locator.nth(index).is_visible(timeout=200):
                total += 1
        except Exception:
            pass
    return total


def _write_upload_debug_artifacts(page, image_path: Path) -> list[str]:
    artifacts: list[str] = []
    if page is None:
        return artifacts
    try:
        TWITTER_COORD_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = TWITTER_COORD_DIR / f"x_upload_debug_{stamp}_{image_path.stem[:40]}"
        png_path = base.with_suffix(".png")
        html_path = base.with_suffix(".html")
        page.screenshot(path=str(png_path), full_page=True, timeout=5_000)
        artifacts.append(str(png_path))
        html_path.write_text(page.content(), encoding="utf-8", errors="replace")
        artifacts.append(str(html_path))
    except Exception as exc:
        artifacts.append(f"debug_artifacts_failed={exc}")
    return artifacts


def _wait_for_attached_image(root, expected_count: int, *, page=None, editor=None, image_path: Path | None = None) -> None:
    deadline = time.time() + TWITTER_FILE_UPLOAD_TIMEOUT_MS / 1000
    last_count = 0
    while time.time() < deadline:
        last_count = _attached_image_count(root)
        if last_count >= expected_count:
            # Let X finish enabling the post button after the preview appears.
            time.sleep(1)
            return
        time.sleep(1)
    details = [f"scope={last_count}/{expected_count}"]
    file_input_selector = "input[type='file'][accept*='image']"
    media_button_selector = MEDIA_BUTTON_SELECTOR
    if image_path is not None:
        try:
            details.append(f"file_exists={image_path.exists()}")
            details.append(f"file_bytes={image_path.stat().st_size if image_path.exists() else 0}")
        except Exception:
            pass
    try:
        details.append(f"scope_file_inputs={_locator_count(root, file_input_selector)}")
        details.append(f"scope_media_buttons={_visible_locator_count(root, media_button_selector)}")
    except Exception:
        pass
    if editor is not None:
        try:
            strict = _strict_composer_scope(editor)
            if strict is not None:
                details.append(f"strict={_attached_image_count(strict)}")
        except Exception:
            pass
    if page is not None:
        try:
            details.append(f"page={_attached_image_count(page)}")
            details.append(f"page_file_inputs={_locator_count(page, file_input_selector)}")
            details.append(f"page_media_buttons={_visible_locator_count(page, media_button_selector)}")
            details.append(f"url={page.url}")
        except Exception:
            pass
        if image_path is not None:
            artifacts = _write_upload_debug_artifacts(page, image_path)
            if artifacts:
                details.append("debug=" + " | ".join(artifacts))
    raise TimeoutError(f"X image upload non confirmee: {', '.join(details)} preview(s)")


def _post_compose_image_thread(page, posts: list[tuple[str, tuple[Path, ...]]]) -> bool:
    print("X compose: ouverture du composer...", flush=True)
    page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30_000)
    time.sleep(2)

    upload_scopes = []
    for i, (text, image_paths) in enumerate(posts):
        if i > 0:
            print(f"X compose: ajout du post #{i + 1}...", flush=True)
            if not _click_thread_add_button(page):
                print("X bouton d'ajout au thread introuvable.")
                return False
            time.sleep(1)

        print(f"X compose: remplissage post #{i + 1}/{len(posts)}...", flush=True)
        editor = _wait_visible_editor(page, i)
        editor.click(timeout=10_000)
        if text:
            editor.fill(text)
        scope = None
        for image_index, image_path in enumerate(image_paths):
            print(
                f"X compose: upload image {image_index + 1}/{len(image_paths)} "
                f"post #{i + 1}/{len(posts)} ({image_path.name})...",
                flush=True,
            )
            scope = _attach_image_to_composer(page, editor, image_path, i)
        upload_scopes.append(scope)

    for i, (_text, image_paths) in enumerate(posts):
        print(f"X compose: verification image post #{i + 1}/{len(posts)}...", flush=True)
        editor = _wait_visible_editor(page, i, timeout_ms=5_000)
        scope = upload_scopes[i] if i < len(upload_scopes) else None
        if scope is None:
            scope = _strict_composer_scope(editor) or _composer_scope(editor)
        if scope is None:
            print(f"X composer post #{i + 1} introuvable pour verification image")
            return False
        attached = _attached_image_count(scope)
        if attached < len(image_paths):
            print(f"X image absente dans le post #{i + 1}: {attached}/{len(image_paths)}")
            return False

    print("X compose: clic publication du thread...", flush=True)
    page.locator(
        "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
    ).first.click(timeout=10_000)
    expected = "\n".join(text for text, _ in posts if text)
    print("X compose: attente confirmation X...", flush=True)
    return _wait_post_submitted(page, expected, timeout_ms=90_000)


def _launch_once(p, profile_dir: Path, *, headless: bool, args: list[str]):
    try:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            channel="chrome",
            args=args,
        )
    except Exception:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            args=args,
        )


def _short_error(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else exc.__class__.__name__


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _launch(p, profile_dir: Path):
    """Lance un contexte Chrome persistant avec anti-detection."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled"]
    headless = _env_bool("TWITTER_HEADLESS", _env_bool("TSM_HEADLESS", True))
    if os.name != "nt" and not os.getenv("DISPLAY"):
        headless = True

    attempts = max(1, TWITTER_BROWSER_LAUNCH_ATTEMPTS)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            if attempt > 1:
                print(f"X navigateur: tentative {attempt}/{attempts}...", flush=True)
            return _launch_once(p, profile_dir, headless=headless, args=args)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            print(
                f"X navigateur ferme au lancement ({_short_error(exc)}). "
                f"Nouvelle tentative dans {TWITTER_BROWSER_LAUNCH_RETRY_DELAY}s...",
                flush=True,
            )
            time.sleep(TWITTER_BROWSER_LAUNCH_RETRY_DELAY)

    raise last_error


def _load_credentials(session_file: Path) -> dict | None:
    """Lit username/password depuis le fichier de session si presents."""
    try:
        data = json.loads(Path(session_file).read_text(encoding="utf-8-sig"))
        if "username" in data and "password" in data:
            return {
                "username": data["username"],
                "password": data["password"],
                "email":    data.get("email", data["username"]),
            }
    except Exception:
        pass
    return None


def _load_storage_state(session_file: Path) -> dict | None:
    """Lit les cookies/localStorage Playwright si le fichier de session les contient."""
    try:
        data = json.loads(Path(session_file).read_text(encoding="utf-8-sig"))
        if data.get("cookies") or data.get("origins"):
            return {
                "cookies": data.get("cookies", []),
                "origins": data.get("origins", []),
            }
    except Exception:
        pass
    return None


def _restore_storage_state(context, session_file: Path) -> bool:
    state = _load_storage_state(session_file)
    if not state:
        return False
    cookies = state.get("cookies") or []
    if cookies:
        context.add_cookies(cookies)
    return True


def _auto_login(page, username: str, password: str, email: str = ""):
    """Remplit le formulaire de connexion X automatiquement."""
    print("  Auto-login en cours...")
    page.goto("https://x.com/login", wait_until="domcontentloaded")
    time.sleep(2)

    # Champ username
    print("  -> Saisie du username...")
    username_input = page.locator("input[autocomplete='username']")
    try:
        username_input.wait_for(state="visible", timeout=6_000)
    except PlaywrightTimeout:
        username_input = page.locator("input").first
        username_input.wait_for(state="visible", timeout=10_000)
    username_input.fill(username)
    time.sleep(0.5)

    # Bouton Suivant
    try:
        next_btn = page.locator("[data-testid='LoginForm_Login_Button']")
        next_btn.wait_for(state="visible", timeout=5_000)
        next_btn.click()
    except PlaywrightTimeout:
        username_input.press("Enter")
    time.sleep(2)

    # X demande souvent de ressaisir le username (ou email/telephone) avant le mot de passe
    try:
        second_input = page.locator("input[name='text']")
        second_input.wait_for(state="visible", timeout=4_000)
        print("  -> Confirmation email/telephone requise...")
        second_input.fill(email or username)
        page.locator("[data-testid='ocfEnterTextNextButton']").click()
        time.sleep(2)
    except PlaywrightTimeout:
        pass  # Pas d'etape intermediaire

    # Champ password
    print("  -> Saisie du mot de passe...")
    pwd_input = page.locator("input[type='password']").first
    pwd_input.wait_for(state="visible", timeout=10_000)
    pwd_input.fill(password)
    time.sleep(0.5)

    # Bouton Connexion
    try:
        login_btn = page.locator("[data-testid='LoginForm_Login_Button']")
        login_btn.wait_for(state="visible", timeout=5_000)
        login_btn.click()
    except PlaywrightTimeout:
        pwd_input.press("Enter")
    time.sleep(4)

    # Verification que la connexion a reussi
    if "login" in page.url or "accounts" in page.url or "onboarding" in page.url:
        print(f"  ERREUR : Login echoue, URL actuelle : {page.url}")
    else:
        print(f"  Auto-login termine. URL : {page.url}")


def _looks_logged_out(page) -> bool:
    url = page.url
    if "login" in url or "onboarding" in url or "accounts" in url:
        return True
    try:
        if page.locator("input[autocomplete='username']").count():
            return True
        if page.get_by_text("Email or username", exact=True).count():
            return True
    except Exception:
        pass
    return False


def setup_session(session_file: Path):
    """Ouvre Chrome et connecte automatiquement si credentials disponibles, sinon manuellement."""
    session_file = Path(session_file)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = _profile_dir(session_file)
    credentials = _load_credentials(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        _restore_storage_state(context, session_file)
        page = context.new_page()
        if credentials:
            _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
        else:
            page.goto("https://x.com/login", wait_until="domcontentloaded")
            print("\nConnecte-toi a Twitter/X dans le navigateur.")
            input("-> Appuie sur ENTREE une fois connecte et arrive sur l'accueil X : ")
        context.close()
    print(f"OK Session sauvegardee dans : {profile_dir}")


def post_thread(tweets: list[str], session_file: Path) -> bool:
    if not tweets:
        print("Aucun tweet a poster.")
        return False
    if not _validate_tweet_lengths(tweets, label="thread texte"):
        return False

    session_file = Path(session_file)
    profile_dir  = _profile_dir(session_file)

    with _twitter_account_slot(session_file) as account_key:
        # Premiere utilisation : creer la session (le dossier Default indique que Chrome a bien tourne)
        if not (profile_dir / "Default").exists() and not _load_storage_state(session_file):
            print("Aucun profil Twitter trouve. Connexion initiale requise...")
            setup_session(session_file)

        with sync_playwright() as p:
            context = None
            print(f"\nPublication de {len(tweets)} tweet(s)...")

            try:
                context = _launch(p, profile_dir)
                _restore_storage_state(context, session_file)
                page    = context.new_page()

                page.goto("https://x.com/home", wait_until="domcontentloaded")
                time.sleep(2)

                if _looks_logged_out(page):
                    print("Session expiree. Reconnexion automatique...")
                    credentials = _load_credentials(session_file)
                    if credentials:
                        _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                    else:
                        context.close()
                        setup_session(session_file)
                        context = _launch(p, profile_dir)
                        page    = context.new_page()
                    page.goto("https://x.com/home", wait_until="domcontentloaded")
                    time.sleep(2)

                success = True
                try:
                    _wait_account_spacing(account_key)
                    if len(tweets) > 1:
                        success = _post_compose_text_thread(page, tweets)
                        if success:
                            _mark_account_posted(account_key)
                            print(f"OK Thread de {len(tweets)} posts publie")
                    else:
                        for i, tweet in enumerate(tweets, 1):
                            page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
                            editor = _open_compose_and_wait_editor(page, session_file, 0)
                            editor.click(timeout=10_000)
                            editor.fill(tweet)
                            time.sleep(1)
                            page.locator(
                                "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
                            ).first.click(timeout=10_000)
                            if not _wait_post_submitted(page, tweet):
                                success = False
                                break
                            _mark_account_posted(account_key)
                            print(f"OK Tweet {i}/{len(tweets)} publie")

                except Exception as e:
                    print(f"X Erreur publication: {e}")
                    success = False

            finally:
                if context is not None:
                    context.close()

            return success


def post_with_image(tweet: str, image_path: Path, session_file: Path, *, skip_if=None) -> bool:
    """Post a single tweet with one image attached.

    skip_if : callable optionnelle re-évaluée APRES l'acquisition du slot de compte.
    Si elle renvoie True (ex.: posted.lock créé entre-temps par un autre process qui
    tenait le slot), le post est annulé et la fonction renvoie True (rien à refaire).
    C'est la protection anti-double-post inter-process (incident global/us 17/07/2026 :
    deux run_all_charts.py concurrents, le second postait après avoir attendu le slot).
    """
    _set_last_post_error("")
    session_file = Path(session_file)
    image_path   = Path(image_path)
    profile_dir  = _profile_dir(session_file)

    if not _validate_tweet_lengths([tweet], label="post avec image"):
        return False

    if not image_path.exists():
        print(f"X image introuvable: {image_path}")
        return False

    print("X: attente du slot de post (verrou compte)...", flush=True)
    with _twitter_account_slot(session_file) as account_key:
        if skip_if is not None:
            try:
                already = bool(skip_if())
            except Exception as exc:
                print(f"X skip_if en erreur (ignore): {exc}")
                already = False
            if already:
                print("X: deja poste par un autre process (detecte apres le slot) — post annule")
                return True
        if not (profile_dir / "Default").exists():
            print("Aucun profil Twitter trouve. Connexion initiale requise...")
            setup_session(session_file)

        print("X: ouverture du navigateur...", flush=True)
        with sync_playwright() as p:
            context = None
            try:
                context = _launch(p, profile_dir)
                _restore_storage_state(context, session_file)
                page    = context.new_page()

                page.goto("https://x.com/home", wait_until="domcontentloaded")
                time.sleep(2)

                if _looks_logged_out(page):
                    print("Session expiree. Reconnexion automatique...")
                    credentials = _load_credentials(session_file)
                    if credentials:
                        _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                    else:
                        context.close()
                        setup_session(session_file)
                        context = _launch(p, profile_dir)
                        page    = context.new_page()
                    page.goto("https://x.com/home", wait_until="domcontentloaded")
                    time.sleep(2)

                _wait_account_spacing(account_key)
                page.goto("https://x.com/compose/post", wait_until="domcontentloaded")

                editor = _open_compose_and_wait_editor(page, session_file, 0)
                editor.click(timeout=10_000)
                editor.fill(tweet)
                attach_scope = _attach_image_to_composer(page, editor, image_path, 0)
                if _attached_image_count(attach_scope) < 1:
                    raise RuntimeError("image absente du composer juste avant le post — abandon")

                page.locator(
                    "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
                ).first.click(timeout=10_000)
                if not _wait_post_submitted(page, tweet):
                    _set_last_post_error("post non confirme apres clic")
                    return False

                _mark_account_posted(account_key)
                print("OK Tweet avec image publie")
                return True

            except Exception as e:
                _set_last_post_error(str(e))
                print(f"X Erreur post_with_image: {e}")
                return False

            finally:
                if context is not None:
                    context.close()


def schedule_post(
    tweet: str,
    scheduled_at: datetime | str,
    session_file: Path,
    image_path: Path | None = None,
) -> bool:
    """Schedule a single native X post through the web UI."""
    session_file = Path(session_file)
    profile_dir = _profile_dir(session_file)
    tweet = str(tweet or "").strip()

    if not tweet and image_path is None:
        print("Aucun contenu a programmer.")
        return False
    if not _validate_tweet_lengths([tweet], label="post programme"):
        return False

    image_path = Path(image_path) if image_path else None
    if image_path is not None and not image_path.exists():
        print(f"X image introuvable: {image_path}")
        return False

    try:
        scheduled_dt = _coerce_schedule_datetime(scheduled_at)
    except ValueError as exc:
        print(f"X date programmation invalide: {exc}")
        return False

    with _twitter_account_slot(session_file) as account_key:
        if not (profile_dir / "Default").exists() and not _load_storage_state(session_file):
            print("Aucun profil Twitter trouve. Connexion initiale requise...")
            setup_session(session_file)

        with sync_playwright() as p:
            context = None
            try:
                context = _launch(p, profile_dir)
                _restore_storage_state(context, session_file)
                page = context.new_page()

                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30_000)
                time.sleep(2)

                if _looks_logged_out(page):
                    print("Session expiree. Reconnexion automatique...")
                    credentials = _load_credentials(session_file)
                    if credentials:
                        _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                    else:
                        context.close()
                        setup_session(session_file)
                        context = _launch(p, profile_dir)
                        page = context.new_page()
                    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(2)

                _wait_account_spacing(account_key)
                page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=30_000)

                editor = _open_compose_and_wait_editor(page, session_file, 0)
                editor.click(timeout=10_000)
                if tweet:
                    editor.fill(tweet)
                if image_path is not None:
                    attach_scope = _attach_image_to_composer(page, editor, image_path, 0)
                    if _attached_image_count(attach_scope) < 1:
                        raise RuntimeError("image absente du composer juste avant la programmation — abandon")

                _set_schedule_dialog(page, scheduled_dt)
                page.locator(
                    "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
                ).first.click(timeout=10_000)
                if not _wait_post_scheduled(page, tweet):
                    return False

                _mark_account_posted(account_key)
                print(f"OK Post programme pour {scheduled_dt:%Y-%m-%d %H:%M}")
                return True

            except Exception as e:
                print(f"X Erreur schedule_post: {e}")
                return False

            finally:
                if context is not None:
                    context.close()


def post_image_thread(posts: list[tuple[str, Path | list[Path] | tuple[Path, ...]]], session_file: Path) -> bool:
    """Post a native X thread where each post has one or more images attached."""
    normalized_posts: list[tuple[str, tuple[Path, ...]]] = []
    for text, image_paths in posts:
        if isinstance(image_paths, (list, tuple)):
            paths = tuple(Path(path) for path in image_paths if path)
        else:
            paths = (Path(image_paths),) if image_paths else ()
        if paths:
            normalized_posts.append((str(text or "").strip(), paths))
    posts = normalized_posts
    if not posts:
        print("Aucun post image a publier.")
        return False
    if not _validate_tweet_lengths([text for text, _ in posts], label="thread image"):
        return False
    missing = [image_path for _, image_paths in posts for image_path in image_paths if not image_path.exists()]
    if missing:
        print(f"X image introuvable: {missing[0]}")
        return False

    session_file = Path(session_file)
    profile_dir = _profile_dir(session_file)

    if not (profile_dir / "Default").exists() and not _load_storage_state(session_file):
        print("Aucun profil Twitter trouve. Connexion initiale requise...")
        setup_session(session_file)

    print("X thread: acquisition du slot compte...", flush=True)
    with _twitter_account_slot(session_file) as account_key:
        print("X thread: slot compte acquis", flush=True)
        with sync_playwright() as p:
            context = None
            try:
                context = _launch(p, profile_dir)
                _restore_storage_state(context, session_file)
                page = context.new_page()
                print(f"\nPublication d'un thread de {len(posts)} post(s) avec image...")

                print("X thread: ouverture home...", flush=True)
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30_000)
                print(f"X thread: home chargee ({page.url})", flush=True)
                time.sleep(2)

                if _looks_logged_out(page):
                    print("Session expiree. Reconnexion automatique...")
                    credentials = _load_credentials(session_file)
                    if credentials:
                        _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                    else:
                        context.close()
                        setup_session(session_file)
                        context = _launch(p, profile_dir)
                        page = context.new_page()
                    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(2)

                _wait_account_spacing(account_key)
                ok = _post_compose_image_thread(page, posts)
                if ok:
                    _mark_account_posted(account_key)
                    print(f"OK Thread image de {len(posts)} posts publie")
                return ok

            except Exception as e:
                print(f"X Erreur post_image_thread: {e}")
                return False

            finally:
                if context is not None:
                    context.close()


def split_tweets(content: str, max_len: int = 280) -> list[str]:
    if len(content) <= max_len:
        return [content]

    tweets  = []
    current = ""

    for section in content.split("\n\n"):
        candidate = (current + "\n\n" + section).strip()
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                tweets.append(current)
            current = section

    if current:
        tweets.append(current)

    return tweets
