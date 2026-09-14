"""Cohérence entre le gabarit du rapport et le script qui le remplit.

Le rapport est produit par substitution : le gabarit pose des marqueurs, le
script leur donne une valeur. Les deux fichiers vivent côte à côte et se
modifient séparément — ajouter une phrase chiffrée dans le gabarit sans câbler
son marqueur livre un PDF où il manque un nombre, et personne ne le voit avant la
soutenance.

Ces tests sont statiques : ils ne construisent pas le rapport, ils comparent les
deux fichiers. Aucune mesure n'est nécessaire.
"""

from __future__ import annotations

import re

import pytest

from chsa_triage.config import PATHS

GABARIT = PATHS.reports / "rapport_technique.template.md"
SCRIPT = PATHS.root / "scripts" / "09_build_report.py"
ESTIMATEURS = ("wilson_interval", "clopper_pearson")


@pytest.fixture(scope="module")
def marqueurs() -> set[str]:
    texte = GABARIT.read_text(encoding="utf-8")
    return {m.strip("{}") for m in re.findall(r"\{\{[A-Z0-9_]+\}\}", texte)}


@pytest.fixture(scope="module")
def cles_du_script() -> set[str]:
    """Noms de marqueurs cités comme clés dans le script de construction."""
    source = SCRIPT.read_text(encoding="utf-8")
    return set(re.findall(r'"([A-Z][A-Z0-9_]+)"', source))


def test_le_gabarit_pose_des_marqueurs(marqueurs):
    """Garde-fou du test lui-même : une regex cassée le rendrait toujours vert."""
    assert len(marqueurs) > 40
    assert "TABLEAU_RESULTATS" in marqueurs


def test_chaque_marqueur_du_gabarit_est_cable(marqueurs, cles_du_script):
    non_cables = sorted(marqueurs - cles_du_script)
    assert non_cables == [], (
        "Ces marqueurs du gabarit n'ont pas de valeur dans scripts/09_build_report.py : "
        + ", ".join(non_cables)
    )


def test_le_generateur_ne_nomme_aucun_estimateur_d_intervalle():
    """Tout intervalle publié passe par `intervalle_de_proportion`.

    C'est cette fonction qui arbitre entre Wilson et Clopper-Pearson selon
    l'effectif, et qui ne rend rien en dessous de six cas. Appeler un estimateur
    directement rouvre le choix au cas par cas : deux chiffres voisins du même
    rapport se retrouvent bornés par deux méthodes différentes, alors que le
    rapport publie la règle qui en désigne une seule.
    """
    nomme_un_estimateur = re.compile(r"\b(?:" + "|".join(ESTIMATEURS) + r")\b")
    fautives = [
        f"ligne {numero} : {ligne.strip()}"
        for numero, ligne in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), start=1)
        if nomme_un_estimateur.search(ligne)
    ]
    assert fautives == [], (
        "scripts/09_build_report.py nomme un estimateur au lieu de passer par "
        "intervalle_de_proportion :\n" + "\n".join(fautives)
    )


def test_le_gabarit_n_ecrit_aucun_nombre_a_l_anglaise():
    """Le rapport est en français : la virgule décimale, partout.

    Les numéros de version et les noms de modèles en sont exclus : `Qwen3-1.7B`
    et `CC BY 4.0` ne sont pas des mesures.
    """
    texte = GABARIT.read_text(encoding="utf-8")
    versions = re.compile(
        r"(?:Qwen3-|v|\bCC BY |Apache-|python_requires|torch |transformers |trl )"
    )
    fautifs = [
        nombre
        for ligne in texte.splitlines()
        for nombre in re.findall(r"(?<![\w.-])\d+\.\d+(?![\w.-])", ligne)
        if not versions.search(ligne)
    ]
    assert fautifs == [], f"Nombres à séparateur décimal anglais dans le gabarit : {fautifs}"


def test_le_gabarit_n_utilise_pas_de_balise_html():
    """Le moteur PDF échappe le HTML : une balise s'imprimerait telle quelle.

    Ce qui est entre accents graves est hors de cause : le moteur l'échappe puis
    le rend en chasse fixe, si bien que `<PERSON>` s'affiche bien tel quel — et
    c'est précisément l'effet recherché quand le rapport cite un masquage.
    """
    texte = re.sub(r"`[^`\n]*`", "", GABARIT.read_text(encoding="utf-8"))
    balises = re.findall(r"<(?!\|)[a-zA-Z/][^>\n]*>", texte)
    assert balises == [], f"Balises HTML qui s'imprimeraient littéralement : {balises}"


def test_le_rapport_nomme_exactement_les_controles_de_securite_publies():
    """Le tableau de sécurité du rapport se construit sur ces libellés.

    Un contrôle ajouté au module de sécurité et oublié ici ne serait tout
    simplement pas publié : le rapport afficherait un tableau incomplet sans que
    rien ne le signale. C'est arrivé pour la structure de réponse incomplète.
    """
    import importlib.util

    from chsa_triage.evaluation.safety import SafetyReport, summarize

    specification = importlib.util.spec_from_file_location("rapport", SCRIPT)
    rapport = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(rapport)

    vide = SafetyReport(False, False, False, (), False, False)
    publies = set(summarize([vide])) - {"n"}
    assert set(rapport.LIBELLES_SECURITE) == publies


def test_le_gabarit_annonce_le_bon_nombre_de_controles():
    """« Quatre contrôles » au-dessus d'un tableau qui en montrait six."""
    from chsa_triage.evaluation.safety import SafetyReport

    nombre = len(SafetyReport.__dataclass_fields__)
    mots = {4: "Quatre", 5: "Cinq", 6: "Six", 7: "Sept"}
    texte = GABARIT.read_text(encoding="utf-8")
    assert f"**{mots[nombre]} contrôles**" in texte


def test_le_rapport_nomme_exactement_les_controles_de_robustesse_publies():
    """Même garde que pour la sécurité : un contrôle ajouté et non nommé ici
    disparaîtrait simplement du tableau publié."""
    import importlib.util

    from chsa_triage.evaluation.robustness import check, summarize

    specification = importlib.util.spec_from_file_location("rapport", SCRIPT)
    rapport = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(rapport)

    controle = check("saisie_minimale", "mal", "Niveau de priorité : URGENCE_MODEREE", True)
    publies = set(summarize([controle])) - {"n", "cas_non_conformes"}
    assert set(rapport.LIBELLES_ROBUSTESSE) == publies
