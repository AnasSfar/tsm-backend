#!/usr/bin/env python3
"""Téléchargement CSV Spotify Charts — partagé Fr + Global."""
import re
import sys
import subprocess
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_date_arg(s: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise ValueError(f"Format invalide : {s} (attendu YYYY-MM-DD)")
    return date.fromisoformat(s)


def get_downloaded_dates(root: Path, chart_id: str) -> set:
    return {
        "-".join(p.stem.split("-")[-3:])
        for p in root.rglob(f"{chart_id}-*.csv")
    }


def get_dest(root: Path, chart_id: str, d: date) -> Path:
    dest = root / str(d)[:4] / str(d)[5:7] / str(d)
    dest.mkdir(parents=True, exist_ok=True)
    return dest / f"{chart_id}-{d}.csv"


def run_filter(script: Path, d: date, cwd: Path):
    if not script.exists():
        print(f"  ⚠ Script filtre introuvable : {script}")
        return
    print(f"  → Traitement {d}...", end="", flush=True)
    result = subprocess.run(
        [sys.executable, str(script), str(d)],
        capture_output=True, text=True, cwd=str(cwd)
    )
    if result.returncode == 0:
        print(" ✓ filtré")
    else:
        print(f" ⚠ filtre échoué :\n{result.stderr.strip()}")


def run_filter_all(script: Path, cwd: Path):
    """Lance filter.py --all (multiprocessing) pour traiter tous les CSV en attente."""
    if not script.exists():
        print(f"  ⚠ Script filtre introuvable : {script}")
        return
    print(f"\n  → Traitement de tous les CSV (filter.py --all)...", flush=True)
    result = subprocess.run(
        [sys.executable, str(script), "--all"],
        text=True, cwd=str(cwd)
    )
    if result.returncode != 0:
        print(f"  ⚠ filter --all a échoué (code {result.returncode})")


def run_rebuild(script: Path, cwd: Path):
    if not script.exists():
        return
    print(f"\n  → Reconstruction de ts_history.json...", end="", flush=True)
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=str(cwd)
    )
    if result.returncode == 0:
        print(" ✓")
    else:
        print(f" ⚠ {result.stderr.strip()}")


def download_charts(root: Path, chart_id: str, session_file: Path,
                    filter_script: Path, rebuild_script: Path,
                    start: date, end: date):
    already = get_downloaded_dates(root, chart_id)
    dates_to_fetch = [d for d in date_range(start, end) if str(d) not in already][::-1]

    if not dates_to_fetch:
        print("Tous les fichiers sont déjà téléchargés.")
        return

    print(f"{len(dates_to_fetch)} fichiers à télécharger ({start} → {end})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if session_file.exists():
            context = browser.new_context(accept_downloads=True, storage_state=str(session_file))
            print("Session Spotify chargée.")
        else:
            context = browser.new_context(accept_downloads=True)

        page = context.new_page()
        print("Ouverture de Spotify Charts...")
        page.goto("https://charts.spotify.com/home", wait_until="domcontentloaded")

        if "login" in page.url or "accounts.spotify.com" in page.url:
            print("\n⚠  Connexion Spotify requise.")
            input("   → Connecte-toi puis appuie sur ENTRÉE : ")
            context.storage_state(path=str(session_file))
            print("✓ Session sauvegardée.\n")

        print("Connecté. Début des téléchargements...\n")
        success, failed = 0, []

        bulk = len(dates_to_fetch) > 1

        for i, d in enumerate(dates_to_fetch, 1):
            url = f"https://charts.spotify.com/charts/view/{chart_id}/{d}"
            dest = get_dest(root, chart_id, d)
            print(f"[{i}/{len(dates_to_fetch)}] {d} ... ", end="", flush=True)

            try:
                page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                page.wait_for_selector("button[aria-labelledby='csv_download']", timeout=30_000)
                btn = page.locator("button[aria-labelledby='csv_download']").first
                with page.expect_download(timeout=15_000) as dl_info:
                    btn.click(timeout=10_000)
                dl_info.value.save_as(dest)
                print("✓")
                success += 1
                if not bulk:
                    run_filter(filter_script, d, root)

            except Exception as e:
                print(f"✗ {e}")
                failed.append(str(d))

        browser.close()

    if success > 0:
        if bulk:
            run_filter_all(filter_script, root)
        run_rebuild(rebuild_script, root)

    print(f"\n{'='*45}")
    print(f"Terminé : {success}/{len(dates_to_fetch)}")
    if failed:
        print(f"Échecs : {', '.join(failed)}")
