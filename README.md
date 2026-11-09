# BetclicAgent

Agent de veille des cotes **Betclic Côte d'Ivoire** : collecte les cotes du marché ivoirien,
analyse les marges des marchés et alerte sur les opportunités.

> ⚠️ **Ce projet ne place JAMAIS de pari.** Les CGU de Betclic interdisent l'automatisation
> du placement ; l'usage ici est la **collecte et l'analyse de données publiques**.
> Voir [`docs/CGU-RISQUES.md`](docs/CGU-RISQUES.md).

---

## Pourquoi ce projet

Le pipeline [Broker](../Broker) dépend de sources à quota limité (OddsPapi : 250 req/jour,
api-football : 100 req/jour). Betclic expose un backend **public, sans clé, sans
authentification**, qui alimente `betclic.fr`, `betclic.ci` et `betclic.sn` — et dont le
marché servi dépend uniquement d'en-têtes HTTP.

Résultat : une source de cotes **gratuite et non quota-limitée** pour le marché ivoirien.

## Fonctionnalités

- 📋 **Collecte** : matchs par sport (20+ sports), en direct ou à venir, recherche
- 🗓️ **Journée** : `today` pagine toute la liste du jour et la groupe par compétition
- 📊 **Analyse** : probabilités implicites et marge du bookmaker par marché
- 🎯 **Détection** : repère les marchés dont la marge sort de la fourchette habituelle
- 🔔 **Alertes** : rapport console + notification Telegram (optionnelle)
- 🌍 **Multi-marchés** : CI (défaut), FR, SN

## Installation

```bash
git clone <repo> BetclicAgent
cd BetclicAgent
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# ou : .venv/bin/python -m pip install -r requirements.txt     # Linux/macOS
cp .env.example .env                                          # puis ajuster si besoin
```

## Usage

```bash
# Marché CI par défaut
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main sports
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main matches football --limit 10
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main today football          # matchs du jour (complet)
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main analyze <match_id>
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main scan football --limit 10

# Autre marché
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main --market FR matches football
```

### `today` — matchs du jour

Le backend ne renvoie que 40 matchs par appel : `today` **pagine** jusqu'à épuisement,
filtre la journée (dates en UTC = heure locale en CI) et groupe par compétition.
Chaque appel coûte ~15 s (le serveur ne ferme jamais le flux) → compter ~1 min pour
une journée complète.

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main today football
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main today football --json data/matches_today.json
PYTHONPATH=src .venv/Scripts/python.exe -m betclic_agent.main today tennis --date 2026-09-18 --max-pages 5
```

Test de bout en bout :

```bash
.venv/Scripts/python.exe scripts/smoke_test.py
.venv/Scripts/python.exe -m pytest tests/ -q
```

### Rapports hors ligne

Ces deux scripts travaillent sur le JSON déjà collecté (aucune requête réseau) :

```bash
.venv/Scripts/python.exe scripts/report_today.py                 # rapport console (affiches + direct)
.venv/Scripts/python.exe scripts/report_today.py --full          # toutes les compétitions
.venv/Scripts/python.exe scripts/build_today_widget.py           # widget HTML filtrable (data/today.html)
```

## Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `BETCLIC_MARKET` | `CI` | Marché ciblé : `CI`, `FR`, `SN` |
| `REQUEST_DELAY_S` | `1.5` | Délai entre requêtes (usage raisonnable) |
| `MIN_MARGIN_PCT` | `6.0` | Borne basse de la présélection (marge basse, pas un signal de valeur) |
| `MAX_MARGIN_PCT` | `12.0` | Marge haute → marché ignoré |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Alertes Telegram (optionnel) |

## Architecture

```
src/betclic_agent/
├── config.py       Paramètres (.env) — ne divulgue jamais les secrets dans les logs
├── client_ci.py    Client Betclic paramétré sur un marché (CI par défaut)
├── markets.py      Probabilités implicites, marges, présélection par marge
├── devig.py        Dé-margement : proportionnel, puissance, Shin
├── timing.py       Statut temporel estimé (le backend n'en fournit aucun)
├── notifier.py     Rapport console + alertes Telegram
└── main.py         CLI (sports, matches, today, analyze, scan)
```

Détails dans [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Fiabilité des marges — règle importante

Une marge n'a de sens que si les issues d'un marché sont **mutuellement exclusives** et
**couvrent tous les cas**. Les autres marchés produisent des chiffres trompeurs :

| Marché | Marge | Interprétable ? |
|---|---|---|
| `Résultat du match` (3 issues) | 15,45 % | ✅ oui |
| `Les 2 équipes marquent` (2 issues) | 15,48 % | ✅ oui |
| `Double chance` (3 issues qui se recouvrent) | ~115 % | ⚠️ non |
| `Nombre total de buts` (5 paires Over/Under) | ~390 % | ⚠️ non |
| `L'équipe possède 2 buts d'avance` (partiel) | négative | ⚠️ non |

L'agent marque chaque marché `reliable: true/false` et **n'alerte que sur les fiables**.

## Statut des matchs — ce que le backend dit (et ne dit pas)

Le champ que la lib expose sous le nom `is_live` **ne signifie pas que le match est en
cours**. Constat du 17/09/2026 sur le marché CI, 103 matchs du jour :

| Constat | Nombre |
|---|---|
| Matchs marqués « live » avec un coup d'envoi **à venir** | 18 / 19 |
| Matchs à l'heure passée **non** marqués « live » | 12 |

Il coïncide avec les affiches les plus médiatisées (EFL Cup, LaLiga, championnat du
Kazakhstan…) : il est donc exposé sous le nom `live_stream` (« diffusion/offre live chez
le bookmaker »), jamais comme un statut.

Faute de statut fiable côté backend, `timing.match_status()` **estime** le statut
(`upcoming` / `in_play` / `past`) à partir de l'heure de coup d'envoi, avec une fenêtre de
jeu de 3 h. Conséquence assumée : un match reporté est classé `in_play`/`past` à tort.

## Limites connues

- Dépend d'un backend **non officiel** : il peut changer sans préavis (le projet
  [`TapsHTS/betclic-api`](https://github.com/TapsHTS/betclic-api) maintient le protocole)
- Une marge basse est une **présélection**, pas une Value Bet : elle n'indique ni une espérance positive ni une erreur de cote
- Pas de comparaison multi-bookmakers intégrée (à brancher avec Broker)

## Licence et crédits

Le protocole gRPC-web Betclic est implémenté par
[`TapsHTS/betclic-api`](https://github.com/TapsHTS/betclic-api) (MIT), utilisé comme
dépendance. Ce projet n'est **ni affilié ni approuvé par Betclic**.
