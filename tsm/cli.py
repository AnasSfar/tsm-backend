from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from collectors.spotify.core.data_paths import (
    LEGACY_WEBSITE_ARCHIVE_ROOT,
    LEGACY_WEBSITE_DATA_DIR,
    LEGACY_WEBSITE_HISTORY_DIR,
    LEGACY_WEBSITE_ROOT,
    MANUAL_BACKUPS_ROOT,
    REPO_ROOT,
    RUNTIME_CACHE_ROOT,
    RUNTIME_LOGS_ROOT,
    RUNTIME_ROOT,
    SNAPSHOTS_ROOT,
    WEB_EXPORT_DATA_DIR,
    WEB_EXPORT_HISTORY_DIR,
    WEB_EXPORT_ROOT,
)
from tsm.terminal_log import TerminalLog, terminal_log_path


def _run(script: Path, args: list[str]) -> int:
    cmd = [sys.executable, "-u", str(script), *args]
    return _run_cmd(cmd)


def _run_module(module: str, args: list[str]) -> int:
    cmd = [sys.executable, "-u", "-m", module, *args]
    return _run_cmd(cmd)


def _run_cmd(cmd: list[str]) -> int:
    print("$ " + " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(8192)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    return proc.wait()


def _log_date_for_args(args: argparse.Namespace) -> str | None:
    return getattr(args, "date", None) or getattr(args, "new_date", None)


def _log_name(args: argparse.Namespace) -> str:
    parts = [str(getattr(args, "command", "tsm"))]
    for attr in ("collector", "exporter", "audit_target", "migration"):
        value = getattr(args, attr, None)
        if value:
            parts.append(str(value))
    return "_".join(parts)


def _ensure_no_conflicting_post_flags(args: list[str], no_post: bool, post: bool) -> list[str]:
    cleaned = [arg for arg in args if arg not in {"--no-post", "--post"}]
    if no_post:
        cleaned.append("--no-post")
    return cleaned


def collect_streams(args: argparse.Namespace, passthrough: list[str]) -> int:
    script = REPO_ROOT / "collectors" / "spotify" / "streams" / "update_streams.py"
    forwarded = _ensure_no_conflicting_post_flags(passthrough, args.no_post, args.post)
    if args.date:
        forwarded.insert(0, args.date)
    return _run(script, forwarded)


def collect_charts(args: argparse.Namespace, passthrough: list[str]) -> int:
    script = REPO_ROOT / "collectors" / "spotify" / "charts" / "run_all_charts.py"
    forwarded = _ensure_no_conflicting_post_flags(passthrough, args.no_post, args.post)
    if args.date:
        forwarded.append(args.date)
    return _run(script, forwarded)


def collect_apple_music(args: argparse.Namespace, passthrough: list[str]) -> int:
    script = REPO_ROOT / "collectors" / "apple_music" / "run_apple_music.py"
    forwarded = _ensure_no_conflicting_post_flags(passthrough, args.no_post, False)
    return _run(script, forwarded)


def collect_deezer(args: argparse.Namespace, passthrough: list[str]) -> int:
    script = REPO_ROOT / "collectors" / "deezer" / "run_deezer.py"
    forwarded = _ensure_no_conflicting_post_flags(passthrough, args.no_post, False)
    return _run(script, forwarded)


def collect_youtube(args: argparse.Namespace, passthrough: list[str]) -> int:
    forwarded = _ensure_no_conflicting_post_flags(passthrough, args.no_post, False)
    if args.date:
        forwarded.extend(["--date", args.date])
    return _run_module("collectors.youtube.update_youtube", forwarded)


def daily(args: argparse.Namespace, passthrough: list[str]) -> int:
    chart_args = argparse.Namespace(date=args.date, no_post=args.no_post, post=args.post)
    stream_args = argparse.Namespace(date=args.date, no_post=args.no_post, post=args.post)
    rc = collect_charts(chart_args, passthrough)
    if rc != 0:
        return rc
    return collect_streams(stream_args, passthrough)


def export_web(args: argparse.Namespace, passthrough: list[str]) -> int:
    script = REPO_ROOT / "scripts" / "export_for_web.py"
    forwarded = list(passthrough)
    if args.date:
        forwarded.extend(["--new-date", args.date])
    if args.dry_run:
        forwarded.append("--dry-run")
    return _run(script, forwarded)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return set()
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def build_data_audit() -> tuple[str, dict[str, object]]:
    tracked = _git_tracked_files()
    roots = {
        "db": REPO_ROOT / "db",
        "data": REPO_ROOT / "data",
        "snapshots": SNAPSHOTS_ROOT,
        "runtime": RUNTIME_ROOT,
        "legacy_website": LEGACY_WEBSITE_ROOT,
        "collectors": REPO_ROOT / "collectors",
        "scripts": REPO_ROOT / "scripts",
    }
    counts = {}
    for name, root in roots.items():
        files = _iter_files(root)
        counts[name] = {
            "files": len(files),
            "tracked": sum(1 for p in files if p.relative_to(REPO_ROOT).as_posix() in tracked),
        }

    tracked_runtime_noise = sorted(
        path for path in tracked
        if (
            "__pycache__" in path
            or "chrome_profile" in path
            or "browser_cache" in path
            or path.endswith((".pyc", ".log", ".lock"))
        )
    )
    backups = sorted(path for path in tracked if path.endswith(".bak") or ".bak." in path)
    website_exports = sorted(
        path for path in tracked
        if path.startswith("website/site/data/") or path.startswith("website/site/history/")
    )

    hash_groups: dict[str, list[str]] = defaultdict(list)
    duplicate_roots = [REPO_ROOT / "db", LEGACY_WEBSITE_DATA_DIR, LEGACY_WEBSITE_HISTORY_DIR, SNAPSHOTS_ROOT]
    for root in duplicate_roots:
        for path in _iter_files(root):
            try:
                if path.stat().st_size > 10 * 1024 * 1024:
                    continue
                hash_groups[_hash_file(path)].append(path.relative_to(REPO_ROOT).as_posix())
            except OSError:
                continue
    duplicates = {
        digest: paths
        for digest, paths in hash_groups.items()
        if len(paths) > 1
    }

    report = {
        "generated_for": str(REPO_ROOT),
        "canonical_layout": {
            "code": "collectors/",
            "business_data": "db/",
            "daily_snapshots": "snapshots/",
            "runtime_outputs": "runtime/",
            "web_exports": WEB_EXPORT_ROOT.relative_to(REPO_ROOT).as_posix(),
            "legacy_website_archive": LEGACY_WEBSITE_ARCHIVE_ROOT.relative_to(REPO_ROOT).as_posix(),
        },
        "counts": counts,
        "tracked_runtime_noise": tracked_runtime_noise[:200],
        "tracked_runtime_noise_total": len(tracked_runtime_noise),
        "tracked_backups": backups[:200],
        "tracked_backups_total": len(backups),
        "tracked_legacy_website_exports": website_exports[:200],
        "tracked_legacy_website_exports_total": len(website_exports),
        "duplicate_sha256_groups": list(duplicates.values())[:100],
        "duplicate_sha256_groups_total": len(duplicates),
        "recommended_actions": [
            {"family": "db/*.bak", "action": "archive", "target": "data/_archive/manual-backups/YYYY-MM-DD/"},
            {"family": "website/", "action": "archive", "target": "data/_archive/legacy-website/"},
            {"family": "website/site/data and history", "action": "replace writes", "target": "runtime/exports/web/"},
            {"family": "browser caches and logs", "action": "ignore", "target": "runtime/cache and runtime/logs"},
            {"family": "snapshots", "action": "keep", "target": "snapshots/"},
        ],
    }
    markdown = _audit_markdown(report)
    return markdown, report


def _audit_markdown(report: dict[str, object]) -> str:
    counts = report["counts"]
    lines = [
        "# TSM Data Layout Audit",
        "",
        f"Generated for `{report['generated_for']}`.",
        "",
        "## Canonical Layout",
        "",
    ]
    for label, target in report["canonical_layout"].items():
        lines.append(f"- `{label}`: `{target}`")
    lines.extend(["", "## File Counts", ""])
    for name, data in counts.items():
        lines.append(f"- `{name}`: {data['files']} file(s), {data['tracked']} tracked")
    lines.extend(["", "## Risky Tracked Files", ""])
    lines.append(f"- Runtime/cache/log-like tracked files: {report['tracked_runtime_noise_total']}")
    lines.append(f"- Backup files tracked: {report['tracked_backups_total']}")
    lines.append(f"- Legacy website export files tracked: {report['tracked_legacy_website_exports_total']}")
    lines.append(f"- Duplicate checksum groups: {report['duplicate_sha256_groups_total']}")
    lines.extend(["", "## Recommended Actions", ""])
    for item in report["recommended_actions"]:
        lines.append(f"- `{item['family']}` -> `{item['action']}` into `{item['target']}`")
    lines.extend(["", "## Samples", ""])
    for key in ("tracked_runtime_noise", "tracked_backups", "tracked_legacy_website_exports"):
        lines.append(f"### {key}")
        values = report.get(key) or []
        if not values:
            lines.append("- none")
        else:
            for value in values[:30]:
                lines.append(f"- `{value}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def audit_data(args: argparse.Namespace, _passthrough: list[str]) -> int:
    markdown, report = build_data_audit()
    if args.write:
        docs_dir = REPO_ROOT / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "data-layout-audit.md").write_text(markdown, encoding="utf-8")
        (docs_dir / "data-layout-audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("Wrote docs/data-layout-audit.md and docs/data-layout-audit.json")
    else:
        print(markdown)
    return 0


def _copy_with_manifest(src: Path, dst: Path, rows: list[dict[str, str]], apply: bool) -> None:
    rel_src = src.relative_to(REPO_ROOT).as_posix()
    rel_dst = dst.relative_to(REPO_ROOT).as_posix()
    sha = _hash_file(src)
    rows.append({"source": rel_src, "target": rel_dst, "sha256": sha, "action": "copy"})
    if not apply:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied_sha = _hash_file(dst)
    if copied_sha != sha:
        raise RuntimeError(f"Checksum mismatch after copying {rel_src} -> {rel_dst}")


def migrate_layout(args: argparse.Namespace, _passthrough: list[str]) -> int:
    apply = bool(args.apply)
    today = date.today().isoformat()
    manifest_rows: list[dict[str, str]] = []

    for src in sorted((REPO_ROOT / "db").rglob("*.bak")):
        target = MANUAL_BACKUPS_ROOT / today / src.relative_to(REPO_ROOT / "db")
        _copy_with_manifest(src, target, manifest_rows, apply)

    if LEGACY_WEBSITE_ROOT.exists():
        for src in _iter_files(LEGACY_WEBSITE_ROOT):
            target = LEGACY_WEBSITE_ARCHIVE_ROOT / today / src.relative_to(LEGACY_WEBSITE_ROOT)
            _copy_with_manifest(src, target, manifest_rows, apply)

    manifest_dir = REPO_ROOT / "docs"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / ("layout-migration-manifest.csv" if apply else "layout-migration-dry-run.csv")
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source", "target", "sha256", "action"])
        writer.writeheader()
        writer.writerows(manifest_rows)

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] {len(manifest_rows)} file(s) planned/copied. Manifest: {manifest_path.relative_to(REPO_ROOT)}")
    if not apply:
        print("No files were copied. Re-run with --apply to archive copies with checksum verification.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tsm")
    sub = parser.add_subparsers(dest="command", required=True)

    daily_parser = sub.add_parser("daily", help="Run main daily collectors.")
    daily_parser.add_argument("date", nargs="?")
    daily_parser.add_argument("--no-post", action="store_true")
    daily_parser.add_argument("--post", action="store_true")

    collect_parser = sub.add_parser("collect", help="Run one collector family.")
    collect_sub = collect_parser.add_subparsers(dest="collector", required=True)
    for name in ("streams", "charts"):
        p = collect_sub.add_parser(name)
        p.add_argument("date", nargs="?")
        p.add_argument("--no-post", action="store_true")
        p.add_argument("--post", action="store_true")
    p = collect_sub.add_parser("apple-music")
    p.add_argument("--no-post", action="store_true")
    p = collect_sub.add_parser("deezer")
    p.add_argument("--no-post", action="store_true")
    p = collect_sub.add_parser("youtube")
    p.add_argument("date", nargs="?")
    p.add_argument("--no-post", action="store_true")
    p.add_argument("--post", action="store_true")
    p.add_argument("--force", action="store_true")

    export_parser = sub.add_parser("export", help="Run exports.")
    export_sub = export_parser.add_subparsers(dest="exporter", required=True)
    p = export_sub.add_parser("web")
    p.add_argument("--date")
    p.add_argument("--dry-run", action="store_true")

    audit_parser = sub.add_parser("audit", help="Audit repository state.")
    audit_sub = audit_parser.add_subparsers(dest="audit_target", required=True)
    p = audit_sub.add_parser("data")
    p.add_argument("--write", action="store_true", help="Write docs/data-layout-audit.* instead of printing.")

    migrate_parser = sub.add_parser("migrate", help="Prepare layout migrations.")
    migrate_sub = migrate_parser.add_subparsers(dest="migration", required=True)
    p = migrate_sub.add_parser("layout")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, passthrough = parser.parse_known_args(argv)
    log_path = terminal_log_path(_log_name(args), _log_date_for_args(args))

    with TerminalLog(log_path):
        return _dispatch(args, passthrough, parser)


def _dispatch(args: argparse.Namespace, passthrough: list[str], parser: argparse.ArgumentParser) -> int:

    if args.command == "daily":
        return daily(args, passthrough)
    if args.command == "collect":
        if args.collector == "streams":
            return collect_streams(args, passthrough)
        if args.collector == "charts":
            return collect_charts(args, passthrough)
        if args.collector == "apple-music":
            return collect_apple_music(args, passthrough)
        if args.collector == "deezer":
            return collect_deezer(args, passthrough)
        if args.collector == "youtube":
            forwarded = list(passthrough)
            if getattr(args, "force", False):
                forwarded.append("--force")
            return collect_youtube(args, forwarded)
    if args.command == "export" and args.exporter == "web":
        return export_web(args, passthrough)
    if args.command == "audit" and args.audit_target == "data":
        return audit_data(args, passthrough)
    if args.command == "migrate" and args.migration == "layout":
        return migrate_layout(args, passthrough)

    parser.error("unsupported command")
    return 2
