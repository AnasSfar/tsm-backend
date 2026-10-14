# Analyse X — SwiftiesCharts

Source : C:\Users\sfara\Downloads\account_analytics_content_2026-09-17_2026-10-14.csv

Export : 17 septembre–14 octobre 2026. 1 425 identifiants uniques, aucun doublon ni métrique vide.

**Périmètre : tous les posts du CSV.** Aucune colonne ne distingue les réponses, les racines de threads, les reposts ou les posts cités. Aucun filtre « posts principaux » n’est donc appliqué. Ne pas interpréter le volume comme un décompte vérifié des seuls posts principaux.

Les heures proviennent des identifiants Snowflake et sont converties en Europe/Madrid (UTC+2). La date UTC extraite concorde avec la colonne Date pour les 1 425 lignes. Méthode : https://raw.githubusercontent.com/twitter-archive/snowflake/snowflake-2010/src/main/scala/com/twitter/service/snowflake/IdWorker.scala

## Volume

**1 403 posts / 26 journées locales complètes = 53,96 posts/jour**, du 18 septembre au 13 octobre. Les 17 septembre et 14 octobre sont des journées limites partielles et sont exclus de cette moyenne. Médiane : 43,5 posts/jour. Suivant les dates UTC du CSV : 1 421 / 27 = 52,63 posts/jour du 17 septembre au 13 octobre.

Du 25 au 29 septembre : 568 posts, soit 113,6/jour. Les 21 autres journées complètes : 835 posts, soit 39,76/jour. Cette pointe comprend notamment 232 posts classés iTunes, 109 charts Spotify et 88 Apple Music par les règles textuelles décrites ci-dessous.

| Jour Madrid | Posts | Impressions | Likes |
| --- | --- | --- | --- |
| 2026-09-17 (partiel) | 6 | 5 743 | 361 |
| 2026-09-18 | 40 | 33 363 | 1 255 |
| 2026-09-19 | 82 | 59 407 | 2 868 |
| 2026-09-20 | 41 | 84 401 | 2 740 |
| 2026-09-21 | 25 | 28 378 | 1 432 |
| 2026-09-22 | 34 | 52 875 | 2 061 |
| 2026-09-23 | 13 | 22 320 | 1 707 |
| 2026-09-24 | 46 | 63 351 | 3 402 |
| 2026-09-25 | 88 | 84 504 | 4 293 |
| 2026-09-26 | 93 | 97 158 | 4 601 |
| 2026-09-27 | 124 | 86 400 | 3 458 |
| 2026-09-28 | 128 | 60 099 | 2 082 |
| 2026-09-29 | 135 | 55 301 | 3 027 |
| 2026-09-30 | 59 | 29 296 | 1 516 |
| 2026-10-01 | 31 | 21 286 | 1 376 |
| 2026-10-02 | 50 | 67 096 | 1 707 |
| 2026-10-03 | 40 | 22 746 | 1 094 |
| 2026-10-04 | 51 | 33 034 | 1 215 |
| 2026-10-05 | 26 | 17 714 | 952 |
| 2026-10-06 | 16 | 13 059 | 885 |
| 2026-10-07 | 44 | 73 762 | 1 684 |
| 2026-10-08 | 43 | 23 897 | 1 212 |
| 2026-10-09 | 55 | 29 522 | 1 182 |
| 2026-10-10 | 50 | 27 533 | 1 253 |
| 2026-10-11 | 37 | 20 529 | 942 |
| 2026-10-12 | 22 | 39 407 | 602 |
| 2026-10-13 | 30 | 18 552 | 692 |
| 2026-10-14 (partiel) | 16 | 9 211 | 375 |

## Métriques de l’export

| Mesure | Valeur |
| --- | --- |
| Impressions | 1 179 944 |
| Likes | 49 974 |
| Engagements | 130 451 |
| Reposts | 8 573 |
| Replies | 985 |
| Bookmarks | 1 830 |
| New follows | 73 |
| URL Clicks | 1 957 |
| Profile visits | 6 694 |

Impressions = affichages, pas personnes uniques. Les nouveaux abonnements sont ceux attribués aux posts dans l’export, pas la croissance nette du compte. La fenêtre d’accumulation des métriques n’est pas explicitée dans le fichier ; aucun chiffre n’est présenté comme un résultat standardisé à 24 heures.

Post médian : 583 impressions et 22 likes. Moyenne : 828.0 impressions/post. Taux d’engagement X pondéré : 11.06%. Taux d’interactions sociales pondéré : 5.20%.

Taux X = somme Engagements / somme Impressions. Taux social = (Likes + Reposts + Replies + Bookmarks) / Impressions ; il mesure des actions, pas des utilisateurs uniques. Le total Engagements X reste la valeur fournie par X : les colonnes détaillées ne sont pas supposées exhaustives.

## Classements

### Les plus vus

| Post | Madrid | Impressions | Likes | Engagements X | Taux social | Clics URL | Abonnements |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [🤍 / "Opalite" has now surpassed 900,000,000 streams. It is Taylor Swift's 34th song to do so. The next so…](https://x.com/SwiftiesCharts/status/2107608590983090235) | 2026-10-07 01:07 | 46 365 | 474 | 1268 | 1.16% | 1 | 5 |
| [📈 / "Wi$h Li$t" has now surpassed "evermore (feat. Bon Iver)" and is now Taylor Swift's 121st most stream…](https://x.com/SwiftiesCharts/status/2106030931987308662) | 2026-10-02 16:37 | 28 443 | 176 | 1264 | 0.80% | 79 | 4 |
| [📈 / Taylor Swift's albums and songs on the Spotify counter Sunday, October 11th, 2026. See the full updat…](https://x.com/SwiftiesCharts/status/2109755390069981229) | 2026-10-12 23:17 | 24 933 | 28 | 3496 | 0.19% | 24 | 9 |
| [❤️‍🔥 You can now rank 'The Life of a Showgirl' in our website right here : 🔗: https://t.co/2RYJ9veDcC Sha…](https://x.com/SwiftiesCharts/status/2102436425509859771) | 2026-09-22 18:34 | 21 598 | 155 | 1342 | 1.11% | 458 | 1 |
| [🏆 / "Blank Space" has once again earned its BEST DAY since October 28th, 2023 with 1,891,368 streams [+1.…](https://x.com/SwiftiesCharts/status/2101734018186125431) | 2026-09-20 20:03 | 16 157 | 126 | 844 | 0.90% | 18 | 3 |

### Les plus aimés

| Post | Madrid | Impressions | Likes | Engagements X | Taux social | Clics URL | Abonnements |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [🎥 / "Patient Zero" debuts with 2,255,307 views in its first 24 hours on YouTube. https://t.co/DplYSjFlhf…](https://x.com/SwiftiesCharts/status/2103698037063467111) | 2026-09-26 06:07 | 4 328 | 599 | 738 | 15.00% | 0 | 0 |
| [🤍 / "Opalite" has now surpassed 900,000,000 streams. It is Taylor Swift's 34th song to do so. The next so…](https://x.com/SwiftiesCharts/status/2107608590983090235) | 2026-10-07 01:07 | 46 365 | 474 | 1268 | 1.16% | 1 | 5 |
| [📈/ “Elizabeth Taylor” has now surpassed 400 MILLION streams on Spotify. — The third from “The Life Of A S…](https://x.com/SwiftiesCharts/status/2103238719083815131) | 2026-09-24 23:42 | 3 375 | 452 | 590 | 15.08% | 0 | 0 |
| [🤍 / "Blank Space" has now surpassed 2,600,000,000 streams. It is Taylor Swift's 2nd song to do so. https:…](https://x.com/SwiftiesCharts/status/2101745041681039588) | 2026-09-20 20:47 | 2 792 | 407 | 514 | 16.05% | 2 | 0 |
| [🎥 / ❤️‍🔥 "Patient Zero" music video daily views on YouTube: DAY 1 - 4,497,126 DAY 2 - 3,281,343 (-27.0%) …](https://x.com/SwiftiesCharts/status/2105780046921355392) | 2026-10-02 00:01 | 13 831 | 380 | 684 | 3.15% | 2 | 4 |

### Le plus d’engagements X

| Post | Madrid | Impressions | Likes | Engagements X | Taux social | Clics URL | Abonnements |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [📈 / Taylor Swift's albums and songs on the Spotify counter Sunday, October 11th, 2026. See the full updat…](https://x.com/SwiftiesCharts/status/2109755390069981229) | 2026-10-12 23:17 | 24 933 | 28 | 3496 | 0.19% | 24 | 9 |
| [📈 / Taylor Swift on Spotify US Charts on Friday, September 25, 2026 : "Wood" re-enters the chart at #137 …](https://x.com/SwiftiesCharts/status/2103838985265037738) | 2026-09-26 15:27 | 11 572 | 186 | 1598 | 2.12% | 1 | 3 |
| [🌍 / Taylor Swift on Spotify Global Charts on Saturday, September 26, 2026 : https://t.co/QyU4J08aZu…](https://x.com/SwiftiesCharts/status/2104208296257396991) | 2026-09-27 15:55 | 13 543 | 218 | 1523 | 1.98% | 0 | 2 |
| [❤️‍🔥 You can now rank 'The Life of a Showgirl' in our website right here : 🔗: https://t.co/2RYJ9veDcC Sha…](https://x.com/SwiftiesCharts/status/2102436425509859771) | 2026-09-22 18:34 | 21 598 | 155 | 1342 | 1.11% | 458 | 1 |
| [🤍 / "Opalite" has now surpassed 900,000,000 streams. It is Taylor Swift's 34th song to do so. The next so…](https://x.com/SwiftiesCharts/status/2107608590983090235) | 2026-10-07 01:07 | 46 365 | 474 | 1268 | 1.16% | 1 | 5 |

### Le plus d’abonnements attribués

| Post | Madrid | Impressions | Likes | Engagements X | Taux social | Clics URL | Abonnements |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [📈 / Taylor Swift's albums and songs on the Spotify counter Sunday, October 11th, 2026. See the full updat…](https://x.com/SwiftiesCharts/status/2109755390069981229) | 2026-10-12 23:17 | 24 933 | 28 | 3496 | 0.19% | 24 | 9 |
| [🤍 / "Opalite" has now surpassed 900,000,000 streams. It is Taylor Swift's 34th song to do so. The next so…](https://x.com/SwiftiesCharts/status/2107608590983090235) | 2026-10-07 01:07 | 46 365 | 474 | 1268 | 1.16% | 1 | 5 |
| [🎥 / ❤️‍🔥 "Patient Zero" music video daily views on YouTube: DAY 1 - 4,497,126 DAY 2 - 3,281,343 (-27.0%) …](https://x.com/SwiftiesCharts/status/2105780046921355392) | 2026-10-02 00:01 | 13 831 | 380 | 684 | 3.15% | 2 | 4 |
| [📈 / "Wi$h Li$t" has now surpassed "evermore (feat. Bon Iver)" and is now Taylor Swift's 121st most stream…](https://x.com/SwiftiesCharts/status/2106030931987308662) | 2026-10-02 16:37 | 28 443 | 176 | 1264 | 0.80% | 79 | 4 |
| [📈 / Taylor Swift on Spotify US Charts on Friday, September 25, 2026 : "Wood" re-enters the chart at #137 …](https://x.com/SwiftiesCharts/status/2103838985265037738) | 2026-09-26 15:27 | 11 572 | 186 | 1598 | 2.12% | 1 | 3 |

### Le plus de clics URL

| Post | Madrid | Impressions | Likes | Engagements X | Taux social | Clics URL | Abonnements |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [❤️‍🔥 You can now rank 'The Life of a Showgirl' in our website right here : 🔗: https://t.co/2RYJ9veDcC Sha…](https://x.com/SwiftiesCharts/status/2102436425509859771) | 2026-09-22 18:34 | 21 598 | 155 | 1342 | 1.11% | 458 | 1 |
| [🏆 / The Vmas are set to announce this year’s winner of MVoTY earlier than expected and before the officia…](https://x.com/SwiftiesCharts/status/2104159032369987807) | 2026-09-27 12:39 | 2 660 | 97 | 451 | 4.70% | 200 | 0 |
| [❤️‍🔥 / You can now rank all tracks from ‘The Life of a Showgirl : The Encore’ on our website! Can’t wait …](https://x.com/SwiftiesCharts/status/2104544495895253426) | 2026-09-28 14:11 | 14 318 | 42 | 376 | 0.38% | 96 | 0 |
| [📈 / "Wi$h Li$t" has now surpassed "evermore (feat. Bon Iver)" and is now Taylor Swift's 121st most stream…](https://x.com/SwiftiesCharts/status/2106030931987308662) | 2026-10-02 16:37 | 28 443 | 176 | 1264 | 0.80% | 79 | 4 |
| ["Patient Zero" ranks at #2 on Global Spotify Charts with 5,547,520 streams (September 26, 2026). See full…](https://x.com/SwiftiesCharts/status/2104206940087714091) | 2026-09-27 15:50 | 4 423 | 143 | 501 | 4.07% | 38 | 0 |

### Meilleurs taux sociaux (au moins 1 000 impressions, publiés avant le 12 octobre)

| Post | Madrid | Impressions | Likes | Engagements X | Taux social | Clics URL | Abonnements |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [🤍 / "Blank Space" has now surpassed 2,600,000,000 streams. It is Taylor Swift's 2nd song to do so. https:…](https://x.com/SwiftiesCharts/status/2101745041681039588) | 2026-09-20 20:47 | 2 792 | 407 | 514 | 16.05% | 2 | 0 |
| [🤍 / "Elizabeth Taylor" has now surpassed 400,000,000 streams. It is Taylor Swift's 95th song to do so. Th…](https://x.com/SwiftiesCharts/status/2103973465128616340) | 2026-09-27 00:22 | 1 354 | 190 | 242 | 15.51% | 0 | 0 |
| [📈/ “Elizabeth Taylor” has now surpassed 400 MILLION streams on Spotify. — The third from “The Life Of A S…](https://x.com/SwiftiesCharts/status/2103238719083815131) | 2026-09-24 23:42 | 3 375 | 452 | 590 | 15.08% | 0 | 0 |
| [🎥 / "Patient Zero" debuts with 2,255,307 views in its first 24 hours on YouTube. https://t.co/DplYSjFlhf…](https://x.com/SwiftiesCharts/status/2103698037063467111) | 2026-09-26 06:07 | 4 328 | 599 | 738 | 15.00% | 0 | 0 |
| [🤍 / "Shake It Off" has now surpassed 2,000,000,000 streams. It is Taylor Swift's 7th song to do so. The n…](https://x.com/SwiftiesCharts/status/2102097376894743031) | 2026-09-21 20:07 | 1 342 | 177 | 224 | 14.61% | 0 | 0 |

## Note relative sur 10

Score éditorial créé pour cette analyse, pas une note fournie par X ni une estimation d’importance des stats. 50% rang percentile d’impressions + 50% rang percentile du taux social ; résultat multiplié par 10. Comparaison parmi 204 posts publiés avant le 12 octobre ayant au moins 1 000 impressions. Les autres posts restent non notés. Les ex æquo utilisent le milieu de leur rang percentile. Un post de trafic peut avoir une note sociale moyenne tout en accomplissant très bien son objectif de clics.

| Post | Note /10 | Impressions | Taux social |
| --- | --- | --- | --- |
| [🎥 / "Patient Zero" debuts with 2,255,307 views in its first 24 hours on YouTube. https://t.co/D…](https://x.com/SwiftiesCharts/status/2103698037063467111) | 9.36 | 4 328 | 15.00% |
| [📈/ “Elizabeth Taylor” has now surpassed 400 MILLION streams on Spotify. — The third from “The L…](https://x.com/SwiftiesCharts/status/2103238719083815131) | 9.12 | 3 375 | 15.08% |
| [🤍 / "Blank Space" has now surpassed 2,600,000,000 streams. It is Taylor Swift's 2nd song to do …](https://x.com/SwiftiesCharts/status/2101745041681039588) | 8.92 | 2 792 | 16.05% |
| [📈 / Taylor Swift on the Apple Music Global charts : #29 (+13) The Fate of Ophelia #65 (+4) I Kn…](https://x.com/SwiftiesCharts/status/2102671727407091732) | 8.8 | 3 480 | 11.84% |
| [📈/ Taylor Swift extends her record as the female artist with the most songs reaching 1 BILLION …](https://x.com/SwiftiesCharts/status/2108230366976770389) | 8.58 | 2 105 | 13.35% |
| [🎵/ Taylor Swift's song titles containing a location and an exclamation mark : #1 "Florida!!! (f…](https://x.com/SwiftiesCharts/status/2102752679181070812) | 8.43 | 1 997 | 13.32% |
| [📈/ "The Life of a Showgirl: The Encore" earned its BEST DAY since June 5th, 2026 with 7,608,961…](https://x.com/SwiftiesCharts/status/2103226539194122357) | 8.19 | 1 802 | 12.54% |
| [🌍 / Taylor Swift songs on the Global Apple Music chart right now: https://t.co/Ou1yBzf8UE https…](https://x.com/SwiftiesCharts/status/2103406336918593980) | 8.16 | 2 798 | 10.29% |
| [🩵 / "Blank Space" by Taylor Swift over the last months on Spotify : June 2026 — 32,207,468 [-5.…](https://x.com/SwiftiesCharts/status/2101958803977654373) | 8.14 | 2 059 | 11.36% |
| [🎥 / ❤️‍🔥 "Taylor Swift - Patient Zero (Expanded Video)" debuts with 1,581,030 views in its firs…](https://x.com/SwiftiesCharts/status/2107323965157130435) | 8.11 | 1 715 | 13.12% |

## Horaires — heure de Madrid

Comparaison sur les 1 357 posts publiés avant le 12 octobre : les publications les plus récentes sont exclues pour limiter leur désavantage d’ancienneté. Cela ne standardise pas pour autant le temps d’exposition.

| Créneau | Posts | Impressions moyennes | Impressions médianes | Likes médians | Taux social pondéré |
| --- | --- | --- | --- | --- | --- |
| 00:00-02:00 | 151 | 1095 | 595 | 23 | 4.49% |
| 02:00-04:00 | 65 | 527 | 552 | 15 | 4.91% |
| 04:00-06:00 | 50 | 411 | 475.0 | 13.0 | 5.48% |
| 06:00-08:00 | 55 | 602 | 573 | 22 | 8.03% |
| 08:00-10:00 | 39 | 444 | 129 | 4 | 5.32% |
| 10:00-12:00 | 52 | 1054 | 716.0 | 30.0 | 7.48% |
| 12:00-14:00 | 28 | 850 | 768.5 | 38.0 | 6.14% |
| 14:00-16:00 | 119 | 1535 | 902 | 46 | 5.09% |
| 16:00-18:00 | 338 | 689 | 234.0 | 5.0 | 4.95% |
| 18:00-20:00 | 105 | 869 | 576 | 27 | 5.23% |
| 20:00-22:00 | 134 | 814 | 605.5 | 23.0 | 5.39% |
| 22:00-24:00 | 221 | 668 | 594 | 22 | 5.70% |

14–16 h présente la meilleure médiane observée (902 impressions, 119 posts). 12–14 h : 768,5 (28 posts) ; 10–12 h : 716 (52 posts). 16–18 h : 234 (338 posts), mais 287 sont des charts Spotify. Les sujets, les formats, les réponses éventuelles et les jours de lancement diffèrent selon les créneaux : ces résultats ne prouvent pas que l’heure seule cause la performance.

À catégorie textuelle constante : Apple Music, 10–12 h (18 posts) = 1 440,5 impressions médianes ; 14–16 h (15 posts) = 1 245 ; 22–24 h (12 posts) = 720,5. Les charts Spotify ont 918 impressions médianes à 14–16 h (78 posts), contre 203 à 16–18 h (287 posts), sans pouvoir contrôler les racines/réponses.

## Types de contenu

Classement automatique par expressions explicites du texte, exclusif et auditable dans le script. Ce sont des groupes exploratoires, pas des métadonnées officielles X. Les records sont reconnus avant les updates albums ; un update mentionnant BEST DAY se retrouve donc parmi les records. Les cas non reconnus restent « Autres / à vérifier ». Même population antérieure au 12 octobre.

| Type | Posts | Impressions médianes | Likes médians | Taux social pondéré |
| --- | --- | --- | --- | --- |
| Site / classement interactif | 2 | 17958.0 | 98.5 | 0.82% |
| YouTube | 18 | 1332.0 | 96.5 | 7.12% |
| Apple Music | 113 | 876 | 51 | 6.52% |
| Paliers de streams | 15 | 867 | 81 | 4.21% |
| Autres / a verifier | 59 | 721 | 37 | 7.46% |
| Top chansons Spotify | 24 | 669.5 | 22.5 | 3.07% |
| Gainers Spotify | 44 | 644.0 | 21.0 | 4.60% |
| Recap Spotify | 25 | 606 | 22 | 4.06% |
| Depassements entre chansons | 73 | 601 | 25 | 3.86% |
| Top eras Spotify | 20 | 598.5 | 19.0 | 4.60% |
| Updates streams albums | 100 | 564.5 | 20.0 | 5.46% |
| Updates streams chansons | 15 | 557 | 22 | 5.05% |
| Records de streams | 218 | 536.5 | 22.0 | 5.18% |
| Charts Spotify | 391 | 328 | 12 | 5.39% |
| iTunes | 240 | 120.5 | 3.0 | 6.46% |

## Pistes à tester

- Réserver davantage de place aux annonces de paliers, YouTube et aux vrais événements : leurs exemples les plus forts suscitent beaucoup de réactions. Les moyennes seules sont sensibles aux gros succès isolés.
- Examiner les séries iTunes : 240 posts anciens, 120,5 impressions et 3 likes médians. Leur faible portée observée peut aussi refléter une publication en réponses ; vérifier ce statut avant une décision de réduction.
- Tester 10–16 h pour les contenus comparables qui peuvent être programmés. Ne pas retarder une actualité urgente sur la seule base de ces associations.
- Évaluer les annonces du site par leurs clics : le post du 22 septembre apporte 458 clics URL (2,12% de ses 21 598 impressions), indépendamment de sa note sociale.
- Pour vérifier une baisse de portée liée au volume, il faut comparer des posts de même type et statut de thread sur des fenêtres de mesure égales. Cet export ne permet pas une conclusion causale.

## Fichiers associés

`posts_analytics_enriched.csv` : données source intactes avec heure UTC/Madrid, catégorie, taux et note. `daily_analytics.csv`, `hourly_analytics.csv`, `category_analytics.csv` : tableaux détaillés. Le fichier source des Downloads reste inchangé. Aucun post ni paramètre de publication n’a été modifié.
