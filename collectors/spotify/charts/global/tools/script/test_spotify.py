from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://charts.spotify.com", timeout=120000, wait_until="load")
    print(page.title())
    input("Appuie sur Entrée pour fermer...")
    browser.close()