#!/usr/bin/env python3
"""Post Twitter via Playwright (profil Chrome persistant) — partage Fr + Global."""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def _profile_dir(session_file: Path) -> Path:
    """Dossier du profil Chrome persistant, a cote du fichier de session."""
    return Path(session_file).parent / "chrome_profile"


def _launch(p, profile_dir: Path):
    """Lance un contexte Chrome persistant avec anti-detection."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled"]
    try:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            channel="chrome",
            args=args,
        )
    except Exception:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=args,
        )


def _load_credentials(session_file: Path) -> dict | None:
    """Lit username/password depuis le fichier de session si presents."""
    try:
        data = json.loads(Path(session_file).read_text(encoding="utf-8"))
        if "username" in data and "password" in data:
            return {
                "username": data["username"],
                "password": data["password"],
                "email":    data.get("email", data["username"]),
            }
    except Exception:
        pass
    return None


def _auto_login(page, username: str, password: str, email: str = ""):
    """Remplit le formulaire de connexion X automatiquement."""
    print("  Auto-login en cours...")
    page.goto("https://x.com/login", wait_until="domcontentloaded")
    time.sleep(2)

    # Champ username
    print("  -> Saisie du username...")
    username_input = page.locator("input[autocomplete='username']")
    username_input.wait_for(state="visible", timeout=10_000)
    username_input.fill(username)
    time.sleep(0.5)

    # Bouton Suivant
    next_btn = page.locator("[data-testid='LoginForm_Login_Button']")
    next_btn.wait_for(state="visible", timeout=5_000)
    next_btn.click()
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
    pwd_input = page.locator("input[type='password']")
    pwd_input.wait_for(state="visible", timeout=10_000)
    pwd_input.fill(password)
    time.sleep(0.5)

    # Bouton Connexion
    login_btn = page.locator("[data-testid='LoginForm_Login_Button']")
    login_btn.wait_for(state="visible", timeout=5_000)
    login_btn.click()
    time.sleep(4)

    # Verification que la connexion a reussi
    if "login" in page.url or "accounts" in page.url:
        print(f"  ERREUR : Login echoue, URL actuelle : {page.url}")
    else:
        print(f"  Auto-login termine. URL : {page.url}")


def setup_session(session_file: Path):
    """Ouvre Chrome et connecte automatiquement si credentials disponibles, sinon manuellement."""
    session_file = Path(session_file)
    session_file.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = _profile_dir(session_file)
    credentials = _load_credentials(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
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
    if not (profile_dir / "Default").exists():
        print("Aucun profil Twitter trouve. Connexion initiale requise...")
        setup_session(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        page    = context.new_page()
        print(f"\nPublication de {len(tweets)} tweet(s)...")
        previous_url = None

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

            success = True
            for i, tweet in enumerate(tweets, 1):
                try:
                    if previous_url and "/status/" in previous_url:
                        page.goto(previous_url, wait_until="domcontentloaded")
                        time.sleep(2)
                        page.locator("[data-testid='reply']").first.click(timeout=10_000)
                    else:
                        page.goto("https://x.com/compose/post", wait_until="domcontentloaded")

                    time.sleep(2)
                    editor = page.locator("[data-testid='tweetTextarea_0']").first
                    editor.click(timeout=10_000)
                    editor.fill(tweet)
                    time.sleep(1)
                    page.locator(
                        "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
                    ).first.click(timeout=10_000)
                    try:
                        page.wait_for_url("**status**", timeout=8_000)
                    except PlaywrightTimeout:
                        time.sleep(3)
                    previous_url = page.url
                    print(f"OK Tweet {i}/{len(tweets)} publie")

                except Exception as e:
                    print(f"X Erreur tweet {i}: {e}")
                    success = False
                    break

        finally:
            context.close()

        return success


def post_with_image(tweet: str, image_path: Path, session_file: Path) -> bool:
    """Post a single tweet with one image attached."""
    session_file = Path(session_file)
    image_path   = Path(image_path)
    profile_dir  = _profile_dir(session_file)

    if not (profile_dir / "Default").exists():
        print("Aucun profil Twitter trouvé. Connexion initiale requise...")
        setup_session(session_file)

    with sync_playwright() as p:
        context = _launch(p, profile_dir)
        page    = context.new_page()
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            time.sleep(2)

            if "login" in page.url:
                print("Session expirée. Reconnexion automatique...")
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

            page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
            time.sleep(2)

            # Attach image via hidden file input
            file_input = page.locator("input[type='file'][accept*='image']").first
            file_input.set_input_files(str(image_path))
            time.sleep(3)

            # Add tweet text
            editor = page.locator("[data-testid='tweetTextarea_0']").first
            editor.click(timeout=10_000)
            editor.fill(tweet)
            time.sleep(1)

            # Post
            page.locator(
                "[data-testid='tweetButton'], [data-testid='tweetButtonInline']"
            ).first.click(timeout=10_000)
            try:
                page.wait_for_url("**status**", timeout=8_000)
            except PlaywrightTimeout:
                time.sleep(3)

            print("OK Tweet avec image publié")
            return True

        except Exception as e:
            print(f"X Erreur post_with_image: {e}")
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
