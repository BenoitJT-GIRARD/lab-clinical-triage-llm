"""Tests de la production des livrables documentaires.

Le rapport et la présentation sont des binaires : personne ne les relit ligne à
ligne. S'ils se cassent, il faut que ce soit ici et non la veille de la
soutenance. Ces tests vérifient que les trois chaînes — figures, PDF, diapositives —
produisent des fichiers valides à partir de données de la forme attendue.
"""

from __future__ import annotations

import zipfile

import matplotlib.pyplot as plt
import pytest

from chsa_triage.reporting import figures
from chsa_triage.reporting.markdown_pdf import _bloc_de_code, _element_de_liste, render
from chsa_triage.reporting.slides import Slide, construire

STATISTIQUES = {
    "repartition_niveaux": {
        "URGENCE_VITALE": 1666,
        "URGENCE_MODEREE": 1666,
        "CONSULTATION_DIFFEREE": 1666,
    },
    "repartition_langues": {"fr": 2499, "en": 2499},
    "repartition_confiance": {"haute": 4188, "moyenne": 810},
    "repartition_sources": {"vignette_clinique": 4188, "medmcqa": 643, "medquad": 167},
}

MESURES = {
    "Règle explicite": {
        "n": 60,
        "exactitude": 0.58,
        "exactitude_ic95": [0.45, 0.70],
        "sous_triage": 0.42,
        "sous_triage_detail": {"fautes": 17, "cas_urgents": 40},
        "respect_format": 1.0,
        "surclassement": 0.05,
    },
    "SFT + LoRA + DPO": {
        "n": 60,
        "exactitude": 0.80,
        "exactitude_ic95": [0.68, 0.88],
        "sous_triage": 0.15,
        "sous_triage_detail": {"fautes": 6, "cas_urgents": 40},
        "respect_format": 1.0,
        "surclassement": 0.12,
    },
}


@pytest.fixture(autouse=True)
def style():
    figures.appliquer_style()


def test_figure_composition_dataset(tmp_path):
    chemin = figures.composition_dataset(STATISTIQUES, tmp_path / "composition.png")
    assert chemin.exists()
    assert chemin.read_bytes().startswith(b"\x89PNG")


def test_figure_comparaison_systemes(tmp_path):
    chemin = figures.comparaison_systemes(MESURES, tmp_path / "comparaison.png")
    assert chemin.stat().st_size > 5000


def test_figure_matrices_confusion(tmp_path):
    confusion = {
        "lignes": ["URGENCE_VITALE", "URGENCE_MODEREE", "CONSULTATION_DIFFEREE"],
        "colonnes": [
            "URGENCE_VITALE",
            "URGENCE_MODEREE",
            "CONSULTATION_DIFFEREE",
            "HORS_FORMAT",
        ],
        "matrice": {
            "URGENCE_VITALE": {
                "URGENCE_VITALE": 15,
                "URGENCE_MODEREE": 3,
                "CONSULTATION_DIFFEREE": 1,
                "HORS_FORMAT": 1,
            },
            "URGENCE_MODEREE": {
                "URGENCE_VITALE": 2,
                "URGENCE_MODEREE": 16,
                "CONSULTATION_DIFFEREE": 2,
                "HORS_FORMAT": 0,
            },
            "CONSULTATION_DIFFEREE": {
                "URGENCE_VITALE": 1,
                "URGENCE_MODEREE": 2,
                "CONSULTATION_DIFFEREE": 17,
                "HORS_FORMAT": 0,
            },
        },
    }
    chemin = figures.matrices_confusion({"SFT + LoRA + DPO": confusion}, tmp_path / "confusion.png")
    assert chemin.exists()


def test_figures_de_courbes(tmp_path):
    historique_sft = [
        {"epoch": 0.5, "loss": 1.2},
        {"epoch": 1.0, "loss": 0.8, "eval_loss": 0.9},
        {"epoch": 2.0, "loss": 0.5, "eval_loss": 0.6},
    ]
    assert figures.apprentissage_sft(historique_sft, tmp_path / "sft.png").exists()

    historique_dpo = [
        {"epoch": 0.5, "loss": 0.6},
        {"epoch": 1.0, "loss": 0.3, "eval_rewards/accuracies": 0.92, "eval_rewards/margins": 2.1},
    ]
    assert figures.alignement_dpo(historique_dpo, tmp_path / "dpo.png").exists()


def test_figures_de_synthese(tmp_path):
    comparaison = {
        "variantes": [
            {
                "variante": "r16_lr2e-4",
                "exactitude_triage": 0.82,
                "eval_loss": 0.41,
                "parametres_entrainables": 17432576,
                "duree_s": 500.0,
                "memoire_gpu_max_go": 9.3,
            },
            {
                "variante": "r8_lr2e-4",
                "exactitude_triage": 0.78,
                "eval_loss": 0.45,
                "parametres_entrainables": 8716288,
                "duree_s": 480.0,
                "memoire_gpu_max_go": 8.9,
            },
        ]
    }
    assert figures.reglage_hyperparametres(comparaison, tmp_path / "reglage.png").exists()

    par_piege = {
        "SFT + LoRA + DPO": {
            "presentation_directe": {"exactitude": 0.88, "n": 32},
            "faux_rassurant": {"exactitude": 0.60, "n": 10},
        }
    }
    assert figures.performance_par_piege(par_piege, tmp_path / "pieges.png").exists()
    assert figures.generalisation(
        {"SFT + LoRA + DPO": {"exactitude": 0.95, "n": 500}},
        {"SFT + LoRA + DPO": {"exactitude": 0.80, "n": 60}},
        tmp_path / "gener.png",
    ).exists()

    banc = {
        "concurrence_1": {
            "p50_ms": 420.0,
            "p95_ms": 610.0,
            "debit_req_par_s": 2.3,
            "tokens_par_s": 180.0,
        },
        "concurrence_4": {
            "p50_ms": 700.0,
            "p95_ms": 980.0,
            "debit_req_par_s": 6.1,
            "tokens_par_s": 470.0,
        },
    }
    assert figures.latence_endpoint(banc, tmp_path / "latence.png").exists()


# --- Rapport PDF ---

MARKDOWN = """# Premier chapitre

Un paragraphe avec du **gras**, de l'*italique* et du `code`.

- première puce
- seconde puce

1. étape une
2. étape deux

| Colonne | Valeur |
|---|---|
| exactitude | 0,80 |
| sous-triage | 15 % |

> Une citation encadrée.

\\pagebreak

## Sous-chapitre

Un second paragraphe, après un saut de page.
"""


def test_le_rapport_se_rend_en_pdf(tmp_path):
    destination = tmp_path / "rapport.pdf"
    pages = render(
        markdown=MARKDOWN,
        destination=destination,
        titre="Titre du rapport",
        sous_titres=["Sous-titre", "Auteur"],
        pied="pied de page",
        racine_images=tmp_path,
    )
    assert destination.exists()
    assert destination.read_bytes().startswith(b"%PDF")
    # Page de titre, sommaire, puis le contenu. Le saut de page du gabarit est
    # conditionnel : sur un document aussi court, il reste de la place et il ne
    # se déclenche pas.
    assert pages == 2


def test_un_saut_de_page_conditionnel_n_ouvre_pas_de_page_blanche(tmp_path):
    """Trois pages de blanc sur vingt, c'est le prix d'un saut inconditionnel."""
    avec = render(
        markdown=MARKDOWN,
        destination=tmp_path / "avec.pdf",
        titre="T",
        sous_titres=["S"],
        pied="p",
        racine_images=tmp_path,
    )
    sans = render(
        markdown=MARKDOWN.replace("\\pagebreak\n", ""),
        destination=tmp_path / "sans.pdf",
        titre="T",
        sous_titres=["S"],
        pied="p",
        racine_images=tmp_path,
    )
    assert avec == sans


def test_un_saut_de_page_conditionnel_se_declenche_en_bas_de_page(tmp_path):
    """Un titre de partie suivi de deux lignes en bas de page n'a pas d'intérêt.

    Il existe une quantité de texte qui ne laisse plus assez de place au bas de
    la page : le saut doit alors s'exercer, et le document gagner une page par
    rapport au même texte sans saut. Le remplissage est cherché plutôt que fixé,
    la quantité exacte dépendant de l'interligne de la feuille de styles.
    """

    def pages_de(markdown: str, nom: str) -> int:
        return render(
            markdown=markdown,
            destination=tmp_path / nom,
            titre="T",
            sous_titres=["S"],
            pied="p",
            racine_images=tmp_path,
        )

    def le_saut_ajoute_une_page(paragraphes: int) -> bool:
        remplissage = "\n\n".join("Paragraphe de remplissage. " * 12 for _ in range(paragraphes))
        debut = f"# Premier chapitre\n\n{remplissage}\n\n"
        fin = "# Second chapitre\n\nUne ligne.\n"
        avec = pages_de(debut + "\\pagebreak\n\n" + fin, f"avec_{paragraphes}.pdf")
        sans = pages_de(debut + fin, f"sans_{paragraphes}.pdf")
        return avec == sans + 1

    assert any(le_saut_ajoute_une_page(paragraphes) for paragraphes in range(16, 34))


def test_le_rapport_insere_les_figures(tmp_path):
    figures.composition_dataset(STATISTIQUES, tmp_path / "figure.png")
    destination = tmp_path / "avec_figure.pdf"
    render(
        markdown="# Chapitre\n\n![Une légende](figure.png)\n",
        destination=destination,
        titre="Titre",
        sous_titres=[],
        pied="pied",
        racine_images=tmp_path,
    )
    # Une image insérée pèse nettement plus qu'un PDF de texte seul.
    assert destination.stat().st_size > 30_000


def test_les_caracteres_speciaux_ne_cassent_pas_le_rendu(tmp_path):
    destination = tmp_path / "special.pdf"
    render(
        markdown="# Chapitre\n\nUn texte avec & des < chevrons > et des « guillemets ».\n",
        destination=destination,
        titre="Titre",
        sous_titres=[],
        pied="pied",
        racine_images=tmp_path,
    )
    assert destination.exists()


def test_une_puce_sur_plusieurs_lignes_reste_une_seule_puce():
    """Les lignes de continuation appartiennent à la puce, pas au texte courant.

    Lues une par une, elles devenaient des paragraphes autonomes : la puce
    perdait son retrait, et un gras ouvert sur la première ligne se refermait
    dans le vide, laissant ses astérisques visibles dans le PDF.
    """
    lignes = [
        "- **Un point important** qui commence ici",
        "  et se termine sur la ligne suivante.",
        "- Le point suivant.",
    ]

    texte, derniere = _element_de_liste(lignes, 0, lignes[0][2:])

    assert texte == "**Un point important** qui commence ici et se termine sur la ligne suivante."
    assert derniere == 1


def test_une_puce_s_arrete_a_ce_qui_n_est_pas_sa_suite():
    """Un paragraphe, un tableau ou une autre puce ferment l'élément en cours."""
    for suite in ("", "Un paragraphe qui suit la liste.", "| a | b |", "## Titre"):
        lignes = ["- Un point.", suite]

        texte, derniere = _element_de_liste(lignes, 0, lignes[0][2:])

        assert texte == "Un point.", suite
        assert derniere == 0, suite


def test_un_bloc_de_code_garde_ses_lignes():
    """Le contenu conserve ses retours à la ligne et perd ses délimiteurs."""
    lignes = [
        "```bash",
        "uv sync",
        "uv run pytest",
        "```",
        "Texte après le bloc.",
    ]

    corps, fermeture = _bloc_de_code(lignes, 0)

    assert corps == "uv sync\nuv run pytest"
    assert lignes[fermeture] == "```"


def test_un_bloc_de_code_se_rend_sans_interrompre_le_document(tmp_path):
    destination = tmp_path / "code.pdf"

    pages = render(
        markdown="# Chapitre\n\n```bash\nuv sync\nuv run pytest\n```\n\nSuite du texte.\n",
        destination=destination,
        titre="Titre",
        sous_titres=[],
        pied="pied",
        racine_images=tmp_path,
    )

    assert destination.exists()
    assert pages >= 1


# --- Présentation ---


def test_la_presentation_se_construit(tmp_path):
    figures.composition_dataset(STATISTIQUES, tmp_path / "figure.png")
    destination = construire(
        chemin=tmp_path / "soutenance.pptx",
        titre="Titre",
        sous_titre="Sous-titre",
        auteur="Auteur",
        diapositives=[
            Slide(titre="Sans figure", puces=["une puce", "une autre"]),
            Slide(titre="Avec figure", puces=["une puce"], figure=tmp_path / "figure.png"),
            Slide(titre="Figure seule", figure=tmp_path / "figure.png", note="une note"),
        ],
    )
    assert destination.exists()
    # Un .pptx est une archive : elle doit s'ouvrir et contenir les diapositives.
    with zipfile.ZipFile(destination) as archive:
        diapositives = [n for n in archive.namelist() if n.startswith("ppt/slides/slide")]
    assert len(diapositives) == 4  # page de titre + trois diapositives


def test_une_figure_absente_n_interrompt_pas_la_construction(tmp_path):
    destination = construire(
        chemin=tmp_path / "sans_figure.pptx",
        titre="Titre",
        sous_titre="Sous-titre",
        auteur="Auteur",
        diapositives=[Slide(titre="Figure manquante", figure=tmp_path / "inexistante.png")],
    )
    assert destination.exists()


# --- Ce que les figures ont le droit d'affirmer ---


def test_un_sous_groupe_trop_petit_ne_porte_pas_de_moustache(tmp_path):
    """Trois cas ne permettent pas d'estimer un taux.

    L'intervalle exact d'une réussite parfaite sur trois cas est [0,29 ; 1,00] :
    il couvre 71 % de l'échelle. Une barre surmontée d'une moustache pareille se
    lit « mesuré avec incertitude » quand la lecture juste est « non mesurable ».
    La figure trace alors une barre hachurée et la fraction brute.
    """
    from matplotlib.container import ErrorbarContainer

    par_piege = {
        "modèle": {
            "constantes_discordantes": {"exactitude": 1.0, "n": 3},
            "presentation_directe": {"exactitude": 0.94, "n": 32},
        }
    }
    figures.appliquer_style()
    figure, axe = plt.subplots()
    figures._barre_de_proportion(
        axe,
        [0, 1],
        [
            par_piege["modèle"]["constantes_discordantes"],
            par_piege["modèle"]["presentation_directe"],
        ],
        figures.BLEU,
        0.5,
    )
    moustaches = [c for c in axe.containers if isinstance(c, ErrorbarContainer)]
    assert len(moustaches) == 1, "une seule des deux barres est mesurable"

    hachurees = [barre for barre in axe.patches if barre.get_hatch()]
    assert len(hachurees) == 1
    textes = [t.get_text() for t in axe.texts]
    assert "3/3" in textes, "la fraction brute remplace le taux"
    plt.close(figure)


def test_une_mesure_absente_n_est_pas_dessinee_comme_un_zero(tmp_path):
    """Une catégorie non mesurée était tracée comme une barre à zéro étiquetée « 0.00 ».

    Indiscernable d'un système qui se serait trompé sur tous les cas du
    sous-groupe : une absence présentée comme un échec mesuré.
    """
    figures.appliquer_style()
    figure, axe = plt.subplots()
    figures._barre_de_proportion(
        axe, [0, 1], [None, {"exactitude": 0.9, "n": 20}], figures.BLEU, 0.5
    )
    assert len(axe.patches) == 1, "rien n'est tracé pour la mesure absente"
    assert "n.d." in [t.get_text() for t in axe.texts]
    plt.close(figure)


def test_les_figures_portent_leur_effectif_et_leur_estimateur(tmp_path):
    """Une figure quitte le rapport : elle doit se lire seule."""
    chemin = figures.comparaison_systemes(MESURES, tmp_path / "comparaison.png")
    assert chemin.exists()

    figure, _ = plt.subplots()
    figures._pied_de_figure(figure, ["n = 60 cas", "Wilson"])
    assert any("n = 60" in t.get_text() for t in figure.texts)
    plt.close(figure)


def test_les_nombres_sont_ecrits_a_la_francaise():
    assert figures._fr(0.917, 2) == "0,92"
    assert figures._fr(5000, 0) == "5 000"
    assert figures._fr(1.5, 1) == "1,5"
