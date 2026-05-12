"""
Rafraîchit le fichier de session Spotify (spotify_session.json).
Lance un navigateur visible → connecte-toi → appuie sur ENTRÉE → session sauvegardée.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_FILE = (
    Path(__file__).parent.parent
    / "collectors/spotify/charts/global/tools/json/spotify_session.json"
)

def main():
    print(f"Session cible : {SESSION_FILE}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--no-proxy-server"],
        )

        if SESSION_FILE.exists():
            ctx = browser.new_context(storage_state=str(SESSION_FILE))
            print("Session existante chargée — vérification…")
        else:
            ctx = browser.new_context()

        page = ctx.new_page()
        page.goto("https://open.spotify.com", wait_until="domcontentloaded")

        if "accounts.spotify.com" in page.url or "login" in page.url:
            print("⚠  Non connecté. Connecte-toi dans le navigateur.")
        else:
            print("✓ Déjà connecté.")

        input("\nAppuie sur ENTRÉE une fois connecté pour sauvegarder la session… ")
        ctx.storage_state(path=str(SESSION_FILE))
        print(f"✓ Session sauvegardée → {SESSION_FILE}")
        browser.close()

if __name__ == "__main__":
    main()
