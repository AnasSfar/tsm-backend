#!/usr/bin/env python3
"""Post Twitter via Playwright (profil Chrome persistant) - partage Fr + Global."""
import json
import hashlib
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

TWITTER_COORD_DIR = Path(tempfile.gettempdir()) / "tsm_twitter_posts"
TWITTER_COORD_LOCK = TWITTER_COORD_DIR / "coordinator.lock"
TWITTER_POST_LOCK_TIMEOUT = 30 * 60
TWITTER_ACCOUNT_SPACING_SECONDS = int(os.getenv("TWITTER_ACCOUNT_SPACING_SECONDS", "180"))
TWITTER_MAX_ACTIVE_ACCOUNTS = int(os.getenv("TWITTER_MAX_ACTIVE_ACCOUNTS", "2"))
TWITTER_FILE_UPLOAD_TIMEOUT_MS = int(os.getenv("TWITTER_FILE_UPLOAD_TIMEOUT_MS", "120000"))


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
            if time.time() - start > timeout:
                try:
                    max_age = stale_after or timeout
                    if time.time() - path.stat().st_mtime > max_age:
                        path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                raise TimeoutError(f"Twitter post lock timeout: {path}")
            time.sleep(2)
    return fd


@contextmanager
def _coordinator_lock(timeout: int = TWITTER_POST_LOCK_TIMEOUT):
    fd = _exclusive_file(TWITTER_COORD_LOCK, timeout=timeout, stale_after=60)
    try:
        yield
    finally:
        os.close(fd)
        try:
            TWITTER_COORD_LOCK.unlink()
        except FileNotFoundError:
            pass


def _active_account_markers() -> list[Path]:
    return [path for path in TWITTER_COORD_DIR.glob("active_*.lock") if path.is_file()]


def _acquire_active_account(account_key: str, *, timeout: int) -> Path:
    marker = TWITTER_COORD_DIR / f"active_{account_key}.lock"
    start = time.time()
    while True:
        with _coordinator_lock(timeout=timeout):
            active = _active_account_markers()
            for active_marker in active:
                try:
                    if time.time() - active_marker.stat().st_mtime > timeout:
                        active_marker.unlink()
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
    account_fd = _exclusive_file(account_lock, timeout=timeout, stale_after=60)
    active_marker = None
    try:
        active_marker = _acquire_active_account(account_key, timeout=timeout)
        yield account_key
    finally:
        if active_marker is not None:
            try:
                active_marker.unlink()
            except FileNotFoundError:
                pass
        os.close(account_fd)
        try:
            account_lock.unlink()
        except FileNotFoundError:
            pass


def _clean_editor_text(text: str) -> str:
    """Normalize the text Playwright reads from X's contenteditable composer."""
    return (
        (text or "")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .strip()
    )


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


def _wait_visible_editor(page, index: int = 0, timeout_ms: int = 15_000):
    editor = page.locator(f"[data-testid='tweetTextarea_{index}']").first
    editor.wait_for(state="visible", timeout=timeout_ms)
    return editor


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


def _composer_scope(editor):
    for depth in range(1, 16):
        scope = editor.locator(f"xpath=ancestor::div[{depth}]")
        try:
            if (
                scope.locator("input[type='file'][accept*='image']").count()
                or scope.locator("[aria-label='Add photos or video'], [aria-label='Ajouter des photos ou une vidéo'], [aria-label='Ajouter des photos ou une video']").count()
            ):
                return scope
        except Exception:
            pass
    return None


def _media_button_candidates(root):
    selectors = [
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


def _attach_image_to_composer(page, editor, image_path: Path, index: int = 0) -> None:
    scope = _composer_scope(editor)
    root = scope or page
    before = _attached_image_count(root)
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
    _wait_for_attached_image(root, before + 1)


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


def _wait_for_attached_image(root, expected_count: int) -> None:
    deadline = time.time() + TWITTER_FILE_UPLOAD_TIMEOUT_MS / 1000
    last_count = 0
    while time.time() < deadline:
        last_count = _attached_image_count(root)
        if last_count >= expected_count:
            # Let X finish enabling the post button after the preview appears.
            time.sleep(1)
            return
        time.sleep(1)
    raise TimeoutError(f"X image upload non confirmee: {last_count}/{expected_count} preview(s)")


def _post_compose_image_thread(page, posts: list[tuple[str, Path]]) -> bool:
    page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
    time.sleep(2)

    for i, (text, image_path) in enumerate(posts):
        if i > 0:
            if not _click_thread_add_button(page):
                print("X bouton d'ajout au thread introuvable.")
                return False
            time.sleep(1)

        editor = _wait_visible_editor(page, i)
        editor.click(timeout=10_000)
        if text:
            editor.fill(text)
        _attach_image_to_composer(page, editor, image_path, i)

    for i, _ in enumerate(posts):
        editor = _wait_visible_editor(page, i, timeout_ms=5_000)
        scope = _composer_scope(editor)
        attached = _attached_image_count(scope or page)
        if attached < 1:
            print(f"X image absente dans le post #{i + 1}")
            return False

    page.locator(
        "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
    ).first.click(timeout=10_000)
    expected = "\n".join(text for text, _ in posts if text)
    return _wait_post_submitted(page, expected, timeout_ms=90_000)


def _launch(p, profile_dir: Path):
    """Lance un contexte Chrome persistant avec anti-detection."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled"]
    headless = os.getenv("TWITTER_HEADLESS", "").strip().lower() in {"1", "true", "yes", "on"}
    if os.name != "nt" and not os.getenv("DISPLAY"):
        headless = True
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

    session_file = Path(session_file)
    profile_dir  = _profile_dir(session_file)

    # Premiere utilisation : creer la session (le dossier Default indique que Chrome a bien tourne)
    if not (profile_dir / "Default").exists() and not _load_storage_state(session_file):
        print("Aucun profil Twitter trouve. Connexion initiale requise...")
        setup_session(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        _restore_storage_state(context, session_file)
        page    = context.new_page()
        print(f"\nPublication de {len(tweets)} tweet(s)...")

        try:
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
            with _twitter_account_slot(session_file) as account_key:
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
                            time.sleep(2)
                            if _looks_logged_out(page):
                                print("Session expiree. Reconnexion automatique...")
                                credentials = _load_credentials(session_file)
                                if credentials:
                                    _auto_login(page, credentials["username"], credentials["password"], credentials.get("email", ""))
                                    page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
                                    time.sleep(2)
                            editor = page.locator("[data-testid='tweetTextarea_0']").first
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
            context.close()

        return success


def post_with_image(tweet: str, image_path: Path, session_file: Path) -> bool:
    """Post a single tweet with one image attached."""
    session_file = Path(session_file)
    image_path   = Path(image_path)
    profile_dir  = _profile_dir(session_file)

    if not image_path.exists():
        print(f"X image introuvable: {image_path}")
        return False

    if not (profile_dir / "Default").exists():
        print("Aucun profil Twitter trouve. Connexion initiale requise...")
        setup_session(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        _restore_storage_state(context, session_file)
        page    = context.new_page()
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            time.sleep(2)

            if "login" in page.url:
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

            with _twitter_account_slot(session_file) as account_key:
                _wait_account_spacing(account_key)
                page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
                time.sleep(2)

                editor = page.locator("[data-testid='tweetTextarea_0']").first
                editor.click(timeout=10_000)
                editor.fill(tweet)
                _attach_image_to_composer(page, editor, image_path, 0)

                page.locator(
                    "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
                ).first.click(timeout=10_000)
                if not _wait_post_submitted(page, tweet):
                    return False

                _mark_account_posted(account_key)
                print("OK Tweet avec image publie")
                return True

        except Exception as e:
            print(f"X Erreur post_with_image: {e}")
            return False

        finally:
            context.close()


def post_image_thread(posts: list[tuple[str, Path]], session_file: Path) -> bool:
    """Post a native X thread where each post has one image attached."""
    posts = [(str(text or "").strip(), Path(image_path)) for text, image_path in posts]
    posts = [(text, image_path) for text, image_path in posts if image_path]
    if not posts:
        print("Aucun post image a publier.")
        return False
    missing = [image_path for _, image_path in posts if not image_path.exists()]
    if missing:
        print(f"X image introuvable: {missing[0]}")
        return False

    session_file = Path(session_file)
    profile_dir = _profile_dir(session_file)

    if not (profile_dir / "Default").exists() and not _load_storage_state(session_file):
        print("Aucun profil Twitter trouve. Connexion initiale requise...")
        setup_session(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        _restore_storage_state(context, session_file)
        page = context.new_page()
        print(f"\nPublication d'un thread de {len(posts)} post(s) avec image...")

        try:
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
                    page = context.new_page()
                page.goto("https://x.com/home", wait_until="domcontentloaded")
                time.sleep(2)

            with _twitter_account_slot(session_file) as account_key:
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
