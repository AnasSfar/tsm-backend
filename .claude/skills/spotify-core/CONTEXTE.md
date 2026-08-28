# Contexte Spotify Core

## Role

`collectors/spotify/core` contient les helpers partages par les pipelines
Spotify streams et charts.

## Fichiers

- `data_paths.py`: chemins modernes/legacy, snapshots, exports web, DB, helpers
  `spotify_chart_dir`, `legacy_spotify_chart_dir`, `run_all_charts_root`,
  `first_existing`.
- `twitter.py`: posting X/Twitter, sessions, images, locks/skip_if autour du
  composer.
- `git_ops.py`: commit/push.
- `notify.py`: ntfy.
- `logger.py`: logging.
- `history.py`: helpers history.
- `retention.py`: nettoyage artefacts generes.
- `swift_top_gate.py`: gate Swift Top apres charts.
- `download.py`: telechargements.
- `fmt.py`: formatters.
- `chart_comment.py`: commentaires chart.
- `album_emoji.py`: emoji albums.

## Regles

- Ne pas hardcoder de nouveaux chemins quand `data_paths.py` fournit deja une
  abstraction moderne/legacy.
- Toute modification de `twitter.py` doit preserver:
  - pas de post image sans image confirmee;
  - re-check `skip_if` apres acquisition du slot;
  - locks anti-double-post;
  - compat sessions region;
  - slot de post a priorite (`_twitter_account_slot(priority=...)` / env `TWITTER_POST_PRIORITY`
    / fichiers `waiter_<acct>_<pid>.json`) : sous contention le slot va au plus prioritaire
    puis au plus ancien, avec anti-famine (`TWITTER_WAITER_AGING_SECONDS`). Fail-open si la
    file d'attente casse (ne jamais bloquer un post a cause d'elle). Bareme -> skill `data-rules`.
- Toute modification de git/notify doit rester non bloquante quand le pipeline
  doit continuer, sauf quand le code l'exige explicitement.
- Les helpers partages ne doivent pas masquer une donnee manquante par defaut
  silencieux.

## Verification

Selon le helper:

- `data_paths.py`: verifier un script charts et un script streams.
- `twitter.py`: tester en mode no-post/dry-run si possible; ne pas poster sans
  intention claire.
- `retention.py`: dry-run ou inspecter chemins avant suppression.
- `git_ops.py`: ne pas commit/push sans demande.

## Pieges

- Changer un fallback legacy peut affecter des scripts historiques.
- Les chemins `website/site/data` peuvent encore etre lus en fallback, mais les
  exports modernes vivent sous `runtime/exports/web`.
- `twitter.py` est fragile au DOM X; diagnostiquer avec les HTML/debugs plutot
  que deviner les selecteurs.
