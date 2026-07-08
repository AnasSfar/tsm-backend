## Repo map

`REPO_CONTEXT.md` (repo root) is the full annotated tree of this repo: every folder/file's role and each script's CLI options. Read the relevant section there before exploring `collectors/` or `scripts/` with search tools.

## Living documentation (mandatory)

After ANY change, update the impacted docs in the same session — stale docs are worse than no docs:
- Script added/modified/moved, or its CLI options changed → update `REPO_CONTEXT.md`.
- Workflow, data/posting rule, or convention changed → update the matching skill in `.claude/skills/`.
- New product decision that isn't written anywhere → write it into the closest skill or context doc.
Never end a session leaving a context file that contradicts the code.

## TSM Frontend Rule

When the user says "frontend" for TSM, always work in:
`C:\Users\sfara\Documents\GitHub\tsm-frontend`

React/Vite UI lives in:
`C:\Users\sfara\Documents\GitHub\tsm-frontend\frontend`

Frontend API lives in:
`C:\Users\sfara\Documents\GitHub\tsm-frontend\api`

Do not edit:
`C:\Users\sfara\Documents\GitHub\tsm-backend\website`

unless the user explicitly asks for `website/`, the legacy static site, `website/site/data`, `website/site/history`, or generated static data.
