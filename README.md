# tsm-backend

## Main commands

The safe entrypoint for day-to-day work is:

```bash
python -m tsm daily --no-post
python -m tsm collect streams --no-post
python -m tsm collect charts --no-post
python -m tsm collect apple-music
python -m tsm export web --date YYYY-MM-DD
python -m tsm audit data --write
python -m tsm migrate layout --dry-run
```

Current layout rules:

- Collector code lives under `collectors/`.
- Source-of-truth business data lives under `db/`.
- Dated collector outputs live under `snapshots/`.
- Generated web/R2 exports live under `runtime/exports/web/`.
- `website/` is legacy and should not receive new export writes.

See `docs/runbook.md` for migration and safety notes.

## Apple Music collector quality checks

Run Apple Music unit tests locally:

```bash
python -m unittest discover -s collectors/apple_music/tests -p "test_*.py"
```

Optional environment variables for HTTP resiliency tuning:

- `APPLE_MUSIC_TIMEOUT` (default: `20`)
- `APPLE_MUSIC_RETRY_TOTAL` (default: `3`)
- `APPLE_MUSIC_RETRY_BACKOFF` (default: `1.0`)

PowerShell example with custom values:

```powershell
$env:APPLE_MUSIC_TIMEOUT = "30"
$env:APPLE_MUSIC_RETRY_TOTAL = "5"
$env:APPLE_MUSIC_RETRY_BACKOFF = "0.5"
python -m unittest discover -s collectors/apple_music/tests -p "test_*.py"
```

CI coverage for this collector is defined in:

- `.github/workflows/apple-music-tests.yml`

## R2 storage warnings

`scripts/check_r2_storage.py` checks R2 bucket storage metrics and sends `ntfy`
warnings when a bucket crosses its configured soft limit. A daily GitHub
workflow is defined in `.github/workflows/check-r2-storage.yml`.

Required GitHub secret:

- `CLOUDFLARE_ANALYTICS_API_TOKEN` with Cloudflare Account Analytics Read

Useful GitHub variables:

- `R2_STORAGE_BUCKET_LIMITS`, for example `taylor-data=9GB,taylor-app=1GB`
- `R2_STORAGE_WARNING_PERCENT`, default `80`
- `NTFY_TOPIC_R2_STORAGE`, default `taylormuseum-r2`

Local dry-run:

```bash
python scripts/check_r2_storage.py --dry-run
```
