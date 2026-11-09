class EngineError(Exception):
    """Base des erreurs du moteur d'analyse."""


class UnknownTeam(EngineError):
    """Équipe absente du jeu historique : aucune estimation n'est produite."""


class InsufficientData(EngineError):
    """Historique trop court pour estimer le modèle : aucune valeur n'est inventée."""
