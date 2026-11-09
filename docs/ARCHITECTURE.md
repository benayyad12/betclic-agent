# Architecture

## Vue d'ensemble

```
                    ┌──────────────────────────────┐
                    │  offering.begmedia.com        │
                    │  (backend gRPC-web public)    │
                    │  FR · CI · SN · …             │
                    └──────────────┬───────────────┘
                                   │  en-têtes x-bg-regulation
                                   ▼
                    ┌──────────────────────────────┐
                    │  client_ci.BetclicMarketClient│
                    └──────────────┬───────────────┘
                                   │ matchs + marchés
                                   ▼
   ┌───────────────┐      ┌────────────────────┐      ┌──────────────┐
   │  config.py    │─────►│   markets.py       │─────►│ notifier.py  │
   │  (.env)       │      │ marges · détection │      │ console + TG │
   └───────────────┘      └────────────────────┘      └──────────────┘
                                   ▲
                                   │
                          ┌────────┴────────┐
                          │    main.py      │  CLI
                          └─────────────────┘
```

## Modules

| Module | Responsabilité unique |
|---|---|
| `config.py` | Charger et exposer les paramètres depuis `.env` (sans fuiter de secrets) |
| `client_ci.py` | Parler à l'API Betclic pour un marché donné ; simplifier les réponses |
| `markets.py` | Calculer les probabilités implicites, les marges, et présélectionner les marchés à marge basse (`find_low_margin_markets`, alias `find_opportunities`) |
| `devig.py` | Retirer la marge d'un marché : méthodes proportionnelle, puissance et Shin (somme = 1) |
| `timing.py` | Estimer le statut temporel des matchs (le backend n'en fournit aucun de fiable) |
| `notifier.py` | Présenter les résultats (console) et alerter (Telegram) : HTML échappé, échecs journalisés, alertes dédoublonnées (`alerts_sent`) |
| `staking.py` | Gestion de mise : flat, Kelly fractionné plafonné, simulation Monte Carlo du risque de ruine |
| `backtest_engine/` | Métriques de backtest (Brier, ROI, CLV, bootstrap…) et walk-forward sans fuite |
| `db/` | Persistance SQLAlchemy 2 : matchs, marchés, historique des cotes en ajout seul, runs |
| `alembic/` | Migrations du schéma (`alembic upgrade head`, URL lue depuis `DATABASE_URL`) |
| `main.py` | Exposer le tout en ligne de commande |

## Décisions de conception

### 1. Le marché est un paramètre, pas un fork
`betclic.fr`, `betclic.ci` et `betclic.sn` partagent le même backend. Le marché servi
dépend d'en-têtes HTTP (`x-bg-regulation`, `Origin`, `Referer`) — le client les
surcharge après l'initialisation, sans dupliquer le code du protocole.

### 2. La fiabilité des marges est explicite
La marge (`Σ 1/cote − 1`) n'est interprétable que pour des issues **mutuellement
exclusives et exhaustives**. Deux conditions sont vérifiées :

1. le **type** de marché est reconnu exhaustif (`EXHAUSTIVE_MARKET_RULES`) ;
2. le **nombre de sélections** correspond exactement (3 pour un 1X2, 2 pour un Oui/Non).

Un marché non conforme porte `reliable: false` et est exclu de la détection : mieux vaut
ne rien alerter que d'alerter sur un chiffre faux.

### 3. Pas de placement de pari — jamais
Le paquet `betclic-api` ne permet que la **lecture**. Aucune fonction d'écriture, de
connexion ou de mise n'est utilisée. C'est une contrainte de conception, pas un oubli :
voir `CGU-RISQUES.md`.

### 4. Dépendance au protocole, pas copie
Le protocole gRPC-web (encodage protobuf manuel, trames binaires) est délégué à
`betclic-api` (MIT) plutôt que recopié : le jour où un champ change, une seule
dépendance à mettre à jour.

### 5. Le « live » du backend n'est pas un statut
Le client renvoie 40 matchs par appel au maximum : une journée entière se collecte par
**pagination** (`collect_all_matches`), avec arrêt après N pages hors du jour visé pour
borner le coût (~15 s par appel, le serveur ne ferme jamais le flux).

Le champ `is_live` de la lib est exposé sous le nom `live_stream` : vérifié le
17/09/2026, il ne dit **pas** que le match est en cours (18 matchs sur 19 marqués ainsi
avaient un coup d'envoi futur). Le statut réel est estimé par `timing.match_status()` à
partir de l'heure — estimation assumée, documentée dans le README.

## Interface web temps réel (`api.py`, `static/index.html`)

- **Collecteur unique par (sport, marché, jour)** (`_run_hub`) : interroge Betclic toutes les
  `COLLECT_INTERVAL_S` (15 s), partagé par tous les onglets. Le nombre de requêtes ne dépend pas
  du nombre de navigateurs ouverts.
- **Poussée SSE** (`/api/stream/{sport}`) : l'état courant part à la connexion, puis seuls les
  changements sont envoyés (`data`), ou un `tick` de contrôle si rien n'a bougé. Erreurs : `error`.
- **Repli REST** : `/api/today/{sport}` reste disponible et sert au rafraîchissement si le flux tombe.
- **Affichage instantané** : le dernier snapshot est gardé dans `localStorage` et affiché avant la réponse.
- **Cache REST** : cotes d'un match en stale-while-revalidate (`MATCH_CACHE_TTL_S`, `MATCH_STALE_MAX_S`).

## Flux d'une journée (`today`)

1. `collect_all_matches(sport, until_day)` → pagination jusqu'à sortie du jour visé
2. `matches_on(matches, day)` → filtre la journée (dates backend en UTC = heure locale)
3. `sort_by_time()` → tri par coup d'envoi puis compétition
4. `match_status()` → répartition en cours / à venir / terminés
5. `--json` → export réutilisable par `scripts/report_today.py` et
   `scripts/build_today_widget.py` (rapports hors ligne, zéro requête)

## Mesures du backend (marché CI, 17/09/2026)

| Mesure | Valeur |
|---|---|
| Taille de page imposée par `get_matches` | 40 matchs |
| Durée d'un appel | ~15 s (le serveur ne ferme jamais le flux → fin sur timeout de lecture) |
| Catalogue football complet | ~1 436 matchs, 36 pages, jusqu'à juillet 2030 |
| Journée type (17/09) | 103 matchs, atteints en ~1 min 34 via `until_day` |
| Ordre des pages | globalement chronologique mais **pas strict** → arrêt après 3 pages hors jour |

Conséquence pratique : ne jamais boucler « jusqu'à la fin » pour une seule journée (≈9 min
pour 1 436 matchs) ; `until_day` divise le coût par ~6 et a ramené 5 matchs que la
collecte plafonnée à 30 pages avait manqués.

## Flux d'un scan

1. `collect_matches(sport, limit)` → liste de matchs (id, nom, compétition, diffusion)
2. Pour chaque match : `get_main_markets(id)` → `{marché: [sélections]}`
3. `analyze_markets()` → marges + flag `reliable`
4. `find_low_margin_markets()` → présélection des marchés fiables dans la fourchette de marge (pas une Value Bet)
5. `print_report()` + `alert_opportunities()` (Telegram si configuré)
6. `REQUEST_DELAY_S` entre chaque match (politesse réseau)

Le pré-chargement web (`api.py`, `_prefetch_markets`) tourne 8 matchs à la fois au maximum (`PREFETCH_CONCURRENCY`), chaque requête espacée de `REQUEST_DELAY_S`. Test : `test_prefetch_never_exceeds_concurrency_cap`.

Les pages de liste du jour sont récupérées à 2 maximum en parallèle (`LIST_CONCURRENCY`) : c'est un
écart assumé à la règle « une requête à la fois », choisi pour la rapidité. Le pré-chargement des cotes reste séquentiel.

## Backtest de référence (`historical.py`, `backtest.py`)

- Données : `xgabora/Club-Football-Match-Data` (MIT), 1X2 pré-match + Elo ClubElo.
  Téléchargées dans `data/` (ignoré par git). Pas de xG, pas de cotes Betclic.
- Modèles : `market` (cotes dé-marginées) et `elo` (logistique Elo, nul fixe à 27 %).
- Lancement : `python -m betclic_agent.backtest --division E0`
- Résultat Premier League 2019+ (2 858 matchs) : le modèle Elo est moins bon que le
  marché (Brier 0.594 contre 0.572). Sa règle de valeur perd 5.7 % par pari
  (IC 95 % [−13.4 %, +1.7 %]). Aucune rentabilité n'est démontrée.
- Le ROI est optimiste : les cotes de référence ne sont pas celles de Betclic au moment du pari.

## Moteur de backtest (`backtest_engine/`)

- `metrics.py` : Brier, log loss, courbe de calibration, ROI, yield, drawdown maximal,
  CLV (uniquement si une cote de clôture existe), intervalle bootstrap du ROI.
  Une métrique sans donnée renvoie `None`, jamais une valeur inventée.
- `walkforward.py` : découpage chronologique ; l'entraînement précède strictement le test.
- Chaque métrique est testée sur un cas synthétique à résultat connu (`tests/test_backtest_engine.py`).

## Gestion de mise (`staking.py`)

- Flat : fraction fixe de la bankroll.
- Kelly : `(p × cote − 1) / (cote − 1)`, nul si l'espérance est négative. Fraction (ex. 0.25) et plafond par pari.
- Monte Carlo : probabilité de passer sous un seuil de bankroll. Suppose que `p` est juste ;
  si le modèle surestime `p`, le risque réel est plus élevé.
- Tests : `tests/test_staking.py`.

## Analyse de match (`soccergpt.py`, `GET /api/predict`)

- Client Sportmonks (token dans l'en-tête `Authorization`) → statistiques d'équipe
  (templates portés de SoccerGPT, Apache-2.0) → `build_match_prompt` → GPT-4o (`ask_model`, import paresseux de `openai`).
- `parse_prediction` refuse une réponse qui n'est pas un score ; `predict` met le résultat en cache 1 h.
- Erreurs : 400 équipes identiques, 503 configuration manquante, 404 équipe inconnue, 502 autre échec amont.
- Tests : `tests/test_soccergpt.py`, `tests/test_api.py` (endpoint).

## Alertes (`notifier.py`)

- Les valeurs des bookmakers sont échappées (`html.escape`) avant l'envoi en mode HTML.
- Un échec Telegram est journalisé sans le token ; il n'est pas avalé silencieusement.
- Une alerte envoyée est enregistrée dans `alerts_sent` (clé = match, marché, marge) et n'est pas renvoyée.


## Extensions prévues

- **Comparaison multi-bookmakers** : brancher les cotes Broker/OddsPapi pour détecter
  de vrais écarts inter-sites (l'arbitrage n'a de sens qu'entre bookmakers distincts)
- **Historisation** : stocker les cotes dans SQLite pour suivre les mouvements
- **Veille Reddit** : le backend de recherche Reddit n'autorise que les flux RSS et la
  recherche ; à intégrer si un besoin de veille qualitative se confirme
