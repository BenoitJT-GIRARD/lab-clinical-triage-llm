"""Assemblage de l'archive de livrables à déposer sur la plateforme.

La convention de nommage est celle de l'énoncé : un dossier
`Titre_du_projet_nom_prenom`, et à l'intérieur des fichiers
`Nom_Prenom_<numéro du livrable>_<nom du livrable>_<mmaaaa>`, où la date est
celle du démarrage du projet.

Les livrables 2, 3 et 4 — le modèle, l'endpoint et le pipeline — ne sont pas des
fichiers : ce sont des artefacts en ligne. Chacun reçoit donc une fiche générée
qui donne son adresse, sa version, la façon de l'utiliser et la façon de le
reproduire. Les poids eux-mêmes sont publiés sur le Hugging Face Hub : plusieurs
gigaoctets n'ont pas leur place dans une archive déposée sur une plateforme.

Usage :
    uv run python scripts/11_package_deliverable.py
    uv run python scripts/11_package_deliverable.py --endpoint https://mon-endpoint
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil

# Une seule liste d'arguments constante, sans shell ni entree exterieure :
# la lecture de l'etiquette publiee, plus bas.
import subprocess  # nosec B404
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from chsa_triage.config import MODEL, PATHS
from chsa_triage.reporting.formats import nombre_fr
from chsa_triage.utils import get_logger, revision_git

logger = get_logger("livrables")

NOM = "Girard"
PRENOM = "Benoit"
DATE_DEMARRAGE = "062026"
TITRE_PROJET = "POC_agent_triage_medical"


def _prefixe(numero: int | None, nom: str) -> str:
    """Construit le nom d'un livrable selon la convention de l'énoncé."""
    if numero is None:
        return f"{NOM}_{PRENOM}_{nom}_{DATE_DEMARRAGE}"
    return f"{NOM}_{PRENOM}_{numero}_{nom}_{DATE_DEMARRAGE}"


def _note_hors_rapport(figures: list[str]) -> str:
    """Annonce les figures qui n'existent que dans cette annexe.

    Le rapport est borné à vingt pages : quelques figures en sortent. Leurs
    nombres, eux, restent dans le texte et les tableaux du rapport, où ils
    portent en plus les intervalles de confiance qu'un graphique ne montre pas.
    """
    if not figures:
        return ""
    liste = ", ".join(f"`{nom}`" for nom in figures)
    accord = (
        "Une figure ne paraît" if len(figures) == 1 else f"{len(figures)} figures ne paraissent"
    )
    return (
        f"{accord} pas dans le rapport, borné à vingt pages, et n'existe"
        f"{'' if len(figures) == 1 else 'nt'} qu'ici : {liste}. Les nombres "
        "correspondants restent dans le texte et les tableaux du rapport.\n"
    )


def _copier(source: Path, destination: Path) -> bool:
    if not source.exists():
        logger.warning("Absent, ignoré : %s", source)
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    # Le chemin est journalisé relativement au dossier des rapports quand il s'y
    # trouve — c'est le cas de toute l'archive — et en entier sinon. Sans ce
    # repli, une destination située hors de cette arborescence ferait échouer la
    # copie elle-même au moment de la journaliser.
    try:
        lisible = destination.relative_to(PATHS.reports)
    except ValueError:
        lisible = destination
    logger.info("+ %s", lisible)
    return True


def _carnets_sans_sortie() -> list[str]:
    """Carnets dont aucune cellule de code ne porte de sortie exécutée.

    Les carnets sont versionnés sans sortie — c'est ce qui rend leurs diffs
    lisibles — mais ils partent en annexe de l'archive, où un carnet vide ne
    montre rien de la démarche qu'il documente.
    """
    muets = []
    for carnet in sorted((PATHS.root / "notebooks").glob("*.ipynb")):
        contenu = json.loads(carnet.read_text(encoding="utf-8"))
        cellules_de_code = [c for c in contenu["cells"] if c["cell_type"] == "code"]
        if cellules_de_code and not any(c.get("outputs") for c in cellules_de_code):
            muets.append(carnet.name)
    return muets


def _empreinte(chemin: Path) -> str:
    """Empreinte SHA-256 d'un fichier, pour vérifier un téléchargement."""
    if not chemin.exists():
        return "fichier absent localement"
    condense = hashlib.sha256()
    with chemin.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(1 << 20), b""):
            condense.update(bloc)
    return condense.hexdigest()


def _etiquette_publiee() -> str:
    """Dernière étiquette `modele-v*` du dépôt, ou la version du projet à défaut.

    L'étiquette est lue dans le dépôt plutôt qu'écrite en dur, pour que la
    commande de service donnée par la fiche et l'empreinte SHA-256 calculée sur
    les poids présents sur le disque désignent la même publication. Écrite en
    dur, elle citerait les anciens poids dès la publication suivante.
    """
    try:
        sortie = subprocess.run(  # nosec B603 B607
            ["git", "describe", "--tags", "--abbrev=0", "--match", "modele-v*"],
            cwd=PATHS.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return sortie.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"modele-v{version('chsa-triage')}"


def _fiche_modele(depot_git: str, revision: str) -> str:
    """Fiche du livrable 2 : où sont les poids et comment s'en servir."""
    resume = PATHS.reports / "training" / "sft.json"
    metriques = (
        json.loads(resume.read_text(encoding="utf-8"))["metriques"] if resume.exists() else {}
    )
    adaptateur = PATHS.dpo_adapter / "adapter_model.safetensors"
    return f"""# Livrable 2 — Modèle d'IA spécialisé

Modèle de langage `Qwen/Qwen3-1.7B-Base`, spécialisé au triage des urgences par
fine-tuning supervisé avec LoRA, puis aligné par préférences (DPO).

## Où télécharger les poids

| Artefact | Dépôt Hugging Face |
|---|---|
| Adaptateur LoRA issu du fine-tuning supervisé | `{MODEL.hub_sft_model_id}` |
| Modèle supervisé fusionné, base de l'adaptateur DPO | `{MODEL.hub_sft_merged_model_id}` |
| Adaptateur LoRA issu de l'alignement DPO | `{MODEL.hub_dpo_model_id}` |
| **Modèle final fusionné, prêt pour vLLM** | `{MODEL.hub_merged_model_id}` |

Empreinte SHA-256 de l'adaptateur DPO : `{_empreinte(adaptateur)}`

L'adaptateur supervisé s'applique sur `Qwen/Qwen3-1.7B-Base`. L'adaptateur DPO,
lui, s'applique sur le **modèle supervisé fusionné** : c'est par-dessus ces
poids-là que l'alignement a été entraîné. Ce modèle intermédiaire est donc
publié lui aussi, bien que personne ne le serve — sans lui, l'adaptateur DPO
serait un artefact que rien ne peut ouvrir. **Pour servir le modèle, prenez le
modèle final fusionné** : c'est le seul qui se charge seul. L'adaptateur DPO se
sert aussi à chaud, mais par-dessus le modèle supervisé fusionné — deux
artefacts à apparier ; l'adaptateur supervisé, lui, entraîne une matrice entière
que le serveur d'inférence refuse.

## Comment l'utiliser

Le modèle final est fusionné : il se charge comme n'importe quel modèle, sans
bibliothèque d'adaptation. Le gabarit de dialogue et le jeton de fin de séquence
sont inscrits dans le tokenizer exporté, donc aucun réglage n'est nécessaire côté
appelant.

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained("{MODEL.hub_merged_model_id}")
modele = AutoModelForCausalLM.from_pretrained("{MODEL.hub_merged_model_id}")
```

Servi par vLLM :

```bash
vllm serve {MODEL.hub_merged_model_id} --revision {revision} --served-model-name qwen3-1.7b-chsa-triage --max-model-len 1024
```

## Caractéristiques d'entraînement

| Élément | Valeur |
|---|---|
| Paramètres entraînés | {metriques.get("parametres_entrainables", "n/a")} sur {metriques.get("parametres_total", "n/a")} ({metriques.get("part_entrainable_pct", "n/a")} %) |
| Durée du fine-tuning supervisé | {metriques.get("duree_s", "n/a")} s |
| Mémoire GPU maximale | {metriques.get("memoire_gpu_max_go", "n/a")} Go |

Hyperparamètres, courbes et environnement : `reports/training/` dans le dépôt.

## Reproduire

```bash
uv run python scripts/03_train_sft.py
uv run python scripts/04_merge_and_export.py --adapter sft
uv run python scripts/05_train_dpo.py
uv run python scripts/04_merge_and_export.py --adapter dpo
```

Code, hyperparamètres et graine : {depot_git}

## Limites d'usage

Aide à la décision destinée au personnel soignant, sous supervision humaine
obligatoire. Ne remplace pas une évaluation clinique. Le catalogue de
présentations qui a servi à construire les données n'a pas été validé par un
médecin urgentiste : ce modèle ne doit pas être utilisé en situation réelle.
"""


def _fiche_endpoint(endpoint: str, depot_git: str) -> str:
    """Fiche du livrable 3 : adresse, authentification, exemples d'appel."""
    banc = PATHS.reports / "benchmark_endpoint.json"
    mesures = json.loads(banc.read_text(encoding="utf-8"))["mesures"] if banc.exists() else {}
    tableau = (
        "\n".join(
            f"| {nom.replace('concurrence_', '')} | {m['p50_ms']:.0f} ms | "
            f"{m['p95_ms']:.0f} ms | {nombre_fr(m['debit_req_par_s'], 2)} req/s |"
            for nom, m in mesures.items()
        )
        or "| — | mesure non disponible | — | — |"
    )
    return f"""# Livrable 3 — Endpoint de démonstration

API de triage servie par **vLLM** derrière une passerelle **FastAPI**
conteneurisée.

## Adresse

| Élément | Valeur |
|---|---|
| Endpoint | {endpoint} |
| Documentation interactive | {endpoint}/docs |
| Authentification | en-tête `X-API-Key` |

## Architecture

```
poste d'accueil / SIH  ──HTTPS──▶  passerelle FastAPI  ──HTTP──▶  serveur vLLM (GPU)
                                   authentification              modèle fusionné
                                   questionnaire adaptatif       traitement par lots
                                   règle de contrôle             cache d'attention
                                   anonymisation et audit
```

## Points d'entrée

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/health` | état du service et du moteur d'inférence |
| `POST` | `/questionnaire/next` | question suivante du questionnaire adaptatif |
| `POST` | `/triage` | niveau de priorité, justification, conduite à tenir |

## Exemple d'appel

```bash
curl -X POST {endpoint}/triage \\
  -H 'Content-Type: application/json' \\
  -H "X-API-Key: $TRIAGE_API_KEY" \\
  -d '{{"symptoms": "Homme de 62 ans, douleur thoracique et sueurs depuis 20 minutes. TA 148/92, FC 102."}}'
```

La réponse contient le niveau proposé, sa justification, la conduite à tenir,
**le niveau qu'aurait retenu la règle explicite**, l'indicateur d'accord entre
les deux, la latence et l'identifiant de traçabilité.

## Performance mesurée de bout en bout

Mesures prises sur `POST /triage`, réponse complète : génération, règle
explicite, anonymisation et écriture au journal d'audit comprises.

| Requêtes simultanées | Latence médiane | 95ᵉ centile | Débit |
|---|---|---|---|
{tableau}

## Lancer la pile localement

```bash
export TRIAGE_API_KEY="une-cle-choisie"
docker compose -f deploy/docker-compose.yml up --build
```

Procédure de déploiement complète : `deploy/README.md` dans {depot_git}
"""


def _fiche_cicd(depot_git: str) -> str:
    """Fiche du livrable 4 : ce que le pipeline automatise, et comment le vérifier."""
    return f"""# Livrable 4 — Pipeline d'intégration et de déploiement continus

Deux workflows GitHub Actions, dans `.github/workflows/` du dépôt {depot_git}.

## Intégration continue — `ci.yml`

Déclenchée à chaque poussée et à chaque demande de fusion sur `main` et `develop`.

| Étape | Vérification |
|---|---|
| Style | `ruff check` et `ruff format --check` |
| Sécurité du code | `bandit` sur le paquet |
| Sécurité des dépendances | `pip-audit` sur l'arbre complet |
| Tests | `pytest` avec seuil de couverture |
| Image de service | construction, démarrage du conteneur, appel de `/health`, vérification qu'un appel sans clé est refusé |

La dernière étape mérite d'être soulignée : une image qui ne démarre pas n'est
pas une image, et une authentification non testée n'en est pas une.

## Déploiement continu — `cd.yml`

Le pipeline distingue deux objets qui évoluent à des rythmes différents.

| Déclencheur | Action |
|---|---|
| Poussée sur `main` | construction et publication de l'image de la passerelle sur GHCR |
| Étiquette `modele-v*` | publication du dataset et des cartes de modèle sur le Hugging Face Hub, et pose de l'étiquette de version sur les dépôts du Hub |
| Après publication | redéploiement sur Modal à la révision étiquetée, puis **vérification de la santé de l'endpoint** |

C'est ainsi que le déploiement « intègre les évolutions du modèle ». L'étiquette
`modele-v*` fige une révision citable sur le Hub ; le job de déploiement relance
l'hébergeur sur cette révision, et le serveur d'inférence y recharge les poids. Aucune image n'a à embarquer trois gigaoctets : une nouvelle version du
modèle se livre sans reconstruire la passerelle, et inversement. Hors étiquette
de modèle, la révision transmise vaut `main` et le modèle en place reste servi.

Un déploiement qui ne vérifie pas son résultat n'est pas un déploiement : la
dernière étape interroge `/health` et fait échouer le workflow si l'endpoint ne
répond pas correctement.

## Vérifier

Onglet **Actions** du dépôt : chaque exécution est consultable, avec ses journaux.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--depot", default="https://github.com/BenoitJT-GIRARD/chsa-triage", help="URL du dépôt git"
    )
    parser.add_argument(
        "--endpoint", default="(déploiement en cours)", help="URL publique de l'endpoint"
    )
    parser.add_argument(
        "--revision",
        default=None,
        help="étiquette de la version publiée sur le Hub (défaut : la dernière modele-v*)",
    )
    args = parser.parse_args()

    base = PATHS.reports / f"{TITRE_PROJET}_{NOM}_{PRENOM}"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)

    # Livrable 1 — dataset bilingue et sa carte.
    dossier_dataset = base / _prefixe(1, "dataset")
    for fichier in sorted(PATHS.data_processed.glob("*.jsonl")):
        _copier(fichier, dossier_dataset / fichier.name)
    _copier(PATHS.data_processed / "metadata.json", dossier_dataset / "metadata.json")
    _copier(PATHS.data / "README.md", dossier_dataset / "carte_donnees.md")

    # Livrables 2, 3 et 4 — artefacts en ligne, décrits par une fiche.
    revision = args.revision or _etiquette_publiee()
    (base / f"{_prefixe(2, 'modele')}.md").write_text(
        _fiche_modele(args.depot, revision), encoding="utf-8"
    )
    (base / f"{_prefixe(3, 'endpoint')}.md").write_text(
        _fiche_endpoint(args.endpoint, args.depot), encoding="utf-8"
    )
    (base / f"{_prefixe(4, 'cicd')}.md").write_text(_fiche_cicd(args.depot), encoding="utf-8")

    # Livrables 5 et 6, et les deux pièces que le sommaire annonce. Leur copie
    # est vérifiée : `_copier` se contente de journaliser une absence, si bien
    # que sans ce relevé l'archive partirait sans le rapport ni la soutenance,
    # avec un sommaire qui les annonce quand même et un correcteur qui cherche.
    obligatoires = {
        "rapport technique": _copier(
            PATHS.reports / "rapport_technique.pdf", base / f"{_prefixe(5, 'rapport')}.pdf"
        ),
        "support de soutenance": _copier(
            PATHS.reports / "soutenance_chsa.pptx", base / f"{_prefixe(6, 'soutenance')}.pptx"
        ),
        "fiche d'auto-évaluation": _copier(
            PATHS.root / "docs" / "auto_evaluation.md",
            base / f"{_prefixe(None, 'auto_evaluation')}.md",
        ),
        "résultats d'évaluation": _copier(
            PATHS.reports / "evaluation_results.json",
            base / "annexes" / "resultats_evaluation.json",
        ),
    }
    for resume in sorted((PATHS.reports / "training").glob("*.json")):
        _copier(resume, base / "annexes" / "entrainement" / resume.name)
    figures_copiees = [
        figure.name
        for figure in sorted(PATHS.figures.glob("*.png"))
        if _copier(figure, base / "annexes" / "figures" / figure.name)
    ]
    # Quelles figures n'entrent pas dans le rapport se lit sur le rapport
    # lui-même : un compte tenu à part dériverait dès l'ajout d'une figure.
    rapport = PATHS.reports / "rapport_technique.md"
    texte_du_rapport = rapport.read_text(encoding="utf-8") if rapport.exists() else ""
    hors_rapport = [nom for nom in figures_copiees if nom not in texte_du_rapport]
    if figures_copiees:
        # Les figures quittent le rapport pour vivre seules dans cette annexe.
        # Chacune porte déjà son effectif et son estimateur ; ce qui leur manque
        # alors, c'est de quoi retrouver l'exécution qui les a produites.
        (base / "annexes" / "figures" / "LISEZ_MOI.md").write_text(
            f"""# Figures du rapport technique

Produites par `scripts/09_build_report.py` à partir des fichiers de résultats du
dépôt — aucune valeur n'y est recopiée à la main.

| Élément | Valeur |
|---|---|
| Révision du dépôt | `{revision_git()}` |
| Version du modèle | `{revision}` |
| Date de production | {datetime.now(UTC).strftime("%Y-%m-%d")} |

{_note_hors_rapport(hors_rapport)}
Chaque figure porte en pied son effectif, l'estimateur d'incertitude employé et
les réserves de lecture que le protocole impose.
""",
            encoding="utf-8",
        )
    for carnet in sorted((PATHS.root / "notebooks").glob("*.ipynb")):
        _copier(carnet, base / "annexes" / "notebooks" / carnet.name)

    absents = [libelle for libelle, copie in obligatoires.items() if not copie]
    muets = _carnets_sans_sortie()
    if absents or muets:
        raise SystemExit(
            "Archive non produite. "
            + ("Pièces manquantes : " + ", ".join(absents) + ". " if absents else "")
            + (
                "Carnets sans sortie exécutée : " + ", ".join(muets) + ". "
                "Exécutez-les avant de packager."
                if muets
                else ""
            )
        )

    (base / "LISEZ_MOI.md").write_text(
        f"""# Livrables — POC agent IA de triage médical (CHSA)

Benoit Girard · IA Engineer · projet démarré en juin 2026.

| Livrable | Fichier ou adresse |
|---|---|
| 1. Dataset médical bilingue | `{_prefixe(1, "dataset")}/` — également publié sur `{MODEL.hub_dataset_id}` |
| 2. Modèle spécialisé | `{_prefixe(2, "modele")}.md` |
| 3. Endpoint de démonstration | `{_prefixe(3, "endpoint")}.md` |
| 4. Pipeline CI/CD | `{_prefixe(4, "cicd")}.md` |
| 5. Rapport technique | `{_prefixe(5, "rapport")}.pdf` |
| 6. Support de soutenance | `{_prefixe(6, "soutenance")}.pptx` |
| Fiche d'auto-évaluation | `{_prefixe(None, "auto_evaluation")}.md` |

Code source, historique et pipeline : {args.depot}

Le dossier `annexes/` contient les résultats bruts d'évaluation, les résumés
d'entraînement, les figures et les notebooks de démarche.
""",
        encoding="utf-8",
    )

    archive = shutil.make_archive(str(base), "zip", root_dir=base.parent, base_dir=base.name)
    taille = Path(archive).stat().st_size / 1024**2
    logger.info("Archive créée : %s (%.1f Mo)", Path(archive).name, taille)
    print(f"Archive : {Path(archive).relative_to(PATHS.root)} ({taille:.1f} Mo)")


if __name__ == "__main__":
    main()
