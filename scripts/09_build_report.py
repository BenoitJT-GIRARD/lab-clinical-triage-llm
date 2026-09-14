"""Livrable 5 — Rapport technique : figures, texte et PDF.

Le rapport est un binaire, donc un livrable qui ne se relit pas. Il est ici
entièrement produit à partir de sources versionnées : un gabarit Markdown pour la
prose, et les fichiers de résultats pour tous les chiffres. Aucune valeur n'est
recopiée à la main, donc aucune valeur ne peut se désynchroniser du code.

Les commentaires d'analyse sont eux aussi calculés : ils comparent le modèle à
ses références et énoncent le verdict que les chiffres imposent. Si une
évaluation change, le texte change avec elle.

Usage :
    uv run python scripts/09_build_report.py
"""

from __future__ import annotations

import argparse
import re

from chsa_triage.config import MODEL, PATHS, SEED, TRAINING
from chsa_triage.data.clinical_catalogue import PRESENTATIONS
from chsa_triage.data.corpus_sources import NOMS_DE_CORPUS
from chsa_triage.data.dataset_io import SOURCES_DOCUMENTEES
from chsa_triage.evaluation.metrics import intervalle_de_proportion
from chsa_triage.reporting import figures
from chsa_triage.reporting.formats import lire_resultats as _lire
from chsa_triage.reporting.formats import milliers as _nombre
from chsa_triage.reporting.formats import nombre_fr as _d
from chsa_triage.reporting.formats import pourcentage as _pourcentage
from chsa_triage.reporting.markdown_pdf import render
from chsa_triage.utils import get_logger, revision_git

logger = get_logger("rapport")

PAGES_MAXIMUM = 20

# Marqueurs qui n'existent que si le résumé d'entraînement correspondant a été
# produit. Sans lui, ils valent « non mesuré » : c'est une mesure qui manque,
# pas un marqueur qu'on a oublié de câbler.
DEPENDENT_DE_L_ENTRAINEMENT = (
    "SFT_TRAIN_LOSS",
    "SFT_EVAL_LOSS",
    "PARAMS_ENTRAINABLES",
    "PARAMS_TOTAL",
    "PART_ENTRAINABLE",
    "SFT_DUREE",
    "SFT_MEMOIRE",
    "DPO_PRECISION",
    "DPO_DUREE",
    "DPO_MEMOIRE",
    "TABLEAU_HYPERPARAMETRES",
    "VARIANTE_RETENUE",
    "REGLAGE_DUREE",
    "REGLAGE_MEMOIRE",
)

NOMS_LISIBLES = {
    "classe_majoritaire": "Classe majoritaire",
    "toujours_urgence_vitale": "Prudence maximale",
    "regle_explicite": "Règle explicite",
    "classifieur_classique": "Classifieur classique",
    "base": "Qwen3-1.7B-Base",
    "sft": "SFT + LoRA",
    "dpo": "SFT + LoRA + DPO (adaptateur)",
    "dpo-fusionne": "SFT + LoRA + DPO (fusionné)",
}

LIBELLES_SECURITE = {
    "part_sans_defaut": "Réponses sans aucun défaut",
    "recommandation_incoherente": "Recommandation incohérente avec le niveau",
    "diagnostic_affirme": "Diagnostic affirmé (interdit par la consigne)",
    "hors_langue": "Réponse hors de la langue imposée",
    "constantes_inventees": "Constante citée mais absente du cas",
    "structure_incomplete": "Structure de réponse incomplète",
    "niveau_hors_contrat": "Niveau annoncé hors de la taxonomie",
}


def _reglage(execution: dict | None, cle: str, defaut):
    """Valeur réellement employée par une exécution, ou celle de `config.py`.

    Les résumés d'entraînement enregistrent ce qui a servi ; la configuration,
    elle, n'enregistre qu'un point de départ. Publier la seconde décrirait un
    modèle qui n'a pas été entraîné : le fine-tuning prend son rang dans la
    variante retenue par le réglage, et non dans `config.py`.
    """
    if not execution:
        return defaut
    return execution.get("hyperparametres", {}).get(cle, defaut)


def _duree(secondes: float | None) -> str:
    """Met une durée en minutes ou en heures, selon son ordre de grandeur."""
    if not secondes:
        return "non mesurée"
    if secondes < 3600:
        return f"{_d(secondes / 60, 0)} min"
    return f"{_d(secondes / 3600, 1)} h"


def _dernier_modele(evaluation: dict) -> str:
    """Nom du modèle le plus avancé réellement évalué."""
    modeles = evaluation["jeu_clinique"]["modeles"]
    # Le modèle fusionné passe en premier : c'est celui qui est livré et servi.
    for nom in ("dpo-fusionne", "dpo", "sft", "base"):
        if nom in modeles:
            return nom
    raise SystemExit("Aucun modèle évalué : lancez scripts/06_evaluate.py.")


def _progression(modeles: dict) -> dict:
    """Ne garde qu'une forme du modèle aligné pour les figures de progression.

    L'adaptateur et le modèle fusionné portent le même apprentissage : les faire
    figurer tous les deux ajouterait une quatrième colonne indiscernable de la
    troisième, et rétrécirait les matrices de confusion jusqu'à l'illisible. Leur
    comparaison a sa propre table, où elle est le sujet et non du bruit.
    """
    if "dpo-fusionne" in modeles and "dpo" in modeles:
        return {nom: valeurs for nom, valeurs in modeles.items() if nom != "dpo"}
    return modeles


def _entonnoir(rendement: dict) -> str:
    """Tableau de l'extraction, corpus par corpus, étape par étape."""
    entete = (
        "| Corpus | Entrées lues | Pas un patient | Hors bornes | Sans signe "
        "| Doublons | Extraits | Livrés |"
    )
    lignes = [entete, "|---|---|---|---|---|---|---|---|"]
    for identifiant, nom in NOMS_DE_CORPUS:
        mesure = rendement.get(identifiant)
        if not mesure or "entonnoir" not in mesure:
            continue
        perdus = mesure["entonnoir"]
        # « Extraits » est ce que l'extraction produit ; « Livrés », ce qu'il en
        # reste après plafonnement par case, anonymisation et déduplication
        # finale. Les cinq colonnes de perte s'additionnent avec « Extraits »
        # pour retrouver les entrées lues ; l'écart avec « Livrés » est l'effet
        # des étapes d'aval.
        lignes.append(
            f"| {nom} | {_nombre(mesure['entrees_lues'])} "
            f"| {_nombre(perdus['sans_presentation_de_patient'])} "
            f"| {_nombre(perdus['hors_bornes_de_longueur'])} "
            f"| {_nombre(perdus['sans_signe_identifie'])} "
            f"| {_nombre(perdus['doublons'])} "
            f"| {_nombre(perdus['retenus'])} "
            f"| **{_nombre(mesure['cas_retenus'])}** |"
        )
    return "\n".join(lignes)


def _variables_du_dataset(metadonnees: dict) -> dict[str, str]:
    """Les phrases du rapport qui décrivent ce que le jeu contient réellement.

    Elles sont calculées depuis la carte du dataset plutôt que rédigées, pour la
    même raison que les tableaux de résultats : un chiffre recopié se désynchronise
    de la reconstruction qui le produit.
    """
    statistiques = metadonnees["statistiques"]
    rendement = statistiques.get("rendement_corpus", {})
    diagnostics = metadonnees.get("diagnostics", {})
    # Une carte antérieure à l'entonnoir et aux diagnostics ferait échouer la
    # construction sur un KeyError, à cent lignes de la cause. On nomme ce qui
    # manque et on dit comment le produire.
    manquants = [
        nom
        for nom, present in (
            ("statistiques.rendement_corpus", rendement),
            (
                "statistiques.rendement_corpus[*].entonnoir",
                all("entonnoir" in m for m in rendement.values()),
            ),
            (
                "statistiques.longueur_des_descriptions",
                statistiques.get("longueur_des_descriptions"),
            ),
            ("statistiques.longueur_des_reponses", statistiques.get("longueur_des_reponses")),
            ("diagnostics", diagnostics),
        )
        if not present
    ]
    if manquants:
        raise SystemExit(
            "La carte du dataset précède cette version du rapport : "
            + ", ".join(manquants)
            + " manque. Reconstruisez le jeu : `uv run python scripts/01_build_dataset.py`."
        )

    total = statistiques["sft_total"]
    par_source = statistiques["repartition_sources"]
    generees = par_source.get("vignette_clinique", 0)
    corpus = total - generees
    detail = ", ".join(
        f"{nom} {_nombre(par_source.get(identifiant, 0))}"
        for identifiant, nom in NOMS_DE_CORPUS
        if identifiant in par_source or par_source.get(identifiant, 0) == 0
    )
    composition = (
        f"{_nombre(generees)} des {_nombre(total)} paires ({_pourcentage(generees / total, 0)}) "
        f"sont des vignettes générées à partir des {len(PRESENTATIONS)} présentations du "
        f"catalogue ; {_nombre(corpus)} ({_pourcentage(corpus / total, 0)}) sont extraites des "
        f"corpus publics — {detail}."
    )

    completions = diagnostics.get("completions", {})
    vignettes = completions.get("par_origine", {}).get("vignettes", {})
    reponses = (
        f"Les {_nombre(vignettes.get('exemples', 0))} vignettes ne portent que "
        f"{vignettes.get('reponses_distinctes', 0)} réponses attendues distinctes — une par "
        f"présentation d'origine — soit environ {_d(vignettes.get('repetition_moyenne', 0), 0)} "
        f"répétitions chacune. Sur l'ensemble du jeu, on compte "
        f"{completions.get('total', 0)} réponses distinctes."
        if vignettes
        else "Mesure non disponible."
    )

    # La sonde linéaire est commentée au chapitre Évaluation, où l'écart entre
    # les deux découpages sert à lire le jeu de test interne. Le chapitre Données
    # n'en publie donc que les bornes, par les trois marqueurs ci-dessous.
    separabilite = diagnostics.get("separabilite", {})

    fuite = diagnostics.get("fuite_par_metadonnees", {})
    if fuite:
        fuite_longue = (
            f"Un vote majoritaire sur les deux seules métadonnées du générateur — délai "
            f"d'installation et profil de constantes — retrouve le niveau dans "
            f"{_pourcentage(fuite['exactitude_du_vote_majoritaire'], 1)} des vignettes, et "
            f"{_pourcentage(fuite['part_en_cellule_homogene'], 1)} d'entre elles tombent dans "
            f"une combinaison qui ne va qu'avec un seul niveau."
        )
    else:
        fuite_longue = "Mesure non disponible."

    longueurs = statistiques["longueur_des_descriptions"]
    longueurs_de_reponse = statistiques["longueur_des_reponses"]
    sans_signe = sum(m["entonnoir"]["sans_signe_identifie"] for m in rendement.values())
    return {
        "ENTREES_LUES": _nombre(sum(m["entrees_lues"] for m in rendement.values())),
        "ENTONNOIR": _entonnoir(rendement),
        "BUDGET_DESCRIPTION": str(longueurs["budget_jetons"]),
        "MEDIANE_REPONSE": str(longueurs_de_reponse["mediane_jetons"]),
        "SANS_SIGNE": _nombre(sans_signe),
        "COMPOSITION_ORIGINE": composition,
        "REPONSES_DISTINCTES": reponses,
        # « n. d. » plutôt qu'un zéro : une exactitude de 0,000 serait une
        # affirmation, et ce n'est pas ce qu'une mesure absente veut dire.
        "SEPARABILITE_ALEATOIRE": (
            _d(separabilite["decoupage_aleatoire"], 3) if separabilite else "n. d."
        ),
        "SEPARABILITE_GROUPEE": (
            _d(separabilite["decoupage_par_presentation"], 3) if separabilite else "n. d."
        ),
        "SEPARABILITE_ECART": _d(separabilite["ecart"] * 100, 0) if separabilite else "n. d.",
        "FUITE_METADONNEES": fuite_longue,
    }


# --- Tableaux ---


def _tableau_hyperparametres(comparaison: dict) -> str:
    """Les quatre variantes, avec l'intervalle de confiance de leur exactitude.

    Sans cet intervalle, le tableau laisserait croire que quelques points d'écart
    séparent deux configurations. Sur soixante cas de validation, trois points
    font deux cas, et les intervalles se recouvrent tous. L'estimateur est celui
    que la porte d'entrée unique du projet retient pour cet effectif, et non un
    choix repris ici : deux bornes voisines du même rapport doivent venir de la
    même règle.
    """
    cas = comparaison["protocole"]["cas_evalues"]
    lignes = []
    for variante in comparaison["variantes"]:
        perte = f"{_d(variante['eval_loss'], 4)}" if variante["eval_loss"] is not None else "n/a"
        intervalle = intervalle_de_proportion(round(variante["exactitude_triage"] * cas), cas)
        lignes.append(
            f"| `{variante['variante']}` | {_nombre(variante['parametres_entrainables'])} | "
            f"{perte} | {_d(variante['exactitude_triage'], 3)}{_entre_crochets(intervalle)} | "
            f"{_d(variante['memoire_gpu_max_go'], 1)} Go |"
        )
    return "\n".join(lignes)


NOMBRES_EN_LETTRES = {1: "une", 2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six"}


def _arrets_nets_du_reglage(comparaison: dict | None) -> str:
    """Ce que les arrêts nets valent dans les fichiers de réglage livrés.

    La phrase est lue dans les fichiers de résultats, comme tous les chiffres du
    rapport : elle décrit le réglage réellement livré avec elle.
    """
    if not comparaison:
        return "ces fichiers-là n'ont pas été rejoués lors de cette exécution"
    parts = [variante["part_arrets_propres"] for variante in comparaison["variantes"]]
    combien = NOMBRES_EN_LETTRES.get(len(parts), str(len(parts)))
    if min(parts) == max(parts):
        return f"les {combien} configurations portent toutes {_d(max(parts), 3)} d'arrêts nets"
    return (
        f"les arrêts nets des {combien} configurations vont de {_d(min(parts), 3)} à "
        f"{_d(max(parts), 3)}"
    )


def _tableau_strategies(statistiques: dict) -> str:
    libelles = {
        "sous_triage": "Niveau sous-évalué",
        "recommandation_dangereuse": "Conduite à tenir qui retarde la prise en charge",
        "diagnostic_affirme": "Diagnostic présenté comme certain",
        "reponse_en_anglais": "Réponse hors de la langue imposée",
    }
    return "\n".join(
        f"| {libelles.get(cle, cle)} | {valeur} |"
        for cle, valeur in statistiques["dpo_repartition_strategies"].items()
    )


def _tableau_resultats(jeu: dict) -> str:
    """Les quatre mesures du tableau sont publiées avec leur intervalle.

    Le sous-triage se compte sur les seuls cas urgents — une quarantaine ici —
    et c'est la mesure de sécurité du projet. Le publier nu laisserait croire
    qu'un écart de deux points entre deux systèmes en est un. Le respect du
    format et le surclassement se comptent sur l'ensemble du jeu, et s'y
    exposent de la même façon : sur soixante cas, cinq points font trois cas.
    """
    lignes = []
    for nom, mesures in [*jeu["references"].items(), *jeu["modeles"].items()]:
        basse, haute = mesures["exactitude_ic95"]
        exactitude = f"{_d(mesures['exactitude'], 3)} [{_d(basse, 2)} – {_d(haute, 2)}]"
        # Le sous-triage garde l'intervalle exact que le fichier de résultats
        # publie : la règle du rapport lui impose Clopper-Pearson quel que soit
        # l'effectif, et la porte d'entrée générale rendrait Wilson à 40 cas.
        basse, haute = mesures["sous_triage_ic95"]
        # Bornes au point de pourcentage entier : la cellule du tableau tient sur
        # une ligne à cette largeur, et la décimale d'une borne d'intervalle
        # n'ajoute rien à ce qu'elle dit.
        sous_triage = (
            f"{_pourcentage(mesures['sous_triage'])} "
            f"[{_pourcentage(basse, 0)} – {_pourcentage(haute, 0)}]"
        )
        lignes.append(
            f"| {NOMS_LISIBLES.get(nom, nom)} | {exactitude} | {sous_triage} | "
            f"{_proportion_avec_intervalle(mesures['respect_format'], mesures['n'])} | "
            f"{_proportion_avec_intervalle(mesures['surclassement'], mesures['n'])} |"
        )
    return "\n".join(lignes)


def _proportion_avec_intervalle(part: float, effectif: int) -> str:
    """Écrit une proportion et l'intervalle que son effectif autorise.

    Le rapport pose qu'aucune estimation n'est publiée nue : la borne vient de la
    porte d'entrée unique du projet, qui choisit l'estimateur selon l'effectif et
    ne rend rien en dessous de six cas.
    """
    intervalle = intervalle_de_proportion(round(part * effectif), effectif)
    if intervalle is None:
        return _pourcentage(part)
    basse, haute = intervalle
    return f"{_pourcentage(part)} [{_pourcentage(basse)} – {_pourcentage(haute)}]"


def _tableau_securite(securite: dict, effectif: int) -> str:
    return "\n".join(
        f"| {libelle} | {_proportion_avec_intervalle(securite[cle], effectif)} |"
        for cle, libelle in LIBELLES_SECURITE.items()
        if cle in securite
    )


LIBELLES_ROBUSTESSE = {
    "part_conforme": "Entrées dégradées traitées sans écart au contrat",
    "respect_format": "Réponse au format attendu",
    "consigne_preservee": "Consigne système non divulguée",
    "reponse_en_francais": "Réponse dans la langue imposée",
    "arrets_propres": "Génération arrêtée par le modèle lui-même",
    "donnees_non_repetees": "Données identifiantes non renvoyées dans la réponse",
    # La mesure porte sur les dix entrées, et non sur la seule qui attend un
    # niveau précis : une entrée sans niveau attendu y compte comme conforme.
    # L'intitulé dit donc ce qui est compté, faute de quoi l'intervalle publié à
    # côté se rapporterait à un effectif de un.
    "niveau_attendu_respecte": "Niveau jamais en contradiction avec l'entrée",
}


def _tableau_robustesse(robustesse: dict | None) -> str:
    if not robustesse:
        return "| — | mesure non disponible |"
    effectif = robustesse["n"]
    return "\n".join(
        f"| {libelle} | {_proportion_avec_intervalle(robustesse[cle], effectif)} |"
        for cle, libelle in LIBELLES_ROBUSTESSE.items()
        if cle in robustesse
    )


def _commentaire_robustesse(robustesse: dict | None) -> str:
    if not robustesse:
        return "Les tests de robustesse n'ont pas été exécutés lors de cette évaluation."
    en_echec = robustesse.get("cas_non_conformes", [])
    if not en_echec:
        # Dix entrées, une par catégorie : à cet effectif, la règle publiée par le
        # rapport demande l'intervalle exact, dont la borne basse pour 10 sur 10
        # est 0,69. Conclure « le modèle ne se laisse pas détourner » sur deux
        # formulations d'injection serait affirmer une propriété de sécurité que
        # l'échantillon ne porte pas.
        intervalle = intervalle_de_proportion(robustesse["n"], robustesse["n"])
        # Sous six entrées, cette règle ne rend rien : la phrase n'annonce alors
        # aucun intervalle plutôt que d'en annoncer un vide.
        borne = (
            f", intervalle de confiance à 95 %{_entre_crochets(intervalle)}" if intervalle else ""
        )
        return (
            f"Les {robustesse['n']} entrées dégradées — une par catégorie — sont traitées sans "
            f"écart au contrat de sortie{borne}. L'échantillon situe, il ne démontre pas : "
            "la résistance à l'injection de consigne n'est éprouvée ici que sur deux "
            "formulations, et le renvoi de données identifiantes sur une seule. Un jeu "
            "adverse dédié est à construire avant tout usage réel."
        )
    accord = (
        "produit une réponse qui s'écarte"
        if len(en_echec) == 1
        else ("produisent une réponse qui s'écarte")
    )
    return (
        f"{len(en_echec)} des {robustesse['n']} entrées dégradées {accord} "
        f"du contrat de sortie : {', '.join(_nom_lisible(nom) for nom in en_echec)}. "
        "Le filet de troncature du service garantit que la consigne système n'atteint jamais le "
        "personnel soignant, mais ces cas doivent être repris avant tout usage réel."
    )


def _tableau_preferences(preferences: dict) -> str:
    """Part de paires bien ordonnées, avec son intervalle, et marge moyenne.

    La marge est une moyenne de différences de vraisemblance par jeton :
    l'évaluation n'en conserve pas la dispersion, et le rapport déclare cette
    exemption dans son tableau des règles. Le signe est écrit, y compris
    positif : c'est lui qui dit de quel côté le modèle penche.
    """
    if not preferences:
        return "| — | mesure non disponible | — |"
    lignes = []
    for nom, mesures in preferences.items():
        marge = mesures["marge_moyenne"]
        lignes.append(
            f"| {NOMS_LISIBLES.get(nom, nom)} | "
            f"{_proportion_avec_intervalle(mesures['part_bien_ordonnees'], mesures['n'])} | "
            f"{'+' if marge > 0 else ''}{_d(marge, 4)} |"
        )
    return "\n".join(lignes)


def _instrument_applicable(preferences: dict) -> str:
    """Dit si la mesure de préférences porte, avant de commenter ce qu'elle montre.

    Une part de paires bien ordonnées se lit contre le hasard, qui vaut 0,5. En
    dessous, la mesure n'est pas « mauvaise » : elle est **inversée**, et il faut
    chercher pourquoi avant d'en tirer quoi que ce soit sur l'alignement. Le
    modèle de base sert ici de témoin — s'il est lui aussi sous le hasard, le
    phénomène précède notre entraînement et ne le décrit pas.
    """
    aligne = preferences.get("dpo", {}).get("part_bien_ordonnees")
    base = preferences.get("base", {}).get("part_bien_ordonnees")
    if aligne is None or aligne >= 0.5:
        return ""

    temoin = (
        f" Le modèle de base, que notre entraînement n'a pas touché, est au même niveau "
        f"({_pourcentage(base)}) : le phénomène précède le fine-tuning et ne le décrit pas."
        if base is not None and base < 0.5
        else ""
    )
    return (
        " **Ces valeurs sont sous le hasard**, qui vaut 50 % sur des paires à deux réponses."
        + temoin
        + " L'explication la plus simple est que l'instrument ne s'applique pas à ce modèle : "
        "on compare des vraisemblances par jeton attribuées à de longues dissertations "
        "anglaises par un modèle spécialisé sur des réponses françaises courtes et "
        "structurées, dont 15 % dépassent en outre sa fenêtre et sont écrêtées. Une "
        "évaluation externe de l'alignement reste à construire — sur des préférences de même "
        "format que la tâche, c'est la leçon à retenir."
    )


def _commentaire_preferences(preferences: dict, appariee: dict | None) -> str:
    """Compare l'alignement au modèle supervisé sur le jeu de préférences externe.

    Les deux modèles ont été mesurés sur les mêmes paires : le verdict se lit sur
    les seules paires où ils divergent, par un test de McNemar. Comparer les deux
    proportions nues ferait déclarer un gain sur deux paires d'écart.
    """
    if not preferences or "sft" not in preferences or "dpo" not in preferences:
        return (
            "La comparaison demande d'évaluer le modèle supervisé et le modèle aligné dans la "
            "même exécution ; elle n'est pas disponible ici."
        )
    supervise = preferences["sft"]["part_bien_ordonnees"]
    aligne = preferences["dpo"]["part_bien_ordonnees"]
    constat = (
        f"Sur les {preferences['dpo']['n']} paires du jeu externe, le modèle supervisé en "
        f"ordonne correctement {_pourcentage(supervise)}, le modèle aligné "
        f"{_pourcentage(aligne)}."
    )
    mise_en_garde = _instrument_applicable(preferences)
    constat += mise_en_garde
    if not appariee:
        return constat + " La comparaison appariée n'a pas été produite lors de cette exécution."

    gagnees = appariee["aligne_seul"]
    perdues = appariee["supervise_seul"]
    detail = (
        f" Les deux modèles s'accordent sur {appariee['accords']} paires ; l'alignement en gagne "
        f"{gagnees} et en perd {perdues}."
    )
    if appariee["p_mcnemar"] <= 0.05 and gagnees > perdues:
        return (
            constat
            + detail
            + f" L'écart tient le test de McNemar (p = {_d(appariee['p_mcnemar'], 3)}) : "
            "l'alignement a bien déplacé la façon dont le modèle ordonne deux réponses "
            "médicales, sur un jeu qu'il n'a jamais vu."
        )
    if appariee["p_mcnemar"] <= 0.05 and perdues > gagnees:
        return (
            constat
            + detail
            + f" L'écart tient le test de McNemar (p = {_d(appariee['p_mcnemar'], 3)}) et joue "
            "contre l'alignement : nos préférences de sécurité, très spécifiques au format de "
            "triage, se transfèrent mal au jugement médical général. C'est un compromis à "
            "assumer et à surveiller."
        )
    # Le décompte des paires discordantes et le verdict du test disent la même
    # chose : les deux tiennent en une phrase, plutôt qu'en deux dont la seconde
    # récrirait la première.
    conclusion = (
        f" Les deux modèles s'accordent sur {appariee['accords']} paires, l'alignement en gagne "
        f"{gagnees} et en perd {perdues} : le test de McNemar ne conclut pas "
        f"(p = {_d(appariee['p_mcnemar'], 3)})."
    )
    # Quand l'instrument ne sait pas juger, il ne sait pas non plus établir une
    # absence de dégradation : conclure à l'innocuité de l'alignement à partir
    # d'une mesure qu'on vient de déclarer inapplicable serait se contredire
    # dans le même paragraphe.
    if not mise_en_garde:
        conclusion += (
            " Ce qui compte ici est qu'il n'a pas **dégradé** le jugement médical général du "
            "modèle, ce qui était le risque principal d'une optimisation sur des préférences "
            "aussi spécifiques."
        )
    return constat + conclusion


def _tableau_latence(banc: dict | None) -> str:
    if not banc:
        return "| — | mesure non disponible | — | — |"
    return "\n".join(
        f"| {nom.replace('concurrence_', '')} | {_d(mesures['p50_ms'], 0)} ms | "
        f"{_d(mesures['p95_ms'], 0)} ms | {_d(mesures['debit_req_par_s'], 2)} req/s |"
        for nom, mesures in banc["mesures"].items()
    )


def _tableau_modules(comparaison: dict | None) -> str:
    """Les trois jeux de modules adaptés, mesurés à protocole identique."""
    if not comparaison:
        return "| — | — | mesure non disponible | — | — |"
    lignes = []
    for entree in comparaison["configurations"]:
        gras = "**" if "tête" in entree["configuration"] else ""
        lignes.append(
            f"| {gras}{entree['configuration']}{gras} | {_nombre(entree['parametres_entrainables'])} | "
            f"{gras}{_d(entree['exactitude'], 3)}{gras} | {gras}{_d(entree['arrets_propres'], 3)}{gras} | "
            f"{gras}{entree['jetons_median']}{gras} |"
        )
    return "\n".join(lignes)


def _comparaison_moteurs(mesures: dict | None) -> str:
    """Met les deux moteurs d'entraînement en regard, mesure à l'appui.

    Le gain que le rapport annonce vient de cette comparaison rejouée, et non
    d'un chiffre repris tel quel : une performance qu'on ne peut pas rejouer ne
    vaut pas mieux qu'une impression.
    """
    if not mesures:
        return "La comparaison des deux moteurs n'a pas été rejouée lors de cette exécution."

    unsloth, transformers = mesures["unsloth"], mesures["transformers"]
    gain = 1 - unsloth["memoire_gpu_max_go"] / transformers["memoire_gpu_max_go"]
    rapport = transformers["secondes_par_pas"] / unsloth["secondes_par_pas"]
    vitesse = (
        f"{_d(rapport, 2)} fois plus rapide".replace(".", ",")
        if rapport >= 1.05
        else f"{_d(1 / rapport, 2)} fois plus lent".replace(".", ",")
        if rapport <= 0.95
        else "à vitesse comparable"
    )
    return (
        f"| Moteur | Mémoire GPU maximale | Secondes par pas |\n"
        f"|---|---|---|\n"
        f"| `transformers` + PEFT + TRL | {_d(transformers['memoire_gpu_max_go'], 2)} Go | "
        f"{_d(transformers['secondes_par_pas'], 2)} s |\n"
        f"| **Unsloth** | **{_d(unsloth['memoire_gpu_max_go'], 2)} Go** | "
        f"{_d(unsloth['secondes_par_pas'], 2)} s |\n\n"
        f"Soit {_pourcentage(gain, 0)} de mémoire en moins, {vitesse}, sur "
        f"{mesures['protocole']['pas']} pas et à configuration identique."
    )


def _jetons_compares(comparaison: dict | None) -> dict[str, str]:
    """Nombre de jetons générés avec et sans la tête de sortie, et le rapport.

    Le rapport commente ces chiffres dans son texte courant, juste à côté du
    tableau qui les affiche. Les recopier à la main exposerait à ce qu'ils se
    contredisent le jour où l'expérience est rejouée.
    """
    introuvable = {
        "JETONS_AVEC_TETE": "—",
        "JETONS_SANS_TETE": "—",
        "FACTEUR_JETONS": "—",
    }
    if not comparaison:
        return introuvable

    par_nom = {e["configuration"]: e["jetons_median"] for e in comparaison["configurations"]}
    avec = next((v for nom, v in par_nom.items() if "tête" in nom), None)
    sans = par_nom.get("projections seules")
    if not avec or not sans:
        return introuvable
    return {
        "JETONS_AVEC_TETE": str(avec),
        "JETONS_SANS_TETE": str(sans),
        "FACTEUR_JETONS": f"{_d(sans / avec, 1)}".replace(".", ","),
    }


def _tableau_livraisons(comparaison: dict | None) -> str:
    """Met l'adaptateur et le modèle fusionné en regard, ligne par ligne."""
    if not comparaison:
        return "| Exactitude | mesure non disponible | — |"

    adaptateur, fusionne = comparaison["adaptateur"], comparaison["fusionne"]

    def latence(entree: dict) -> str:
        valeur = entree.get("latence_mediane_ms")
        return f"{_d(valeur, 0)} ms" if valeur else "non mesurée"

    def poids(entree: dict) -> str:
        mo = entree.get("poids_a_telecharger_mo") or 0
        return f"{_d(mo / 1024, 2)} Go" if mo >= 1024 else f"{_d(mo, 0)} Mo"

    def exactitude(entree: dict) -> str:
        return f"{_d(entree['exactitude'], 3)}{_entre_crochets(entree['exactitude_ic95'])}"

    lignes = [
        f"| Exactitude sur le jeu clinique | {exactitude(adaptateur)} | {exactitude(fusionne)} |",
        f"| Latence médiane | {latence(adaptateur)} | {latence(fusionne)} |",
        f"| Poids à télécharger | {poids(adaptateur)} | {poids(fusionne)} |",
        f"| Modèle de base requis | `{adaptateur['modele_de_base_requis']}` | aucun |",
        "| Servable à chaud par vLLM | oui, sur le modèle supervisé fusionné | oui, seul |",
    ]
    return "\n".join(lignes)


def _commentaire_livraisons(comparaison: dict | None, cas: int) -> str:
    """Formule ce que la comparaison établit, sans chercher un vainqueur absolu.

    Le seuil se déduit de l'effectif du jeu, et non d'une constante : la phrase
    parle en cas, elle doit donc se décider en cas. Un seuil fixe de 0,02 vaudrait
    un cas sur un jeu de 60 mais six cas sur les 300 de la feuille de route, et
    annoncerait dans les deux situations « moins d'un cas » — à tort dès 60,
    puisque 1/60 vaut 0,017.
    """
    if not comparaison:
        return "La comparaison des deux livraisons n'a pas pu être menée."
    ecart = abs(comparaison["adaptateur"]["exactitude"] - comparaison["fusionne"]["exactitude"])
    cas_d_ecart = ecart * cas
    if cas_d_ecart < 1:
        return (
            "Les deux livraisons sont **indiscernables en précision** : l'écart observé vaut "
            f"moins d'un cas sur {cas}. Le choix se joue donc entièrement sur des critères "
            "d'exploitation."
        )
    return (
        f"Un écart de {_d(ecart, 3)}, soit {_d(cas_d_ecart, 1)} cas sur {cas}, sépare les deux "
        "livraisons. Il dépasse le bruit numérique attendu et justifierait de reprendre la "
        "fusion avant de livrer."
    )


def _tableau_sources() -> str:
    return "\n".join(
        f"| `{cle}` | {details['langue']} | {details['licence']} | {details['role']} |"
        for cle, details in SOURCES_DOCUMENTEES.items()
    )


# --- Commentaires calculés ---


def _verdict_contre_la_regle(ecart: float, apparie: dict | None) -> str:
    """Formule l'écart au modèle de référence à partir des tests **appariés**.

    Le verdict ne se lit pas sur le recouvrement des deux intervalles de
    confiance : ce serait un sophisme — le non-recouvrement prouve une
    différence, le recouvrement ne prouve rien — et doublement fautif ici, parce
    que les deux systèmes voient les mêmes cas. L'information est portée par les
    seuls cas où ils divergent, que la comparaison des intervalles marginaux
    jette.

    Deux tests sont rendus, dans l'ordre où un service les lit : d'abord le
    **sous-triage**, la seule faute qui met un patient en danger, puis
    l'exactitude globale. Un système de triage qui se trompe moins souvent
    dangereusement vaut mieux qu'un système qui se trompe moins souvent.
    """
    if not apparie:
        sens = "devance" if ecart > 0 else "reste en dessous de"
        return (
            f"Le modèle {sens} la règle explicite de {_d(abs(ecart) * 100, 0)} points. La "
            "comparaison appariée n'a pas été produite lors de cette exécution : l'écart est "
            "décrit, il n'est pas testé."
        )
    if "sous_triage" not in apparie or "exactitude" not in apparie:
        # Le fichier de résultats ne porte pas la comparaison appariée sur le
        # sous-triage. Le rapport pourrait s'en accommoder et n'imprimer que ce
        # qu'il trouve ; il publierait alors un verdict clinique amputé sans le dire.
        raise SystemExit(
            "Le fichier de résultats précède la comparaison appariée sur le sous-triage. "
            "Relancez `python scripts/06_evaluate.py` : c'est la comparaison qui porte "
            "l'argument clinique du rapport."
        )

    securite = apparie["sous_triage"]
    exactitude = apparie["exactitude"]
    phrases = []

    p_securite = securite["p_mcnemar"]
    ecart_securite = securite["sous_triages_reference"] - securite["sous_triages_modele"]
    if ecart_securite > 0:
        verdict = "établi" if p_securite < 0.05 else "non établi sur ce jeu"
        phrases.append(
            f"**Sur le sous-triage, la mesure qui décide**, le modèle laisse passer "
            f"{securite['sous_triages_modele']} cas urgents sur {securite['cas_urgents']} là où la "
            f"règle en laisse passer {securite['sous_triages_reference']}. Les deux systèmes voient "
            f"les mêmes cas : {securite['modele_seul']} urgences que seul le modèle attrape "
            f"contre {securite['reference_seule']} que seule la règle attrape, soit un écart "
            f"{verdict} (McNemar exact, p = {_d(p_securite, 3)})."
        )
    else:
        phrases.append(
            f"**Sur le sous-triage**, le modèle laisse passer "
            f"{securite['sous_triages_modele']} cas urgents sur {securite['cas_urgents']}, contre "
            f"{securite['sous_triages_reference']} pour la règle : il ne fait pas mieux sur la mesure "
            f"de sécurité (McNemar exact, p = {_d(p_securite, 3)})."
        )

    p_exactitude = exactitude["p_mcnemar"]
    sens = "dépasse" if ecart > 0 else "reste en dessous de"
    conclusion = (
        "et cet écart-là est établi"
        if p_exactitude < 0.05
        else "mais cet écart-là n'est pas établi sur un jeu de cette taille"
    )
    phrases.append(
        f"Sur l'exactitude globale, le modèle {sens} la règle de "
        f"{_d(abs(ecart) * 100, 0)} points, {conclusion} "
        f"({exactitude['modele_seul']} cas contre {exactitude['reference_seule']}, "
        f"p = {_d(p_exactitude, 3)})."
    )
    return " ".join(phrases)


def _commentaire_resultats(
    jeu: dict, final: str, apparie: dict | None = None, apparie_classique: dict | None = None
) -> str:
    """Énonce le verdict que les chiffres imposent, référence par référence."""
    modele = jeu["modeles"][final]
    regle = jeu["references"]["regle_explicite"]
    majoritaire = jeu["references"]["classe_majoritaire"]
    prudence = jeu["references"]["toujours_urgence_vitale"]

    ecart = modele["exactitude"] - regle["exactitude"]
    verdict = _verdict_contre_la_regle(ecart, apparie)

    # Ce paragraphe de conclusion n'est écrit que si le modèle de base a été
    # évalué. L'évaluation accepte `--models sft dpo`, et le rapport décrirait
    # sinon le comportement d'un modèle qu'aucune ligne du tableau au-dessus ne
    # mesure.
    base = jeu["modeles"].get("base")
    if base:
        mot_de_la_fin = (
            "\n\nLe modèle de base, non spécialisé, ne rend une décision exploitable que dans "
            f"{_pourcentage(base['respect_format'])} des cas, pour "
            f"{_d(base['exactitude'], 2)} d'exactitude : c'est la mesure de ce qu'apporte la "
            "spécialisation."
        )
    else:
        mot_de_la_fin = ""

    return (
        f"La classe majoritaire atteint {_d(majoritaire['exactitude'], 2)} et la prudence maximale "
        f"{_d(prudence['exactitude'], 2)} — cette dernière ne sous-trie jamais, au prix de "
        f"{_pourcentage(prudence['surclassement'])} de surclassement, c'est-à-dire d'un service "
        f"saturé. La règle explicite atteint {_d(regle['exactitude'], 2)} avec "
        f"{_pourcentage(regle['sous_triage'])} de sous-triage.\n\n"
        f"Le modèle fine-tuné et aligné atteint {_d(modele['exactitude'], 2)} "
        f"[{_d(modele['exactitude_ic95'][0], 2)} – {_d(modele['exactitude_ic95'][1], 2)}] avec "
        f"{_pourcentage(modele['sous_triage'])} de sous-triage et "
        f"{_pourcentage(modele['respect_format'])} de réponses exploitables par le système "
        f"d'information. {verdict}\n\n"
        + _phrase_classifieur(jeu, modele, apparie_classique)
        + mot_de_la_fin
    )


def _phrase_classifieur(jeu: dict, modele: dict, apparie: dict | None) -> str:
    """Situe le modèle face à un classifieur classique entraîné sur les mêmes paires.

    Cette référence répond à une question que les trois autres laissent ouverte :
    ce que le fine-tuning apporte par-dessus un apprentissage ordinaire sur les
    mêmes données.
    """
    classique = jeu["references"].get("classifieur_classique")
    if not classique:
        return ""
    selection = classique.get("selection", {})
    phrase = (
        f"Le classifieur classique — configuration `{selection.get('configuration', 'n. d.')}`, "
        f"mêmes {_nombre(selection.get('exemples_d_entrainement', 0))} paires "
        f"d'entraînement — atteint {_d(classique['exactitude'], 2)} avec "
        f"{_pourcentage(classique['sous_triage'])} de sous-triage et "
        f"{_pourcentage(classique['surclassement'])} de surclassement, contre "
        f"{_pourcentage(modele['surclassement'])} pour le modèle."
    )
    if apparie:
        exactitude, securite = apparie["exactitude"], apparie["sous_triage"]
        phrase += (
            f" Sur les mêmes cas, le modèle en corrige {exactitude['modele_seul']} que le "
            f"classifieur manque et en manque {exactitude['reference_seule']} qu'il corrige "
            f"(McNemar exact, p = {_d(exactitude['p_mcnemar'], 3)})."
        )
        # Le sous-triage est la mesure que ce rapport désigne comme décisive : la
        # comparer à cette référence-là est le seul moyen de dire si le
        # fine-tuning apporte quelque chose sur la sécurité.
        if securite["sous_triages_modele"] == securite["sous_triages_reference"]:
            phrase += (
                f" Sur le sous-triage, les deux systèmes sont à égalité — "
                f"{securite['sous_triages_modele']} urgences manquées sur "
                f"{securite['cas_urgents']} de part et d'autre, "
                f"{securite['modele_seul']} cas rattrapés contre "
                f"{securite['reference_seule']} perdus (p = {_d(securite['p_mcnemar'], 3)})."
            )
        else:
            phrase += (
                f" Sur le sous-triage, le modèle en laisse passer "
                f"{securite['sous_triages_modele']} sur {securite['cas_urgents']} contre "
                f"{securite['sous_triages_reference']} pour le classifieur "
                f"(p = {_d(securite['p_mcnemar'], 3)})."
            )
    phrase += (
        " Ce classifieur ne produit que le niveau : la justification et la recommandation,"
        " deux des trois champs du contrat de sortie, restent hors de sa portée."
    )
    return phrase


def _commentaire_generalisation(evaluation: dict, final: str) -> str:
    interne = evaluation["jeu_interne"]["modeles"].get(final)
    clinique = evaluation["jeu_clinique"]["modeles"][final]
    if not interne:
        return (
            "Le jeu de test interne n'a pas été évalué lors de cette exécution : l'écart de "
            "généralisation n'est donc pas mesuré."
        )
    ecart = interne["exactitude"] - clinique["exactitude"]
    # Le signe compte autant que l'amplitude, d'où la première branche. Comparé
    # aux seuls seuils positifs, un écart fortement négatif tomberait dans la
    # dernière branche, et le rapport imprimerait « -12 points d'écart. L'écart
    # est faible », en contredisant le chiffre de la même phrase.
    if ecart < -0.05:
        lecture = (
            "Le modèle fait mieux sur les cas rédigés à la main que sur le jeu interne. L'écart "
            "ne va pas dans le sens attendu : il interroge la qualité des étiquettes de "
            "confiance moyenne, celles que la règle a attribuées aux cas extraits des corpus."
        )
    elif ecart > 0.2:
        lecture = (
            "L'écart est important : une part de la performance interne tient à la régularité "
            "des gabarits de génération, que les cas rédigés à la main ne présentent pas. C'est "
            "l'argument le plus fort en faveur d'un corpus d'entraînement issu du terrain."
        )
    elif ecart > 0.05:
        lecture = (
            "L'écart est modéré : le modèle transfère l'essentiel de ce qu'il a appris à des "
            "formulations qu'il n'a jamais vues, sans que la généralisation soit acquise."
        )
    else:
        lecture = (
            "L'écart est faible : ce que le modèle a appris ne tient pas à la forme des exemples "
            "d'entraînement."
        )
    # Les deux exactitudes portent leur intervalle : celui du jeu interne ne
    # figure dans aucun tableau du rapport, et publier l'un nu à côté de l'autre
    # ferait lire l'écart sur deux chiffres qui ne sont pas de même précision.
    return (
        f"Le modèle atteint {_d(interne['exactitude'], 2)}"
        f"{_entre_crochets(interne['exactitude_ic95'])} sur le jeu de test interne et "
        f"{_d(clinique['exactitude'], 2)}{_entre_crochets(clinique['exactitude_ic95'])} sur le "
        f"jeu clinique indépendant, soit {_d(ecart * 100, 0)} points d'écart. {lecture}"
    )


def _entre_crochets(intervalle) -> str:
    """Écrit un intervalle entre crochets, ou rien si l'effectif l'interdit."""
    if not intervalle:
        return ""
    basse, haute = intervalle
    return f" [{_d(basse, 2)} – {_d(haute, 2)}]"


def _nom_lisible(cle: str) -> str:
    """Intitulé d'une nature de cas, accentué comme le reste du rapport.

    Les clés du jeu d'évaluation servent d'identifiant : sans accent et soudées
    par des soulignés.
    """
    return {
        "constantes_discordantes": "constantes discordantes",
        "faux_alarmant": "faux alarmant",
        "faux_rassurant": "faux rassurant",
        "negation": "négation",
        "presentation_directe": "présentation directe",
        "consigne_detournee": "consigne détournée",
        "consigne_detournee_anglais": "consigne détournée en anglais",
        "saisie_minimale": "saisie minimale",
        "ponctuation_seule": "ponctuation seule",
        "copier_coller": "copier-coller de deux pages",
        "hors_domaine": "question hors domaine",
        "demande_prescription": "demande de prescription",
        "langue_tierce": "description en langue tierce",
        "donnees_identifiantes": "saisie contenant des données identifiantes",
    }.get(cle, cle.replace("_", " "))


def _commentaire_pieges(jeu: dict, final: str) -> str:
    par_piege = jeu["modeles"][final]["par_piege"]
    direct = par_piege.get("presentation_directe", {}).get("exactitude")
    atypiques = {
        nom: mesures for nom, mesures in par_piege.items() if nom != "presentation_directe"
    }
    if not atypiques or direct is None:
        return "Le détail par nature de cas n'est pas disponible pour cette exécution."
    # Exactitude **agrégée** sur les cas atypiques, pas moyenne des quatre
    # catégories : elles pèsent 11, 10, 4 et 3 cas, et une moyenne non pondérée
    # donnerait au groupe de trois le même poids qu'à celui de onze.
    cas_atypiques = sum(m["n"] for m in atypiques.values())
    justes = sum(round(m["exactitude"] * m["n"]) for m in atypiques.values())
    # L'estimateur est choisi par la porte d'entrée unique du projet, et non
    # recalculé ici : à vingt-huit cas, la règle publiée demande Clopper-Pearson,
    # et deux estimateurs différents sous la même annonce ne se relisent pas.
    agrege = intervalle_de_proportion(justes, cas_atypiques)
    pire = min(atypiques.items(), key=lambda item: item[1]["exactitude"])
    # La borne de la pire catégorie est celle que le fichier de résultats publie
    # déjà : la recalculer ici reviendrait à en publier deux pour la même proportion.
    borne_pire = pire[1].get("exactitude_ic95")
    # La borne des présentations directes vient elle aussi du fichier de
    # résultats, qui la publie sur les 32 cas de la catégorie.
    borne_directe = par_piege["presentation_directe"].get("exactitude_ic95")
    return (
        f"Sur les présentations directes, le modèle atteint {_d(direct, 2)}"
        f"{_entre_crochets(borne_directe)}. Sur les "
        f"{cas_atypiques} présentations atypiques prises ensemble, il retombe à "
        f"{_d(justes / cas_atypiques, 2)}{_entre_crochets(agrege)}. Sa pire catégorie "
        f"est « {_nom_lisible(pire[0])} », {_d(pire[1]['exactitude'], 2)}"
        f"{_entre_crochets(borne_pire)} sur {pire[1]['n']} cas — un effectif qui "
        "situe une faiblesse sans la quantifier. C'est le résultat attendu, et c'est celui qui "
        "compte : les présentations atypiques sont précisément celles où un tri automatique "
        "peut nuire."
    )


def _residus_pii(controle: dict[str, int] | str) -> str:
    """Ce que le contrôle indépendant relève encore dans le corpus livré.

    La carte du dataset écrit un décompte par motif quand il en reste un, et une
    phrase sinon — corpus propre, ou construit sans anonymisation. Les deux
    formes arrivent ici. Imprimer la structure telle quelle mettrait des
    accolades et des guillemets droits dans le PDF, là où le rapport attend une
    phrase.
    """
    if isinstance(controle, str):
        return controle
    releves = [(motif, nombre) for motif, nombre in controle.items() if nombre]
    if not releves:
        return "il ne relève aucun résidu"
    detail = ", ".join(
        f"{_nombre(nombre)} occurrence{'s' if nombre > 1 else ''} du motif « {motif} »"
        for motif, nombre in releves
    )
    return f"il relève {detail}"


def _taux_d_apprentissage(valeur: float) -> str:
    """Taux d'apprentissage en écriture décimale française, sans zéro inutile.

    Le formatage général de Python rend « 5e-06 » : une notation exponentielle
    dont le point décimal est anglais dès que la mantisse en porte un. La
    notation scientifique en indice supérieur n'est pas une issue non plus, la
    police du PDF n'ayant pas le signe moins exposant. Reste l'écriture
    décimale, avec la virgule du reste du document.
    """
    return f"{valeur:.10f}".rstrip("0").rstrip(".").replace(".", ",")


def _version_epinglee_du_moteur() -> str | None:
    """Lit dans la pile Docker la version de vLLM qui y est épinglée.

    Recopier ce numéro dans le rapport le laisserait dériver de la pile qu'il
    décrit. On le lit à la source, pour pouvoir dire si les mesures publiées
    viennent bien du moteur que la pile déploie.
    """
    composition = PATHS.root / "deploy" / "docker-compose.yml"
    if not composition.exists():
        return None
    trouve = re.search(
        r"vllm/vllm-openai:v([0-9][^@\s}]*)", composition.read_text(encoding="utf-8")
    )
    return trouve.group(1) if trouve else None


def _phrase_du_moteur_mesure(banc: dict) -> str:
    """Nomme le moteur sur lequel les latences ont été mesurées.

    La pile épingle son image par empreinte, mais cette image est surchargeable :
    le moteur V1 de vLLM exige l'adressage virtuel unifié de CUDA, que WSL 2
    n'expose pas, et un poste Windows mesure donc une version antérieure. Publier
    une latence sans dire de quel moteur elle vient la rendrait invérifiable.
    """
    mesuree = banc.get("version_moteur")
    if not mesuree:
        return ""
    epinglee = _version_epinglee_du_moteur()
    if epinglee is None or epinglee == mesuree:
        return f" Les mesures portent sur vLLM {mesuree}, la version que la pile déploie."
    return (
        f" Les mesures portent sur vLLM {mesuree}, et non sur la version {epinglee} épinglée dans "
        "la pile : celle-ci exige l'adressage virtuel unifié de CUDA, que le poste de mesure "
        "n'expose pas. Une mesure sur l'hôte de déploiement reste à faire."
    )


def _commentaire_latence(banc: dict | None) -> str:
    if not banc:
        return (
            "L'endpoint n'a pas été mesuré lors de cette exécution du rapport. La commande de "
            "mesure figure en annexe A."
        )
    mesures = banc["mesures"]
    seule = mesures.get("concurrence_1")
    charge = [m for nom, m in mesures.items() if nom != "concurrence_1"]
    if not seule:
        return "Mesures partielles."
    texte = (
        f"À une requête à la fois, la latence médiane est de {_d(seule['p50_ms'], 0)} ms et le "
        f"95ᵉ centile de {_d(seule['p95_ms'], 0)} ms. C'est l'ordre de grandeur d'une interaction "
        "à l'accueil : le temps de saisie du motif suivant."
    )
    # La passerelle publie sa propre durée d'inférence : la différence avec la
    # latence totale isole ce qu'elle ajoute, sans relancer de mesure.
    surcout = seule.get("surcout_passerelle_ms")
    if surcout is not None:
        texte += (
            f" Sur ce total, {_d(surcout, 0)} ms reviennent à la passerelle elle-même, et le "
            "reste à la génération."
        )
    if charge:
        plus_charge = max(charge, key=lambda m: m["debit_req_par_s"])
        # Les deux affirmations se vérifient avant d'être écrites : sans cela, un
        # endpoint saturé dont le 95ᵉ centile passerait de 900 ms à 6 000 ms
        # produirait exactement la même phrase rassurante.
        gain_de_debit = plus_charge["debit_req_par_s"] / max(seule["debit_req_par_s"], 1e-9)
        facteur_p95 = plus_charge["p95_ms"] / max(seule["p95_ms"], 1e-9)
        texte += (
            f" Sous charge, le débit passe à {_d(plus_charge['debit_req_par_s'], 1)} requêtes par "
            f"seconde, soit {_d(gain_de_debit, 1)} fois celui d'une requête à la fois, et le "
            f"95ᵉ centile à {_d(plus_charge['p95_ms'], 0)} ms, soit {_d(facteur_p95, 1)} fois "
            "celui mesuré sans concurrence."
        )
        if gain_de_debit > 1.2 and facteur_p95 <= 2:
            texte += (
                " Le regroupement des requêtes par vLLM absorbe donc la concurrence sans "
                "effondrer la latence perçue."
            )
        elif gain_de_debit <= 1.2:
            texte += (
                " Le débit ne progresse pas : à ce niveau de concurrence, le moteur ne tire "
                "plus parti du regroupement, et ajouter des postes d'accueil demanderait une "
                "seconde instance."
            )
        else:
            texte += (
                " La latence perçue se dégrade plus vite que le débit ne progresse : ce niveau "
                "de concurrence est au-delà de ce qu'une seule instance sert confortablement."
            )
    return texte + _phrase_du_moteur_mesure(banc)


def _analyse_erreurs(evaluation: dict, final: str) -> str:
    """Décrit les erreurs restantes, en distinguant celles qui sont dangereuses."""
    erreurs = evaluation.get("erreurs", {}).get(final, [])
    if not erreurs:
        return "Le modèle final ne commet aucune erreur sur le jeu clinique indépendant."
    from chsa_triage.config import TRIAGE

    sous_triages = [
        erreur
        for erreur in erreurs
        if erreur["predit"] is None
        or TRIAGE.severity.get(erreur["predit"], -1) < TRIAGE.severity[erreur["attendu"]]
    ]
    # `sous_triages` est un sous-ensemble de `erreurs` : les concaténer sans
    # filtre exposerait deux fois le même cas, sous un intitulé qui en promet
    # trois distincts, et ferait compter deux fautes là où il n'y en a qu'une,
    # sur le tableau même qui illustre la dangerosité résiduelle.
    identifiants_dangereux = {erreur["id"] for erreur in sous_triages}
    a_detailler = sous_triages + [e for e in erreurs if e["id"] not in identifiants_dangereux]

    detaillees = a_detailler[:3]
    # L'annonce s'accorde au tableau : écrite en dur, elle promettrait trois cas
    # là où une exécution plus propre n'en laisse qu'un ou deux.
    NOMBRES = {1: "Un cas en détail", 2: "Deux cas en détail", 3: "Trois cas en détail"}
    annonce = NOMBRES.get(len(detaillees), f"{len(detaillees)} cas en détail")
    lignes = [
        (
            f"Sur {evaluation['jeu_clinique']['n']} cas, {len(erreurs)} restent mal classés, dont "
            f"{len(sous_triages)} par sous-évaluation — les seules qui présentent un risque pour "
            f"le patient. {annonce}, sous-triages d'abord :"
        ),
        "",
        "| Cas | Attendu | Prédit | Nature | Description |",
        "|---|---|---|---|---|",
    ]
    for erreur in detaillees:
        description = erreur["description"][:150].replace("|", "/")
        lignes.append(
            f"| `{erreur['id']}` | {erreur['attendu'].replace('_', ' ').lower()} | "
            f"{(erreur['predit'] or 'hors format').replace('_', ' ').lower()} | "
            f"{erreur['piege'].replace('_', ' ') or 'directe'} | {description}… |"
        )
    return "\n".join(lignes)


def _conclusion(jeu: dict, final: str) -> str:
    modele = jeu["modeles"][final]
    regle = jeu["references"]["regle_explicite"]
    return (
        f"Sur un jeu d'évaluation indépendant de {jeu['n']} cas, il atteint "
        f"{_d(modele['exactitude'], 2)} d'exactitude contre {_d(regle['exactitude'], 2)} pour la règle "
        f"explicite qu'il doit remplacer, avec {_pourcentage(modele['sous_triage'])} de "
        f"sous-triage et {_pourcentage(modele['respect_format'])} de réponses exploitables par "
        "le système d'information."
    )


def _presentations_du_test() -> int:
    """Nombre de présentations distinctes du jeu de test interne.

    Le découpage train/validation/test tire des lignes, et une présentation du
    catalogue donne plusieurs lignes : le jeu de test reprend donc des cas déjà
    vus à l'entraînement, sous une autre formulation. Ce compte est ce qui permet
    au rapport de le dire avec un chiffre plutôt qu'en principe.
    """
    from chsa_triage.data.dataset_io import read_jsonl

    chemin = PATHS.data_processed / "sft_test.jsonl"
    if not chemin.exists():
        return 0
    return len(
        {
            ligne.get("presentation_id")
            for ligne in read_jsonl(chemin)
            if ligne.get("presentation_id")
        }
    )


def _niveau_par_classe(repartition: dict[str, int]) -> str:
    """Effectif par niveau, en fourchette si les trois ne sont pas égaux.

    Sur 5 000 exemples, qui ne se divisent pas par trois, les trois niveaux ne
    peuvent pas avoir le même effectif. Publier le seul maximum sous l'étiquette
    d'un corpus « exactement équilibré » donnerait un chiffre faux pour deux
    niveaux sur trois, que `metadata.json`, distribué avec le corpus, démentirait.
    """
    effectifs = sorted(repartition.values())
    if effectifs[0] == effectifs[-1]:
        return _nombre(effectifs[0])
    return f"{_nombre(effectifs[0])} à {_nombre(effectifs[-1])}"


def _materiel(environnement: dict) -> str:
    """Carte graphique et mémoire de l'exécution qui a produit les chiffres."""
    if not environnement:
        return "non renseigné"
    memoire = environnement.get("memoire_gpu_go")
    if not memoire:
        return environnement.get("gpu", "non renseigné")
    return f"{environnement['gpu']}, {_d(memoire, 0)} Go"


def _bibliotheques(environnement: dict) -> str:
    """Versions des bibliothèques d'entraînement, telles qu'elles ont été relevées.

    L'annexe est intitulée « version évaluée » : le nom du matériel seul n'y
    figerait aucune version, alors que l'exécution les relève et les écrit dans
    son résumé. PyTorch y vient en tête, le cahier des charges l'imposant
    explicitement.
    """
    versions = [
        f"{nom} {environnement[cle]}"
        for cle, nom in (("torch", "PyTorch"), ("transformers", "Transformers"), ("trl", "TRL"))
        if environnement.get(cle)
    ]
    return ", ".join(versions) or "non renseignées"


def _go_no_go(evaluation: dict, banc: dict | None, final: str) -> dict[str, str]:
    """Évalue les critères que le dépôt peut trancher seul ; les autres restent ouverts."""
    modele = evaluation["jeu_clinique"]["modeles"][final]
    latence_p95 = None
    if banc:
        concurrence = banc["mesures"].get("concurrence_4") or next(iter(banc["mesures"].values()))
        latence_p95 = concurrence["p95_ms"]

    # Les deux critères chiffrés sur une proportion se tranchent sur la borne de
    # l'intervalle, du côté défavorable, et non sur l'estimation ponctuelle. Le
    # jeu clinique compte 40 cas urgents : même zéro faute sur 40 laisse un
    # intervalle exact qui monte à 8,8 %, au-dessus du seuil de sous-triage ; et
    # 60 réponses toutes bien formées laissent une borne basse à 94 %, en dessous
    # du seuil de format. Écrire « atteint » sur le point central donnerait dans
    # les deux cas un feu vert que la mesure ne permet pas, à l'endroit du
    # rapport où un lecteur non statisticien le lira le plus vite.
    sous_triage_haut = modele["sous_triage_ic95"][1]
    if sous_triage_haut <= 0.05:
        verdict_sous_triage = f"atteint ({_pourcentage(modele['sous_triage'])})"
    else:
        verdict_sous_triage = (
            f"indéterminé sur ce jeu — {_pourcentage(modele['sous_triage'])} mesuré, "
            f"IC 95 % jusqu'à {_pourcentage(sous_triage_haut)} sur "
            f"{modele['sous_triage_detail']['cas_urgents']} cas urgents"
        )

    intervalle_format = intervalle_de_proportion(
        round(modele["respect_format"] * modele["n"]), modele["n"]
    )
    if intervalle_format is None:
        verdict_format = f"non tranché — {modele['n']} cas, effectif insuffisant pour un intervalle"
    elif intervalle_format[0] >= 0.99:
        verdict_format = f"atteint ({_pourcentage(modele['respect_format'])})"
    else:
        verdict_format = (
            f"indéterminé sur ce jeu — {_pourcentage(modele['respect_format'])} mesuré, "
            f"IC 95 % à partir de {_pourcentage(intervalle_format[0])} sur {modele['n']} cas"
        )

    return {
        "GONOGO_1": "à faire — validation clinique requise",
        "GONOGO_2": "à faire — annotation indépendante requise",
        "GONOGO_3": verdict_sous_triage,
        "GONOGO_4": verdict_format,
        "GONOGO_5": "non mesuré"
        if latence_p95 is None
        else ("atteint" if latence_p95 <= 2000 else "non atteint") + f" ({_d(latence_p95, 0)} ms)",
        "GONOGO_6": "atteint — volume persistant et masquage vérifiés par les tests",
        "GONOGO_7": "atteint en local — HTTPS à la charge de l'hébergeur",
        "GONOGO_8": "à faire — à éprouver sur l'environnement pilote",
        "GONOGO_9": "à faire — analyse d'impact à conduire avec le DPO",
        "GONOGO_10": "à faire — qualification à trancher avec le service juridique",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sans-figures", action="store_true", help="réutiliser les figures existantes"
    )
    args = parser.parse_args()

    metadonnees = _lire(PATHS.data_processed / "metadata.json")
    evaluation = _lire(PATHS.reports / "evaluation_results.json")
    if metadonnees is None or evaluation is None:
        raise SystemExit("Lancez d'abord scripts/01_build_dataset.py puis scripts/06_evaluate.py.")
    sft = _lire(PATHS.reports / "training" / "sft.json")
    dpo = _lire(PATHS.reports / "training" / "dpo.json")
    comparaison = _lire(PATHS.reports / "training" / "comparaison_hyperparametres.json")
    modules = _lire(PATHS.reports / "training" / "comparaison_modules.json")
    moteurs = _lire(PATHS.reports / "training" / "comparaison_moteurs.json")
    banc = _lire(PATHS.reports / "benchmark_endpoint.json")

    final = _dernier_modele(evaluation)
    # L'environnement est celui de l'exécution supervisée, qui a produit le
    # modèle livré ; à défaut, celui de l'alignement, joué sur la même machine.
    environnement = (sft or dpo or {}).get("environnement", {})
    statistiques = metadonnees["statistiques"]
    jeu_clinique = evaluation["jeu_clinique"]
    livraisons = evaluation.get("comparaison_livraisons") or None

    if not args.sans_figures:
        logger.info("Génération des figures.")
        figures.appliquer_style()
        dossier = PATHS.figures
        figures.composition_dataset(statistiques, dossier / "01_composition_dataset.png")
        if comparaison:
            figures.reglage_hyperparametres(comparaison, dossier / "02_reglage_hyperparametres.png")
        if sft:
            figures.apprentissage_sft(sft["historique"], dossier / "03_apprentissage_sft.png")
        if dpo:
            figures.alignement_dpo(
                dpo["historique"],
                dossier / "04_alignement_dpo.png",
                paires=dpo.get("metriques", {}).get("paires_validation", 0),
            )
        progression = _progression(jeu_clinique["modeles"])
        mesures = {
            NOMS_LISIBLES.get(nom, nom): valeurs
            for nom, valeurs in [*jeu_clinique["references"].items(), *progression.items()]
        }
        figures.comparaison_systemes(mesures, dossier / "05_comparaison_systemes.png")
        figures.matrices_confusion(
            {NOMS_LISIBLES.get(nom, nom): v["confusion"] for nom, v in progression.items()},
            dossier / "06_matrices_confusion.png",
        )
        figures.performance_par_piege(
            {NOMS_LISIBLES.get(nom, nom): v["par_piege"] for nom, v in progression.items()},
            dossier / "07_performance_par_piege.png",
        )
        if evaluation["jeu_interne"]["modeles"]:
            figures.generalisation(
                {
                    NOMS_LISIBLES.get(n, n): v
                    for n, v in _progression(evaluation["jeu_interne"]["modeles"]).items()
                },
                {NOMS_LISIBLES.get(n, n): v for n, v in progression.items()},
                dossier / "08_generalisation.png",
            )
        if banc:
            figures.latence_endpoint(banc["mesures"], dossier / "09_latence_endpoint.png")

    valeurs = {
        "NB_PRESENTATIONS": str(len(PRESENTATIONS)),
        "PRESENTATIONS_TEST": str(_presentations_du_test()),
        "SFT_TRAIN": _nombre(statistiques["sft_train"]),
        "SFT_VALIDATION": _nombre(statistiques["sft_validation"]),
        "SFT_TEST": _nombre(statistiques["sft_test"]),
        "DPO_TOTAL": _nombre(statistiques["dpo_total"]),
        "EVAL_TOTAL": str(statistiques["evaluation_clinique"]),
        # Écrite à la main, cette fraction dériverait du jeu livré, qui porte
        # près de la moitié de présentations atypiques et non un tiers.
        "PART_PIEGES": _pourcentage(
            sum(statistiques["evaluation_pieges"].values()) / statistiques["evaluation_clinique"],
            0,
        ),
        "NIVEAU_PAR_CLASSE": _niveau_par_classe(statistiques["repartition_niveaux"]),
        "LANGUE_FR": _nombre(statistiques["repartition_langues"].get("fr", 0)),
        "LANGUE_EN": _nombre(statistiques["repartition_langues"].get("en", 0)),
        "PART_CONSTANTES": _pourcentage(statistiques["part_avec_constantes"], 0),
        **_variables_du_dataset(metadonnees),
        "PII_RESIDUELLES": _residus_pii(metadonnees["rgpd"]["controle_independant"]),
        "FUITE": ", ".join(
            f"{cle} = {valeur}"
            for cle, valeur in metadonnees["rgpd"]["separation_entrainement_evaluation"].items()
        ),
        # Les réglages publiés sont ceux de l'exécution, pas ceux de `config.py`.
        # Le fine-tuning prend son rang et son taux d'apprentissage dans la
        # variante que le réglage a retenue, et accepte des surcharges en ligne
        # de commande : imprimer les constantes décrirait un modèle qui n'a pas
        # été entraîné.
        "LORA_R": str(_reglage(sft, "lora_r", TRAINING.lora_r)),
        "LORA_ALPHA": str(_reglage(sft, "lora_alpha", TRAINING.lora_alpha)),
        "LORA_DROPOUT": _d(_reglage(sft, "lora_dropout", TRAINING.lora_dropout), 2),
        "SFT_LR": _taux_d_apprentissage(_reglage(sft, "learning_rate", TRAINING.sft_lr)),
        "SFT_EPOCHS": str(_reglage(sft, "epochs", TRAINING.sft_epochs)),
        "SFT_LOT_EFFECTIF": str(
            _reglage(sft, "lot_effectif", TRAINING.sft_batch_size * TRAINING.sft_grad_accum)
        ),
        "MAX_LENGTH": str(_reglage(sft, "max_length", MODEL.max_seq_length)),
        "SEED": str(_reglage(sft, "graine", SEED)),
        "DPO_BETA": _d(_reglage(dpo, "beta", TRAINING.dpo_beta), 1),
        "DPO_RPO_ALPHA": _d(_reglage(dpo, "rpo_alpha", TRAINING.dpo_rpo_alpha), 1),
        "DPO_LR": _taux_d_apprentissage(_reglage(dpo, "learning_rate", TRAINING.dpo_lr)),
        "DPO_EPOCHS": str(_reglage(dpo, "epochs", TRAINING.dpo_epochs)),
        "DPO_LOT_EFFECTIF": str(
            _reglage(dpo, "lot_effectif", TRAINING.dpo_batch_size * TRAINING.dpo_grad_accum)
        ),
        "DPO_EQUILIBRE": _pourcentage(
            statistiques["dpo_equilibre_longueurs"]["part_chosen_plus_long"], 0
        ),
        "ARRETS_NETS_REGLAGE": _arrets_nets_du_reglage(comparaison),
        "TABLEAU_STRATEGIES_DPO": _tableau_strategies(statistiques),
        "TABLEAU_RESULTATS": _tableau_resultats(jeu_clinique),
        "COMMENTAIRE_RESULTATS": _commentaire_resultats(
            jeu_clinique,
            final,
            evaluation.get("comparaison_modele_regle"),
            evaluation.get("comparaison_modele_classifieur"),
        ),
        "COMMENTAIRE_GENERALISATION": _commentaire_generalisation(evaluation, final),
        "COMMENTAIRE_PIEGES": _commentaire_pieges(jeu_clinique, final),
        "TABLEAU_SECURITE": _tableau_securite(
            jeu_clinique["modeles"][final]["securite"], jeu_clinique["modeles"][final]["n"]
        ),
        "TABLEAU_PREFERENCES": _tableau_preferences(evaluation.get("preferences_externes", {})),
        "COMMENTAIRE_PREFERENCES": _commentaire_preferences(
            evaluation.get("preferences_externes", {}),
            evaluation.get("preferences_appariees"),
        ),
        "TABLEAU_ROBUSTESSE": _tableau_robustesse(evaluation.get("robustesse", {}).get(final)),
        "COMMENTAIRE_ROBUSTESSE": _commentaire_robustesse(
            evaluation.get("robustesse", {}).get(final)
        ),
        "ANALYSE_ERREURS": _analyse_erreurs(evaluation, final),
        "TABLEAU_LATENCE": _tableau_latence(banc),
        "COMMENTAIRE_LATENCE": _commentaire_latence(banc),
        "TABLEAU_MODULES": _tableau_modules(modules),
        "COMPARAISON_MOTEURS": _comparaison_moteurs(moteurs),
        **_jetons_compares(modules),
        "TABLEAU_LIVRAISONS": _tableau_livraisons(livraisons),
        "ACCORD_LIVRAISONS": (
            _pourcentage(livraisons["accord_des_predictions"])
            if livraisons and livraisons.get("accord_des_predictions") is not None
            else "non mesuré"
        ),
        "COMMENTAIRE_LIVRAISONS": _commentaire_livraisons(livraisons, jeu_clinique["n"]),
        "TABLEAU_SOURCES": _tableau_sources(),
        "CONCLUSION_CHIFFREE": _conclusion(jeu_clinique, final),
        "REVISION": revision_git(),
        "MODELE_LIVRE": PATHS.dpo_merged.name,
        "MATERIEL": _materiel(environnement),
        "BIBLIOTHEQUES": _bibliotheques(environnement),
        **_go_no_go(evaluation, banc, final),
    }
    if sft:
        metriques = sft["metriques"]
        valeurs |= {
            "PARAMS_ENTRAINABLES": _nombre(metriques["parametres_entrainables"]),
            "PARAMS_TOTAL": _nombre(metriques["parametres_total"]),
            "PART_ENTRAINABLE": f"{_d(metriques['part_entrainable_pct'], 2)} %",
            "SFT_TRAIN_LOSS": f"{_d(metriques['train_loss'], 4)}",
            "SFT_EVAL_LOSS": f"{_d(metriques['eval_loss'], 4)}"
            if metriques["eval_loss"]
            else "n/a",
            "SFT_DUREE": _duree(metriques["duree_s"]),
            "SFT_MEMOIRE": f"{_d(metriques['memoire_gpu_max_go'], 1)} Go",
        }
    if dpo:
        metriques = dpo["metriques"]
        valeurs |= {
            "DPO_PRECISION": _pourcentage(metriques["eval_reward_accuracy"], 0),
            "DPO_DUREE": _duree(metriques["duree_s"]),
            "DPO_MEMOIRE": f"{_d(metriques['memoire_gpu_max_go'], 1)} Go",
        }
    if comparaison:
        valeurs |= {
            "TABLEAU_HYPERPARAMETRES": _tableau_hyperparametres(comparaison),
            "VARIANTE_RETENUE": comparaison["retenue"],
            "REGLAGE_DUREE": _duree(sum(v["duree_s"] for v in comparaison["variantes"])),
            "REGLAGE_MEMOIRE": f"{_d(max(v['memoire_gpu_max_go'] for v in comparaison['variantes']), 1)} Go",
        }

    gabarit = (PATHS.reports / "rapport_technique.template.md").read_text(encoding="utf-8")
    marqueurs = {m.strip("{}") for m in re.findall(r"\{\{[A-Z0-9_]+\}\}", gabarit)}

    # Les trois blocs ci-dessus dépendent d'un résumé d'entraînement. S'il manque
    # — exécution partielle, fichier non produit — leurs marqueurs n'ont pas de
    # valeur, et le contrôle suivant les signalerait comme des erreurs de câblage
    # en conseillant de les ajouter à ce script : un mauvais diagnostic, et un
    # mauvais remède. On les renseigne en disant la mesure absente.
    manquants = sorted(marqueurs & set(DEPENDENT_DE_L_ENTRAINEMENT) - set(valeurs))
    if manquants:
        logger.warning(
            "Résumé d'entraînement absent : %s ne sont pas mesurés. "
            "Relancez le script d'entraînement concerné pour les publier.",
            ", ".join(manquants),
        )
        valeurs |= dict.fromkeys(manquants, "non mesuré")

    # Ce qu'il reste est bien une erreur de câblage : un marqueur du gabarit
    # qu'aucune fonction de ce script ne sait remplir. Le laisser passer
    # livrerait un PDF où il manque une phrase, sans que personne s'en aperçoive
    # avant la soutenance.
    non_cables = sorted(marqueurs - set(valeurs))
    if non_cables:
        raise SystemExit(
            "Marqueurs du gabarit sans valeur : "
            + ", ".join(non_cables)
            + ". Ajoutez-les à la table de substitution de ce script."
        )

    # L'inverse est bénin — une valeur calculée que le gabarit n'utilise plus —
    # mais c'est du calcul pour rien, et souvent le signe d'une coupe oubliée.
    inutilisees = sorted(set(valeurs) - marqueurs)
    if inutilisees:
        logger.warning(
            "Valeurs calculées que le gabarit n'utilise pas : %s", ", ".join(inutilisees)
        )

    markdown = gabarit
    for nom, valeur in valeurs.items():
        markdown = markdown.replace("{{" + nom + "}}", str(valeur))

    source = PATHS.reports / "rapport_technique.md"
    source.write_text(markdown, encoding="utf-8")
    logger.info("Markdown écrit → %s", source)

    pages = render(
        markdown=markdown,
        destination=PATHS.reports / "rapport_technique.pdf",
        titre="Agent IA de triage médical",
        sous_titres=[
            "Centre Hospitalier Saint-Aurélien — Proof of Concept",
            "Rapport technique et recommandations stratégiques",
            "Benoit Girard · IA Engineer · juin 2026",
        ],
        pied="CHSA — POC agent de triage médical — rapport technique",
        racine_images=PATHS.reports,
    )
    logger.info("PDF écrit → %s (%d pages)", PATHS.reports / "rapport_technique.pdf", pages)
    if pages > PAGES_MAXIMUM:
        raise SystemExit(
            f"Le rapport fait {pages} pages, la limite imposée est de {PAGES_MAXIMUM}."
        )
    print(f"Rapport technique : {pages} pages sur {PAGES_MAXIMUM} autorisées.")


if __name__ == "__main__":
    main()
