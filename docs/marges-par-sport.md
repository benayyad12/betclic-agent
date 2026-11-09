# Quel sport rapporte le plus ? — données et méthode

> Question posée le 17/09/2026. Deux lectures, deux réponses — et une limite : **aucun
> historique local n'existe encore** (aucune cote n'est stockée en base). On distingue donc
> les **données externes vérifiables** de **nos mesures du jour**.

## 1. Côté bookmaker (ce que le sport rapporte à l'opérateur)

Source primaire : *Nevada Sports Betting Totals: 1984-2025*, Center for Gaming Research,
University Libraries, UNLV, février 2026 — données extraites des rapports du Nevada Gaming
Control Board de 1984 à 2025 (PDF conservé dans `data/sources/unlv_sportsbetting.pdf`).

### Taux de prélèvement réalisé (« win % », alias hold)

| Catégorie | Hold moyen cumulé | Période |
|---|---|---|
| **Parlays (combinés)** | **30,74 %** | depuis 1992 |
| Hockey | 5,38 % | depuis 2020 |
| **Football** | **5,41 %** | depuis 1992 |
| Basketball | 5,26 % | depuis 1992 |
| Baseball | 4,14 % | depuis 1992 |
| Other (golf, tennis, MMA, boxe, auto…) | 6,74 % | depuis 1992 |
| *Tous paris confondus* | *5,73 %* | *depuis 1984* |

### Montants gagnés par l'opérateur (cumulé, en dollars)

| Catégorie | Misé (drop) | Gagné (win) |
|---|---|---|
| Football | 48,998 Md$ | **2,649 Md$** |
| Basketball | 37,178 Md$ | 1,956 Md$ |
| Baseball | 25,468 Md$ | 1,054 Md$ |
| Other | 10,741 Md$ | 724 M$ |
| **Parlays** | 1,972 Md$ | 606 M$ |
| Hockey | 2,247 Md$ | 121 M$ |

### Dernière année pleine (2025)

| Catégorie | Misé | Gagné | Hold |
|---|---|---|---|
| **Football** | 2 550,6 M$ | **203,5 M$** | 7,98 % |
| Basketball | 2 463,5 M$ | 144,9 M$ | 5,88 % |
| Baseball | 1 498,9 M$ | 107,6 M$ | 7,18 % |
| Other | 1 176,6 M$ | 108,4 M$ | 9,21 % |
| Hockey | 363,8 M$ | 30,9 M$ | 8,50 % |
| Parlay | 18,8 M$ | 6,1 M$ | **32,59 %** |
| **Total** | **8 072,7 M$** | **601,4 M$** | **7,45 %** |

### Lecture

- **En volume et en montant absolu : le football**, très largement (203,5 M$ gagnés en 2025,
  soit 33,8 % du total ; 2,65 Md$ cumulés).
- **En taux : les paris combinés (parlay)**, à ~31 %, sans comparaison — un simple « 3 matchs
  combinés » vaut plus en marge que n'importe quel sport.
- Parmi les sports simples, le hold le plus élevé se trouve sur les **catégories "other"**
  (9,21 % en 2025 : tennis, golf, MMA, boxe) puis le hockey (8,50 %). Le **baseball** est le
  moins rentable en taux (4,14 % sur 33 ans).
- **Pour le parieur, lire l'inverse** : la marge la plus basse = là où il perd le moins
  structurellement (baseball, puis basket, puis football).

## 2. Côté parieur (ce que Betclic CI prélève réellement)

Mesuré le 17/09/2026 par `scripts/margins_by_sport.py` (marché CI, 3 matchs par sport, marchés
exhaustifs uniquement) :

| Sport | Marchés fiables | Marge médiane | Amplitude |
|---|---|---|---|
| Basketball (Vainqueur du match) | 3 | **9,09 %** | 6,61 – 12,02 % |
| Football (Résultat du match) | 3 | **11,46 %** | 10,92 – 14,99 % |
| Tennis (Vainqueur du match) | 1 | **15,78 %** | — |

⚠️ Échantillon minuscule et **compétitions mineures** (les premiers matchs de la liste du jour
sont des tours préliminaires) : ces chiffres disent « sur quoi Betclic CI prélève le plus
aujourd'hui », pas une vérité stable. Le tennis ne repose que sur **un** marché mesuré — à
reprendre avec un échantillon plus large (ses appels sont aussi les plus lents : ~18 marchés
retournés par match, plusieurs minutes par appel).

### Effet compétition : plus fort que l'effet sport

Même sport (football), même jour, même bookmaker — mais les affiches de Ligue Europa :

| Affiche (17/09/2026, 19:00) | Marge « Résultat du match » (3 issues) | « Les 2 équipes marquent » |
|---|---|---|
| Juventus – NEC Nimègue | **5,89 %** | 9,17 % |
| Celtic – Ferencváros | **6,09 %** | 8,96 % |
| Besiktas – Marseille | **6,18 %** | 9,22 % |
| Real Sociedad – Bournemouth | **8,44 %** | 8,89 % |
| *Compétitions mineures (3 matchs mesurés plus haut)* | *11,46 %* | *—* |

**Autrement dit : la compétition pèse au moins autant que le sport.** Le football de Ligue
Europa est facturé ~6 % quand des tours préliminaires asiatiques sont facturés ~11,5 %, et le
basket de compétition mineure (9,09 %) se situe entre les deux. Comparer « le football » et
« le basket » sans contrôler le niveau de compétition n'a donc pas de sens.

Bonne nouvelle de méthode : le filtre a correctement rejeté les faux marchés
(« Résultat du match – Extra gains à chaque but d'écart », 2 issues, marges annoncées
0,3 % / 1,04 % / 2,02 % = pièges non exhaustifs), alors que le vrai 1X2 à 3 issues passait ✅.

### Pourquoi les deux chiffres diffèrent

La marge théorique (overround) d'un marché principal est de l'ordre de 5-8 % ; le hold
**réalisé** est plus élevé parce qu'il intègre les combinés, les paris live et le
comportement des parieurs. Comparer 5,4 % (hold football) à une marge 1X2 mesurée de ~7 %
n'a donc rien d'incohérent.

## Limites

- Les données Nevada décrivent un marché régulé américain : elles donnent un **ordre de
  grandeur et une hiérarchie entre sports**, pas les marges de Betclic CI.
- Nos mesures sont un **instantané** : elles varient selon la compétition, l'heure et
  l'engouement. Un historique exploitable suppose de stocker les marges dans le temps
  (extension « Historisation » prévue dans `docs/ARCHITECTURE.md`).
