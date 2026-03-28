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