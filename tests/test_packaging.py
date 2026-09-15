"""Tests du script d'archivage des livrables.

L'archive est ce que reçoit le jury. Deux propriétés comptent autant que son
contenu : elle ne part pas sans une pièce que son sommaire annonce, et elle ne
part pas avec des carnets vides de toute sortie.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def emballage():
    """Charge le script d'archivage, dont le nom commence par un chiffre."""
    chemin = RACINE / "scripts" / "11_package_deliverable.py"
    specification = importlib.util.spec_from_file_location("emballage", chemin)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_un_carnet_execute_n_est_pas_signale(emballage, tmp_path, monkeypatch):
    carnet = {
        "cells": [
            {"cell_type": "markdown", "source": ["texte"]},
            {"cell_type": "code", "source": ["1 + 1"], "outputs": [{"output_type": "stream"}]},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    dossier = tmp_path / "notebooks"
    dossier.mkdir()
    (dossier / "01_demarche.ipynb").write_text(json.dumps(carnet), encoding="utf-8")
    monkeypatch.setattr(emballage, "PATHS", replace(emballage.PATHS, root=tmp_path))
    assert emballage._carnets_sans_sortie() == []


def test_un_carnet_sans_sortie_est_signale(emballage, tmp_path, monkeypatch):
    """Un carnet vide en annexe ne montre rien de la démarche qu'il documente."""
    carnet = {
        "cells": [{"cell_type": "code", "source": ["1 + 1"], "outputs": []}],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    dossier = tmp_path / "notebooks"
    dossier.mkdir()
    (dossier / "02_entrainement.ipynb").write_text(json.dumps(carnet), encoding="utf-8")
    monkeypatch.setattr(emballage, "PATHS", replace(emballage.PATHS, root=tmp_path))
    assert emballage._carnets_sans_sortie() == ["02_entrainement.ipynb"]


def test_la_copie_dit_si_le_fichier_manquait(emballage, tmp_path):
    """C'est ce booléen qui décide si l'archive peut être produite."""
    source = tmp_path / "present.txt"
    source.write_text("contenu", encoding="utf-8")
    assert emballage._copier(source, tmp_path / "copie" / "present.txt") is True
    assert emballage._copier(tmp_path / "absent.txt", tmp_path / "copie" / "absent.txt") is False


def test_la_revision_de_service_est_une_etiquette_de_modele(emballage):
    """La fiche doit épingler une version publiée, jamais la branche par défaut."""
    assert emballage._etiquette_publiee().startswith("modele-v")
