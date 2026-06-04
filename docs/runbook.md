# TSM Backend Runbook

## Canonical Layout

- `collectors/`: collector source code only.
- `db/`: small business data and source-of-truth CSV/JSON files.
- `snapshots/`: dated operational snapshots written by collectors.
- `runtime/exports/web/`: generated web/API export payloads before R2 upload.
- `runtime/cache/`, `runtime/logs/`, `runtime/social/`: local runtime artifacts ignored by Git.
- `data/_archive/`: migration archives and compatibility data.
- `website/`: legacy static site, no longer a target for new export writes.

## Main Commands

```powershell
python -m tsm daily --no-post
python -m tsm collect streams --no-post
python -m tsm collect charts --no-post
python -m tsm collect apple-music
python -m tsm export web --date 2026-06-03
python -m tsm audit data --write
python -m tsm migrate layout --dry-run
```

Use `--apply` for `migrate layout` only after reviewing `docs/layout-migration-dry-run.csv`.
The migration command archives copies with SHA-256 verification; it does not delete source files.

## Safety Rules

- Do not delete `website/` until exports in `runtime/exports/web/` and R2 keys have been validated.
- Do not remove `.bak` files until they are archived under `data/_archive/manual-backups/` and checksums match.
- Keep old wrappers temporarily; they delegate to `python -m tsm`.
- Treat `collectors/spotify/core/data_paths.py` as the source of truth for paths.
