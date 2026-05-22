# tsm-backend

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
