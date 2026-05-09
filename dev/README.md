# Dev Scripts

This folder contains one-off scripts, verification helpers, and local artifacts
that are useful during development but are not part of the daily production
pipeline.

## Folders

- `adhoc/checks/`: small CSV/JSON inspection scripts.
- `adhoc/verify/`: verification scripts for ranking, display, and formulas.
- `adhoc/debug/`: quick local debug scripts and exploratory prints.
- `maintenance/`: manual repair or regeneration scripts.
- `artifacts/`: local logs and throwaway files moved out of the repo root.

Production collectors should live under `collectors/` and reusable operational
tools should live under `scripts/`.
