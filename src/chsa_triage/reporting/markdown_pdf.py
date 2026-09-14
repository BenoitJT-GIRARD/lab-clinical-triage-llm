"""Rendu d'un document Markdown en PDF paginé.

Le rapport technique est un livrable, donc un binaire. Pour qu'il reste
vérifiable et rejouable, sa source est un fichier Markdown versionné et son PDF
est produit par ce module — pas par un traitement de texte.

Le sous-ensemble de Markdown reconnu est volontairement restreint à ce dont le
rapport a besoin : titres, paragraphes, listes — sur une ligne ou plusieurs —,
tableaux, images, citations, blocs de code et sauts de page. Une bibliothèque Markdown complète apporterait cent fonctions
inutiles et une dépendance de plus ; les vingt lignes de conversion ci-dessous
se lisent en entier.

Rien de tout cela ne dépend d'un outil système : ReportLab est une dépendance
Python pure, et la génération fonctionne donc aussi en intégration continue.
"""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

# Les polices de base de ReportLab sont encodées en WinAnsi, qui ne porte ni les
# signes de comparaison, ni l'exposant ordinal, ni les traits de tableau : ces
# caractères sortaient en carrés noirs. Une police Unicode est enregistrée pour
# eux, et pour eux seuls — le corps du texte garde Helvetica, donc ses métriques
# et sa pagination. La police voyage avec matplotlib, déjà nécessaire aux figures.
POLICE_UNICODE = "DejaVuSans"
POLICE_UNICODE_FIXE = "DejaVuSansMono"

CARACTERES_HORS_WINANSI = "─ᵉ∩▶≥≤−×·"


def _enregistrer_les_polices_unicode() -> bool:
    """Déclare les polices de secours à ReportLab. Faux si elles sont introuvables.

    Deux sont nécessaires : une proportionnelle pour le corps du texte, et une à
    chasse fixe pour les blocs de code, où le schéma d'architecture est dessiné
    avec des traits et des flèches.
    """
    from pathlib import Path

    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    dossier = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    fichiers = {
        POLICE_UNICODE: dossier / "DejaVuSans.ttf",
        POLICE_UNICODE_FIXE: dossier / "DejaVuSansMono.ttf",
    }
    if not all(fichier.exists() for fichier in fichiers.values()):
        return False
    for nom, fichier in fichiers.items():
        pdfmetrics.registerFont(TTFont(nom, str(fichier)))
    return True


POLICE_UNICODE_DISPONIBLE = _enregistrer_les_polices_unicode()

# Palette sobre, lisible en impression noir et blanc comme à l'écran.
BLEU = colors.HexColor("#1F4E79")
GRIS = colors.HexColor("#4A4A4A")
GRIS_CLAIR = colors.HexColor("#EDEDED")

MARGE = 20 * mm
LARGEUR_UTILE = A4[0] - 2 * MARGE
# Part de la largeur utile occupée par une figure.
PART_LARGEUR_FIGURE = 0.80
# Hauteur en dessous de laquelle une nouvelle partie passe à la page suivante :
# un titre suivi de deux lignes en bas de page n'a pas d'intérêt.
HAUTEUR_MINIMALE_DE_PARTIE = 120


def _styles() -> dict[str, ParagraphStyle]:
    """Feuille de styles du document."""
    base = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle(
            "titre", parent=base["Title"], fontSize=24, leading=30, textColor=BLEU, spaceAfter=6
        ),
        "sous_titre": ParagraphStyle(
            "sous_titre",
            parent=base["Normal"],
            fontSize=13,
            leading=18,
            textColor=GRIS,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontSize=16,
            leading=20,
            textColor=BLEU,
            spaceBefore=14,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=12.5,
            leading=16,
            textColor=BLEU,
            spaceBefore=10,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base["Heading3"],
            fontSize=11,
            leading=14,
            textColor=GRIS,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "corps": ParagraphStyle(
            "corps",
            parent=base["BodyText"],
            fontSize=9.5,
            leading=12.6,
            alignment=TA_JUSTIFY,
            spaceAfter=4,
        ),
        "liste": ParagraphStyle(
            "liste",
            parent=base["BodyText"],
            fontSize=9.5,
            leading=12.4,
            leftIndent=10,
            bulletIndent=2,
            spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "code",
            parent=base["BodyText"],
            fontName=POLICE_UNICODE_FIXE if POLICE_UNICODE_DISPONIBLE else "Courier",
            fontSize=7.5,
            leading=10,
            leftIndent=10,
            spaceBefore=2,
            spaceAfter=6,
        ),
        "citation": ParagraphStyle(
            "citation",
            parent=base["BodyText"],
            fontSize=9,
            leading=12.5,
            leftIndent=10,
            textColor=GRIS,
            borderPadding=4,
            spaceAfter=6,
        ),
        "legende": ParagraphStyle(
            "legende",
            parent=base["Normal"],
            fontSize=8,
            leading=11,
            textColor=GRIS,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "cellule": ParagraphStyle("cellule", parent=base["Normal"], fontSize=8, leading=10.5),
        "entete_cellule": ParagraphStyle(
            "entete_cellule",
            parent=base["Normal"],
            fontSize=8,
            leading=10.5,
            textColor=colors.white,
        ),
        "toc1": ParagraphStyle("toc1", fontSize=10, leading=16, spaceBefore=4),
        "toc2": ParagraphStyle("toc2", fontSize=9, leading=13, leftIndent=12),
    }


def _hors_winansi(texte: str) -> str:
    """Fait porter par la police Unicode les caractères qu'Helvetica ignore.

    La substitution est faite caractère par caractère : tout le reste du texte
    conserve la police du style, et la mise en page ne bouge pas.
    """
    if not POLICE_UNICODE_DISPONIBLE:
        return texte
    for caractere in CARACTERES_HORS_WINANSI:
        if caractere in texte:
            texte = texte.replace(caractere, f'<font face="{POLICE_UNICODE}">{caractere}</font>')
    return texte


def _inline(texte: str) -> str:
    """Convertit le balisage de ligne de Markdown vers celui de ReportLab."""
    echappe = texte.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    echappe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", echappe)
    echappe = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", echappe)
    echappe = re.sub(r"`(.+?)`", r'<font face="Courier" size="8.5">\1</font>', echappe)
    # Les liens sont rendus en bleu, cliquables dans la plupart des lecteurs.
    echappe = re.sub(r"\[(.+?)\]\((.+?)\)", r'<link href="\2" color="#1F4E79">\1</link>', echappe)
    return _hors_winansi(echappe)


def _bloc_de_code(lignes: list[str], index: int) -> tuple[str, int]:
    """Lit un bloc de code encadré par des délimiteurs et renvoie son contenu.

    Le contenu garde ses retours à la ligne : recopié dans un terminal, il doit
    fonctionner tel quel. Sans ce traitement, les commandes se suivaient dans un
    paragraphe justifié, délimiteurs compris.

    Renvoie le texte du bloc et l'indice de sa ligne de fermeture.
    """
    corps = []
    suivant = index + 1
    while suivant < len(lignes) and not lignes[suivant].strip().startswith("```"):
        corps.append(lignes[suivant].rstrip())
        suivant += 1
    return "\n".join(corps), suivant


def _element_de_liste(lignes: list[str], index: int, texte: str) -> tuple[str, int]:
    """Rassemble un élément de liste et ses lignes de continuation.

    Une puce du rapport tient souvent sur trois lignes. Lues une par une, les
    lignes suivantes devenaient des paragraphes autonomes : la puce perdait son
    retrait, et un gras ouvert sur la première ligne se refermait dans le vide,
    laissant ses astérisques visibles dans le PDF.

    Renvoie le texte complet et l'indice de sa dernière ligne.
    """
    morceaux = [texte]
    suivant = index + 1
    while suivant < len(lignes):
        brute = lignes[suivant]
        depouillee = brute.strip()
        if not depouillee or not brute.startswith((" ", "\t")):
            break
        if re.match(r"^[-*] |^\d+\. ", depouillee):
            break
        if depouillee.startswith(("#", "|", "![", "> ")):
            break
        morceaux.append(depouillee)
        suivant += 1
    return " ".join(morceaux), suivant - 1


class _Document(BaseDocTemplate):
    """Document A4 avec numérotation des pages et enregistrement des titres."""

    def __init__(self, chemin: Path, titre: str, pied: str):
        super().__init__(
            str(chemin),
            pagesize=A4,
            leftMargin=MARGE,
            rightMargin=MARGE,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=titre,
            author="Benoit Girard",
        )
        self.pied = pied
        cadre = Frame(MARGE, 18 * mm, LARGEUR_UTILE, A4[1] - 36 * mm, id="corps")
        self.addPageTemplates(
            PageTemplate(id="standard", frames=[cadre], onPage=self._pied_de_page)
        )

    def _pied_de_page(self, canevas, document) -> None:
        """Dessine le pied de page et le numéro de page."""
        canevas.saveState()
        canevas.setFont("Helvetica", 7.5)
        canevas.setFillColor(GRIS)
        canevas.drawString(MARGE, 11 * mm, self.pied)
        canevas.drawRightString(A4[0] - MARGE, 11 * mm, f"page {document.page}")
        canevas.setStrokeColor(GRIS_CLAIR)
        canevas.line(MARGE, 14 * mm, A4[0] - MARGE, 14 * mm)
        canevas.restoreState()

    def afterFlowable(self, flowable) -> None:
        """Alimente la table des matières à partir des titres rencontrés."""
        if not isinstance(flowable, Paragraph):
            return
        niveau = {"h1": 0, "h2": 1}.get(flowable.style.name)
        if niveau is not None:
            self.notify("TOCEntry", (niveau, flowable.getPlainText(), self.page))


def _tableau(lignes: list[str], styles: dict) -> Table:
    """Construit un tableau à partir de lignes Markdown de la forme `| a | b |`."""
    cellules = [
        [c.strip() for c in ligne.strip().strip("|").split("|")]
        for ligne in lignes
        if not re.fullmatch(r"\|[\s:|-]+\|", ligne.strip())
    ]
    # Une ligne plus courte que l'en-tête ferait échouer le rendu : on complète.
    nombre_colonnes = max(len(ligne) for ligne in cellules)
    donnees = [
        [
            Paragraph(_inline(valeur), styles["entete_cellule" if index == 0 else "cellule"])
            for valeur in [*ligne, *[""] * (nombre_colonnes - len(ligne))]
        ]
        for index, ligne in enumerate(cellules)
    ]
    # Première colonne plus large : elle porte les intitulés.
    largeurs = [LARGEUR_UTILE * 0.28] + [LARGEUR_UTILE * 0.72 / (nombre_colonnes - 1)] * (
        nombre_colonnes - 1
    )
    tableau = Table(donnees, colWidths=largeurs, repeatRows=1, hAlign="LEFT")
    tableau.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BLEU),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS_CLAIR]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return tableau


def _image(chemin: Path, legende: str, styles: dict) -> list:
    """Insère une figure mise à l'échelle de la largeur utile, avec sa légende."""
    from reportlab.lib.utils import ImageReader

    largeur_source, hauteur_source = ImageReader(str(chemin)).getSize()
    # Une figure ne va pas jusqu'aux marges : elle paraîtrait plus large que le
    # texte justifié qui l'entoure. Elle est centrée dans la colonne, et gagne au
    # passage la hauteur correspondante — un rapport borné à vingt pages compte
    # aussi en millimètres.
    largeur = min(LARGEUR_UTILE * PART_LARGEUR_FIGURE, largeur_source)
    hauteur = hauteur_source * largeur / largeur_source
    # Une figure ne doit jamais occuper plus des deux tiers d'une page.
    hauteur_maximale = (A4[1] - 36 * mm) * 0.62
    if hauteur > hauteur_maximale:
        largeur *= hauteur_maximale / hauteur
        hauteur = hauteur_maximale
    image = Image(str(chemin), width=largeur, height=hauteur)
    image.hAlign = "CENTER"
    elements = [image]
    if legende:
        elements.append(Spacer(1, 3))
        elements.append(Paragraph(_inline(legende), styles["legende"]))
    return [KeepTogether(elements)]


def _page_de_titre(titre: str, sous_titres: list[str], styles: dict) -> list:
    """Compose la page de titre."""
    elements: list = [Spacer(1, 45 * mm), Paragraph(titre, styles["titre"]), Spacer(1, 6)]
    for ligne in sous_titres:
        elements.append(Paragraph(_inline(ligne), styles["sous_titre"]))
    elements.append(Spacer(1, 18 * mm))
    return elements


def render(
    markdown: str,
    destination: Path,
    titre: str,
    sous_titres: list[str],
    pied: str,
    racine_images: Path,
) -> int:
    """Rend le document et renvoie le nombre de pages produites."""
    styles = _styles()
    document = _Document(destination, titre, pied)

    table_des_matieres = TableOfContents()
    table_des_matieres.levelStyles = [styles["toc1"], styles["toc2"]]

    elements: list = _page_de_titre(titre, sous_titres, styles)
    elements += [Paragraph("Sommaire", styles["h1"]), table_des_matieres, PageBreak()]

    lignes = markdown.splitlines()
    index = 0
    paragraphe: list[str] = []

    def vider_paragraphe() -> None:
        if paragraphe:
            elements.append(Paragraph(_inline(" ".join(paragraphe)), styles["corps"]))
            paragraphe.clear()

    while index < len(lignes):
        ligne = lignes[index]
        depouillee = ligne.strip()

        if not depouillee:
            vider_paragraphe()
        elif depouillee.startswith("```"):
            vider_paragraphe()
            corps, index = _bloc_de_code(lignes, index)
            elements.append(Preformatted(corps, styles["code"]))
        elif depouillee == r"\pagebreak":
            vider_paragraphe()
            # Saut **conditionnel** : une nouvelle partie commence en haut de page
            # si la page courante est presque pleine, et continue à la suite
            # sinon. Un saut inconditionnel ouvrirait huit pages neuves dans ce
            # rapport, dont trois de blanc pur — payer trois pages sur vingt pour
            # de l'esthétique serait un mauvais arbitrage dans un document
            # plafonné.
            elements.append(CondPageBreak(HAUTEUR_MINIMALE_DE_PARTIE))
        elif depouillee.startswith("### "):
            vider_paragraphe()
            elements.append(Paragraph(_inline(depouillee[4:]), styles["h3"]))
        elif depouillee.startswith("## "):
            vider_paragraphe()
            elements.append(Paragraph(_inline(depouillee[3:]), styles["h2"]))
        elif depouillee.startswith("# "):
            vider_paragraphe()
            elements.append(Paragraph(_inline(depouillee[2:]), styles["h1"]))
        elif depouillee.startswith("> "):
            vider_paragraphe()
            elements.append(Paragraph(_inline(depouillee[2:]), styles["citation"]))
        elif depouillee.startswith("!["):
            vider_paragraphe()
            correspondance = re.match(r"!\[(.*?)\]\((.+?)\)", depouillee)
            if correspondance:
                elements += _image(
                    racine_images / correspondance.group(2), correspondance.group(1), styles
                )
        elif depouillee.startswith("|"):
            vider_paragraphe()
            bloc = []
            while index < len(lignes) and lignes[index].strip().startswith("|"):
                bloc.append(lignes[index])
                index += 1
            elements.append(_tableau(bloc, styles))
            elements.append(Spacer(1, 8))
            continue
        elif depouillee.startswith(("- ", "* ")):
            vider_paragraphe()
            texte, index = _element_de_liste(lignes, index, depouillee[2:])
            elements.append(Paragraph(_inline(texte), styles["liste"], bulletText="•"))
        elif re.match(r"^\d+\. ", depouillee):
            vider_paragraphe()
            numero, debut = depouillee.split(". ", 1)
            texte, index = _element_de_liste(lignes, index, debut)
            elements.append(Paragraph(_inline(texte), styles["liste"], bulletText=f"{numero}."))
        else:
            paragraphe.append(depouillee)
        index += 1

    vider_paragraphe()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Deux passes : la première recense les titres, la seconde numérote le sommaire.
    document.multiBuild(elements)
    return document.page
