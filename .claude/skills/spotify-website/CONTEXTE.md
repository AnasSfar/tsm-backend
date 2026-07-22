# Contexte collectors/spotify/website

## Role

`collectors/spotify/website` contains legacy website/data artifacts:

```text
collectors/spotify/website/data/
collectors/spotify/website/site/
```

Modern collectors normally write/read through helpers in
`collectors/spotify/core/data_paths.py`.

## Guardrail

Do not treat this as the active TSM frontend. For frontend work, use:

```text
C:\Users\sfara\Documents\GitHub\tsm-frontend
```

Do not hand-edit generated static data unless the user explicitly asks for a
legacy/static data correction and the source pipeline has been checked.

## Related Paths

Modern exports:

```text
runtime/exports/web/site/data/
runtime/exports/web/site/history/
```

Legacy fallbacks:

```text
website/site/data/
website/site/history/
collectors/spotify/website/
```

## Verification

Before changing this area, confirm whether the task is:

- legacy static website support;
- generated data investigation;
- migration/fallback cleanup.

If it is normal UI/frontend work, switch to `tsm-frontend`.
