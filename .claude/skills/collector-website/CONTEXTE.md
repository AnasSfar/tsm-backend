# Contexte collectors/website

## Role

`collectors/website` currently contains legacy/static website artifacts:

```text
collectors/website/site/
```

This is not the active React/Vite frontend.

## Guardrail

When the user says "frontend" for TSM, work in:

```text
C:\Users\sfara\Documents\GitHub\tsm-frontend
```

React/Vite UI:

```text
C:\Users\sfara\Documents\GitHub\tsm-frontend\frontend
```

Frontend API:

```text
C:\Users\sfara\Documents\GitHub\tsm-frontend\api
```

Do not edit:

```text
C:\Users\sfara\Documents\GitHub\tsm-backend\website
```

unless the user explicitly asks for:

- `website/`;
- the legacy static site;
- `website/site/data`;
- `website/site/history`;
- generated static data.

## Related Paths

Modern backend exports usually go to:

```text
runtime/exports/web/site/data/
runtime/exports/web/site/history/
```

Legacy fallback paths still appear in code:

```text
website/site/data/
website/site/history/
```

Use `collectors/spotify/core/data_paths.py` helpers instead of hardcoding when a
collector already has modern/legacy path abstractions.

## Verification

Before modifying this area, confirm the user intended legacy website/static data
work. For generated data, verify the source pipeline rather than hand-editing
outputs.
