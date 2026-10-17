from pathlib import Path
import ast
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def git(*args, data=None):
    result = subprocess.run(["git", *args], cwd=ROOT, input=data, capture_output=True, check=True)
    return result.stdout


git("diff", "--cached", "--quiet")

# Preserve pre-existing work in these shared files; stage only this task's edits.
path = "collectors/spotify/streams/tools/scripts/forecast_milestones.py"
current = (ROOT / path).read_text(encoding="utf-8")
start = current.index("def next_song_milestone_at_recent_pace(")
end = current.index("def _daily_on_projection_day(", start)
staged = current[:start] + current[end:]
ast.parse(staged)
partials = {path: staged}

path = ".claude/skills/spotify-streams/CONTEXTE.md"
head = git("show", f"HEAD:{path}").decode("utf-8")
current = (ROOT / path).read_text(encoding="utf-8")
marker = "## Anticipation des milestones (decision proprietaire 2026-10-16)"
partials[path] = current[:current.index(marker)] + head[head.index(marker):]

path = ".claude/skills/image-gen/SKILL.md"
head = git("show", f"HEAD:{path}").decode("utf-8")
current = (ROOT / path).read_text(encoding="utf-8")
start = current.index("**Milestone Watch, owner decision 2026-10-17:")
end = current.index("## Hors", start)
marker = "## Hors"
index = head.index(marker)
partials[path] = head[:index] + current[start:end] + head[index:]

for path, text in partials.items():
    blob = git("hash-object", "-w", "--stdin", data=text.encode("utf-8")).decode().strip()
    git("update-index", "--cacheinfo", "100644", blob, path)

git("add", "--",
    "collectors/spotify/streams/tools/scripts/milestone_estimator.py",
    "collectors/spotify/streams/tools/scripts/milestone_seasonality.py",
    "collectors/spotify/streams/tools/scripts/spotify_app_cards.py",
    "dev/audit_milestone_estimator.py", "dev/test_milestone_estimator.py",
    "dev/test_milestone_seasonality.py", "scripts/r2.py",
    "previews_and_sims/milestone-estimator/README.md",
    "previews_and_sims/milestone-estimator/check_frontend.py",
    "previews_and_sims/milestone-estimator/preview_notes.py")
print(git("diff", "--cached", "--stat").decode())
git("diff", "--cached", "--check")
