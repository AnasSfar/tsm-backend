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
- Incident 2026-09-17 (global-post/us-post, plusieurs tentatives echouees) :
  quand le file chooser natif ne se declenche pas ("X file chooser
  introuvable, fallback input[type=file]"), le fallback `set_input_files`
  contourne l'evenement de fermeture du chooser et laisse une couche
  decorative absolument positionnee (inset:0) peinte au-dessus de
  `tweetButton`. Un `.click()` classique boucle en timeout contre elle ; meme
  `force=True` echoue silencieusement car Playwright dispatche quand meme
  l'evenement souris aux coordonnees du bouton, que le navigateur route vers
  ce meme div en hit-test (constate le 2026-09-17 : le clic force ne renvoyait
  pas d'erreur mais le texte du composer n'etait jamais efface, donc rien
  n'etait poste). Fix dans `_click_tweet_button()` : clic normal, puis
  raccourci clavier Ctrl/Cmd+Entree (evite completement le hit-test), puis
  `element.evaluate("el => el.click()")` (invoque les handlers JS du bouton
  sans passer par le hit-test souris), et seulement en dernier recours un clic
  force. Apres chaque etape, on verifie brievement le composer/toast avant
  d'escalader, pour eviter de poster deux fois si une etape a en fait marche.
  Un screenshot/HTML debug est aussi ecrit des que le fallback file-input est
  utilise (`_write_upload_debug_artifacts`), pour diagnostiquer pourquoi le
  chooser natif ne se declenche pas.
