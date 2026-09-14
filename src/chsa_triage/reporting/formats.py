"""Mise en forme des chiffres des livrables, et lecture des fichiers de résultats.

Le rapport technique et le support de soutenance affichent les mêmes mesures, et
les mettent en forme par les quatre fonctions réunies ici. Une copie de chaque
côté dériverait : il suffirait que le pourcentage supprime la décimale inutile
d'un côté et pas de l'autre pour que deux livrables tenus de montrer le même
chiffre le montrent différemment, sans que rien ne le signale.
"""

from __future__ import annotations

import json
from pathlib import Path

from chsa_triage.utils import chemin_pour_journal, get_logger

logger = get_logger("livrables")


def nombre_fr(valeur: float, decimales: int = 1) -> str:
    """Formate un nombre à la française : virgule décimale.

    Le rapport et la soutenance sont en français. Écrire « 9.71 Go » à côté de
    « 0,80 » dans le même tableau est une faute de typographie, et elle se voit.
    """
    return f"{valeur:.{decimales}f}".replace(".", ",")


def pourcentage(valeur: float | None, decimales: int = 1) -> str:
    """Pourcentage à la française, sans décimale inutile.

    « 100 % » plutôt que « 100,0 % », et la virgule partout ailleurs.
    """
    if valeur is None:
        return "non mesuré"
    return nombre_fr(valeur * 100, decimales).removesuffix(",0") + " %"


def milliers(valeur: int) -> str:
    """Sépare les milliers par une espace, comme le veut l'usage français."""
    return f"{valeur:,}".replace(",", " ")


def lire_resultats(chemin: Path) -> dict | None:
    """Lit un fichier de résultats, ou renvoie None s'il n'a pas encore été produit.

    L'absence est journalisée : un livrable construit sur des mesures manquantes
    doit le dire au moment où il les cherche, pas au moment où le lecteur trouve
    un tiret dans un tableau.
    """
    if not chemin.exists():
        logger.warning("Fichier absent : %s", chemin_pour_journal(chemin))
        return None
    return json.loads(chemin.read_text(encoding="utf-8"))
