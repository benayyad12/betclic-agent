"""BetclicAgent — agent de veille des cotes Betclic Côte d'Ivoire.

Collecte les cotes du marché ivoirien via le backend gRPC-web public de Betclic,
détecte les opportunités (marges anormales, écarts) et alerte.

⚠️ Ce projet NE PLACE JAMAIS DE PARI. Les CGU Betclic interdisent l'automatisation
du placement ; l'usage ici est la collecte et l'analyse de données publiques.
"""

__version__ = "0.1.0"
