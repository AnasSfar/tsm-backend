import json
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
r = json.loads((HERE/'analytics_results.json').read_text(encoding='utf-8'))


def number(x):
    return f'{x:,}'.replace(',', ' ')


def pct(x):
    return f'{x*100:.2f}%'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']
                     + ['| '+' | '.join(str(x) for x in row)+' |' for row in rows])


lines = ['# Analyse X — SwiftiesCharts',
         '\nSource : '+r['source'],
         '\nExport : 17 septembre–14 octobre 2026. 1 425 identifiants uniques, aucun doublon ni métrique vide.',
         '\n**Périmètre : tous les posts du CSV.** Aucune colonne ne distingue les réponses, les racines de threads, '
         'les reposts ou les posts cités. Aucun filtre « posts principaux » n’est donc appliqué. Ne pas interpréter '
         'le volume comme un décompte vérifié des seuls posts principaux.',
         '\nLes heures proviennent des identifiants Snowflake et sont converties en Europe/Madrid (UTC+2). '
         'La date UTC extraite concorde avec la colonne Date pour les 1 425 lignes. Méthode : '
         'https://raw.githubusercontent.com/twitter-archive/snowflake/snowflake-2010/src/main/scala/com/twitter/service/snowflake/IdWorker.scala',
         '\n## Volume',
         '\n**1 403 posts / 26 journées locales complètes = 53,96 posts/jour**, du 18 septembre au 13 octobre. '
         'Les 17 septembre et 14 octobre sont des journées limites partielles et sont exclus de cette moyenne. '
         'Médiane : 43,5 posts/jour. Suivant les dates UTC du CSV : 1 421 / 27 = 52,63 posts/jour du 17 septembre au 13 octobre.',
         '\nDu 25 au 29 septembre : 568 posts, soit 113,6/jour. Les 21 autres journées complètes : '
         '835 posts, soit 39,76/jour. Cette pointe comprend notamment 232 posts classés iTunes, '
         '109 charts Spotify et 88 Apple Music par les règles textuelles décrites ci-dessous.',
         '\n'+table(['Jour Madrid','Posts','Impressions','Likes'],
                    [[d+(' (partiel)' if d in ('2026-09-17','2026-10-14') else ''), s['n'],number(s['Impressions']),number(s['Likes'])]
                     for d,s in r['daily'].items()]),
         '\n## Métriques de l’export',
         '\n'+table(['Mesure','Valeur'], [[k,number(r['overall'][k])] for k in
                    ('Impressions','Likes','Engagements','Reposts','Replies','Bookmarks','New follows','URL Clicks','Profile visits')]),
         '\nImpressions = affichages, pas personnes uniques. Les nouveaux abonnements sont ceux attribués aux posts '
         'dans l’export, pas la croissance nette du compte. La fenêtre d’accumulation des métriques n’est pas explicitée '
         'dans le fichier ; aucun chiffre n’est présenté comme un résultat standardisé à 24 heures.',
         f"\nPost médian : {r['overall']['median_views']} impressions et {r['overall']['median_likes']} likes. "
         f"Moyenne : {r['overall']['mean_views']:.1f} impressions/post. Taux d’engagement X pondéré : "
         f"{pct(r['overall']['engagement_rate'])}. Taux d’interactions sociales pondéré : {pct(r['overall']['social_rate'])}.",
         '\nTaux X = somme Engagements / somme Impressions. Taux social = '
         '(Likes + Reposts + Replies + Bookmarks) / Impressions ; il mesure des actions, pas des utilisateurs uniques. '
         'Le total Engagements X reste la valeur fournie par X : les colonnes détaillées ne sont pas supposées exhaustives.',
         '\n## Classements']
for key,label in [('Impressions','Les plus vus'),('Likes','Les plus aimés'),('Engagements','Le plus d’engagements X'),
                  ('New follows','Le plus d’abonnements attribués'),('URL Clicks','Le plus de clics URL'),
                  ('social_rate','Meilleurs taux sociaux (au moins 1 000 impressions, publiés avant le 12 octobre)')]:
    lines += ['\n### '+label,
              '\n'+table(['Post','Madrid','Impressions','Likes','Engagements X','Taux social','Clics URL','Abonnements'],
                         [[f"[{p['Post text'][:105].replace('|','/')}…]({p['Post Link']})",p['Madrid'][:16].replace('T',' '),
                           number(p['Impressions']),p['Likes'],p['Engagements'],pct(p['social_rate']),p['URL Clicks'],p['New follows']]
                          for p in r['rankings'][key]])]
lines += ['\n## Note relative sur 10',
          '\nScore éditorial créé pour cette analyse, pas une note fournie par X ni une estimation d’importance des stats. '
          '50% rang percentile d’impressions + 50% rang percentile du taux social ; résultat multiplié par 10. '
          f"Comparaison parmi {r['eligible_n']} posts publiés avant le 12 octobre ayant au moins 1 000 impressions. "
          'Les autres posts restent non notés. Les ex æquo utilisent le milieu de leur rang percentile. '
          'Un post de trafic peut avoir une note sociale moyenne tout en accomplissant très bien son objectif de clics.',
          '\n'+table(['Post','Note /10','Impressions','Taux social'],
                     [[f"[{p['Post text'][:95].replace('|','/')}…]({p['Post Link']})",p['note_relative_sur_10'],number(p['Impressions']),pct(p['social_rate'])]
                      for p in r['rankings']['note']]),
          '\n## Horaires — heure de Madrid',
          '\nComparaison sur les 1 357 posts publiés avant le 12 octobre : les publications les plus récentes sont '
          'exclues pour limiter leur désavantage d’ancienneté. Cela ne standardise pas pour autant le temps d’exposition.',
          '\n'+table(['Créneau','Posts','Impressions moyennes','Impressions médianes','Likes médians','Taux social pondéré'],
                     [[h,s['n'],f"{s['mean_views']:.0f}",s['median_views'],s['median_likes'],pct(s['social_rate'])] for h,s in r['hours'].items()]),
          '\n14–16 h présente la meilleure médiane observée (902 impressions, 119 posts). 12–14 h : 768,5 '
          '(28 posts) ; 10–12 h : 716 (52 posts). 16–18 h : 234 (338 posts), mais 287 sont des charts Spotify. '
          'Les sujets, les formats, les réponses éventuelles et les jours de lancement diffèrent selon les créneaux : '
          'ces résultats ne prouvent pas que l’heure seule cause la performance.',
          '\nÀ catégorie textuelle constante : Apple Music, 10–12 h (18 posts) = 1 440,5 impressions médianes ; '
          '14–16 h (15 posts) = 1 245 ; 22–24 h (12 posts) = 720,5. Les charts Spotify ont 918 impressions '
          'médianes à 14–16 h (78 posts), contre 203 à 16–18 h (287 posts), sans pouvoir contrôler les racines/réponses.',
          '\n## Types de contenu',
          '\nClassement automatique par expressions explicites du texte, exclusif et auditable dans le script. '
          'Ce sont des groupes exploratoires, pas des métadonnées officielles X. Les records sont reconnus avant '
          'les updates albums ; un update mentionnant BEST DAY se retrouve donc parmi les records. '
          'Les cas non reconnus restent « Autres / à vérifier ». Même population antérieure au 12 octobre.',
          '\n'+table(['Type','Posts','Impressions médianes','Likes médians','Taux social pondéré'],
                     [[c,s['n'],s['median_views'],s['median_likes'],pct(s['social_rate'])]
                      for c,s in sorted(r['categories'].items(),key=lambda kv:-kv[1]['median_views'])]),
          '\n## Pistes à tester',
          '\n- Réserver davantage de place aux annonces de paliers, YouTube et aux vrais événements : '
          'leurs exemples les plus forts suscitent beaucoup de réactions. Les moyennes seules sont sensibles aux gros succès isolés.',
          '- Examiner les séries iTunes : 240 posts anciens, 120,5 impressions et 3 likes médians. '
          'Leur faible portée observée peut aussi refléter une publication en réponses ; vérifier ce statut avant une décision de réduction.',
          '- Tester 10–16 h pour les contenus comparables qui peuvent être programmés. '
          'Ne pas retarder une actualité urgente sur la seule base de ces associations.',
          '- Évaluer les annonces du site par leurs clics : le post du 22 septembre apporte 458 clics URL '
          '(2,12% de ses 21 598 impressions), indépendamment de sa note sociale.',
          '- Pour vérifier une baisse de portée liée au volume, il faut comparer des posts de même type et '
          'statut de thread sur des fenêtres de mesure égales. Cet export ne permet pas une conclusion causale.',
          '\n## Fichiers associés',
          '\n`posts_analytics_enriched.csv` : données source intactes avec heure UTC/Madrid, catégorie, taux et note. '
          '`daily_analytics.csv`, `hourly_analytics.csv`, `category_analytics.csv` : tableaux détaillés. '
          'Le fichier source des Downloads reste inchangé. Aucun post ni paramètre de publication n’a été modifié.']
(HERE/'rapport_analytics.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
assert sum(s['n'] for s in r['daily'].values()) == r['overall']['n']
assert sum(s['Impressions'] for s in r['daily'].values()) == r['overall']['Impressions']
assert sum(s['n'] for s in r['hours'].values()) == sum(s['n'] for s in r['categories'].values()) == r['mature_n']
print('Report saved; counts and totals reconciled.')
