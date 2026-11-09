# CGU et risques — à lire avant toute utilisation

## La ligne rouge

| Activité | Statut | Pourquoi |
|---|---|---|
| **Lire** les cotes (collecte, analyse) | ✅ Autorisé en pratique | Données publiques, aucune authentification |
| **Placer** des paris automatiquement | ⛔ **Interdit** | Violation explicite des CGU ; détection → compte fermé et fonds bloqués |

Ce projet implémente **uniquement la lecture**. Toute contribution qui ajoute du
placement automatique (connexion au compte, validation de mise, clic automatisé) doit
être refusée : elle expose le compte et les fonds de l'utilisateur.

## Ce que dit la source

Le paquet `betclic-api` dont dépend ce projet précise dans son README :

> - Do **not** use this for automated betting or commercial services without Betclic's
>   explicit permission.
> - Respect reasonable rate limits — do not hammer the API with concurrent requests.
> - This library is intended for **personal, educational, and research use only**.

## Ce que confirment les praticiens

Sur r/algobetting et dans les synthèses de discussions Reddit (2026), le problème
opérationnel n°1 des utilisateurs d'agents de paris **n'est pas la précision des
prédictions** — c'est :

- les **limites de mise** imposées par les bookmakers aux comptes gagnants ;
- la **fermeture de comptes** suspects d'automatisation ;
- la difficulté à retirer ses fonds après détection.

Un agent qui place des paris expose donc son utilisateur à perdre l'accès à son compte —
et potentiellement le solde qu'il contient.

## Règles d'usage de ce projet

1. **Lecture seule.** Ne jamais ajouter de code d'authentification ou de placement.
2. **Cadence raisonnable.** `REQUEST_DELAY_S` ≥ 1 s ; pas de requêtes concurrentes.
3. **Pas de revente.** L'usage commercial des données n'est pas couvert par les CGU.
4. **L'utilisateur reste décideur.** L'agent propose des signaux ; le placement manuel
   est un choix humain.
5. **Aucun secret dans le dépôt.** Tokens et identifiants restent dans `.env` (ignoré par git).

## Résumé

```
COLLECTER  →  ANALYSER  →  ALERTER  →  (l'humain décide et place)
                                        ▲
                                        └── seul maillon non automatisé — volontairement
```
