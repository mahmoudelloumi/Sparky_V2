"""
Product Normalization Service
=============================
Matches incoming invoice lines to canonical articles in the database.

Matching strategy (cascading, stops at first confident match):

  1. Exact reference match  — même référence fournisseur déjà connue → score 1.0
  2. Spec-based match        — specs extraites identiques (même catégorie + dimensions) → score 0.85–0.99
  3. Trigram similarity      — similarité de texte sur le nom normalisé → score proportionnel
  4. No match                → nouvel article créé, score 1.0 (définition)

Spec extraction covers the following product families encountered in PV invoices:
  - Câbles (PV, souple, rigide, acier) — section mm², couleur, nb conducteurs
  - Colliers plastique / inox          — longueur × largeur mm
  - Structures métalliques             — profil, dimensions, longueur, hauteur, nb panneaux
  - Panneaux photovoltaïques           — puissance Wc, technologie
  - Disjoncteurs / parafoudres         — ampérage, nb pôles, tension, AC/DC
  - Coffrets                           — nb modules
  - Supports / embouts / accessoires   — dimensions si présentes
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Optional

try:
    import structlog
    log = structlog.get_logger()
except ImportError:
    import logging
    log = logging.getLogger(__name__)  # type: ignore[assignment]

from models import (
    ArticleSpecifications,
    CandidatArticle,
    ImportDocumentRequest,
    ImportDocumentResponse,
    NormalizeLigneRequest,
    NormalizeLigneResponse,
)

log = structlog.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _norm_text(text: str) -> str:
    """Lowercase, remove accents, collapse spaces, unify separators."""
    t = _strip_accents(text.lower())
    t = re.sub(r"[_\-/\\]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _trigram_similarity(a: str, b: str) -> float:
    """Pure-Python trigram similarity (no pg_trgm dependency in Python layer)."""
    def trigrams(s: str):
        s = f"  {s} "
        return {s[i:i+3] for i in range(len(s) - 2)}

    ta, tb = trigrams(a), trigrams(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ─────────────────────────────────────────────────────────────────────────────
# Category detection
# ─────────────────────────────────────────────────────────────────────────────

_CATEGORY_PATTERNS: list[tuple[str, list[str]]] = [
    # Câbles must be checked BEFORE panneaux to avoid "câble PV" → panneaux
    ("Câbles",                   ["cable", "fil souple", "fil rigide", "cv0", "pv_6", "cr16",
                                   "conducteur", "souple", "rigide", "fil 1*", "fil 1x",
                                   "cable pv", "cable solaire"]),
    ("Panneaux photovoltaïque",  ["panneau", "photovoltaique", "module pv", "solar", "bifacial",
                                   "monocristallin", "ppv", "plaque solaire", "vertex"]),
    ("Onduleur",                 ["onduleur", "inverter", "convertisseur", "hybride"]),
    ("Batterie",                 ["batterie", "stockage", "lithium", "lifepo4", "accumulateur", "pylontech"]),
    ("Structure métallique",     ["corniere", "tube carre", "strm", "ipe", "cr40", "tcr40", "cac0"]),
    ("Aluminium",                ["support simple", "support triple", "ss500", "st460", "s.s34"]),
    ("Disjoncteur diff",         ["disjoncteur", "ddf", "disj diff"]),
    ("Parafoudre",               ["parafoudre", "pardc", "parac"]),
    ("Accessoires électrique",   ["coffret", "fiche mc4", "fmc4", "embout", "prise indus",
                                   "fiche indus", "pi32", "fi32", "ce8m"]),
    ("Colliers",                 ["collier plast", "collier plastic", "colp"]),
    ("CES",                      ["anode magnesium"]),
    ("Divers",                   ["collier inox", "colinx", "anode", "embout"]),
]


def detect_category(designation: str, reference: str = "") -> str:
    text = _norm_text(f"{reference} {designation}")
    for category, keywords in _CATEGORY_PATTERNS:
        if any(kw in text for kw in keywords):
            return category
    return "Divers"


# ─────────────────────────────────────────────────────────────────────────────
# Spec extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_specs(designation: str, reference: str = "", category: str = "") -> ArticleSpecifications:
    """
    Parse the product designation + reference to produce structured specs.
    These specs are used both for storage (JSONB) and for similarity scoring.
    """
    text = _norm_text(f"{reference} {designation}")
    specs = ArticleSpecifications()

    # ── Cables ──────────────────────────────────────────────────────────────
    if "câble" in text or "cable" in text or "fil " in text or category == "Câbles":
        # section mm²  →  "6mm2", "16mm2", "6 mm2", "1*6"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*mm2?", text)
        if not m:
            m = re.search(r"\d+\*(\d+(?:[.,]\d+)?)", text)  # "1*6" → section=6
        if m:
            specs.section_mm2 = float(m.group(1).replace(",", "."))

        # colour
        if "noir" in text:
            specs.couleur = "noir"
        elif "rouge" in text:
            specs.couleur = "rouge"
        elif "vert" in text or "jaune" in text or " v/j" in text or "terre" in text:
            specs.couleur = "vert/jaune"

        # nb conductors  →  "1*6" → 1
        m = re.search(r"(\d+)\*\d", text)
        if m:
            specs.nb_conducteurs = int(m.group(1))

        # cable type
        if "pv" in text:
            specs.type_cable = "PV"
        elif "souple" in text:
            specs.type_cable = "souple"
        elif "rigide" in text:
            specs.type_cable = "rigide"
        elif "acier" in text:
            specs.type_cable = "acier"

    # ── Colliers (cable ties) ───────────────────────────────────────────────
    if "collier" in text or "colp" in text:
        # "250*4.7", "250x4.7"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*[*x×]\s*(\d+(?:[.,]\d+)?)", text)
        if m:
            specs.longueur_mm = float(m.group(1).replace(",", "."))
            specs.largeur_mm  = float(m.group(2).replace(",", "."))

        if "inox" in text:
            specs.materiau = "inox"
        elif "plast" in text:
            specs.materiau = "plastique"

    # ── Panneaux PV ─────────────────────────────────────────────────────────
    if ("panneau" in text or "photovoltaique" in text or "ppv" in text
            or "module pv" in text or "solar" in text or "plaque" in text
            or category == "Panneaux photovoltaïque"):
        # puissance "590wc", "590 wc", "545 w", "545 watts"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:w|wc|watts)(?:\b|$)", text)
        if m:
            specs.puissance_wc = float(m.group(1).replace(",", "."))

        if "bifacial" in text:
            specs.technologie = "bifacial"
        elif "mono" in text:
            specs.technologie = "monocristallin"
        elif "poly" in text:
            specs.technologie = "polycristallin"

    # ── Onduleurs ───────────────────────────────────────────────────────────
    if category == "Onduleur" or any(k in text for k in ["onduleur", "inverter", "convertisseur"]):
        # puissance "8kw", "8 kw", "8000w", "8kva"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kw|kva|w)(?:\b|$)", text)
        if m:
            val = float(m.group(1).replace(",", "."))
            unit = m.group(2)
            if unit == "w" and val > 100:  # e.g., 8000W -> 8kW
                val = val / 1000
            specs.puissance_kw = val
            
        if "hybride" in text:
            specs.technologie = "hybride"
        if "triphase" in text:
            specs.tension_type = "triphasé"
        elif "monophase" in text:
            specs.tension_type = "monophasé"

    # ── Batteries ───────────────────────────────────────────────────────────
    if category == "Batterie" or any(k in text for k in ["batterie", "stockage", "lithium", "lifepo4"]):
        # capacite "5.12 kwh", "4.8kwh"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*kwh", text)
        if m:
            specs.capacite_kwh = float(m.group(1).replace(",", "."))
            
        # tension "48v", "51.2v"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*v(?:\b|$)", text)
        if m:
            specs.tension_v = float(m.group(1).replace(",", "."))

    # ── Structures métalliques ───────────────────────────────────────────────
    if any(k in text for k in ["corniere", "tube carre", "strm", "ipe", "cac0", "cr40", "tcr40"]):
        # dims "40*40", "40x40"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*[*x×]\s*(\d+(?:[.,]\d+)?)", text)
        if m:
            specs.dim1_mm = float(m.group(1).replace(",", "."))
            specs.dim2_mm = float(m.group(2).replace(",", "."))

        # longueur "6.5m", "6.5 m"
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:\b|$)", text)
        if m:
            specs.longueur_m = float(m.group(1).replace(",", "."))

        # hauteur "h0.7", "h1", "h0.5"
        m = re.search(r"h\s*(\d+(?:[.,]\d+)?)", text)
        if m:
            specs.hauteur_m = float(m.group(1).replace(",", "."))

        # nb panneaux "double" → 2, "triple" → 3
        if "double" in text:
            specs.nb_panneaux = 2
        elif "triple" in text:
            specs.nb_panneaux = 3

        # profil
        if "corniere" in text:
            specs.profil = "cornière"
        elif "tube carre" in text:
            specs.profil = "tube carré"
        elif "ipe" in text:
            specs.profil = "IPE"

        if "alum" in text:
            specs.materiau = "aluminium"
        elif "acier" in text or "peinture" in text:
            specs.materiau = "acier peint"

    # ── Supports aluminium ───────────────────────────────────────────────────
    if "support" in text:
        # "500", "460", "340" from reference or name
        m = re.search(r"(\d{3,4})", text)
        if m:
            specs.dim1_mm = float(m.group(1))

        # "55cm", "30cm"
        m = re.search(r"(\d+)\s*cm", text)
        if m:
            specs.longueur_m = float(m.group(1)) / 100

        if "triple" in text:
            specs.nb_panneaux = 3
        elif "simple" in text:
            specs.nb_panneaux = 1

    # ── Disjoncteurs / parafoudres ──────────────────────────────────────────
    if (any(k in text for k in ["disjoncteur", "ddf", "parafoudre", "pardc", "parac"])
            or category in ("Disjoncteur diff", "Parafoudre")
            or "disj" in text):
        # amperage "16a", "32a"
        m = re.search(r"(\d+)\s*a\b", text)
        if m:
            specs.amperage_a = float(m.group(1))

        # poles "2p", "3p"
        m = re.search(r"(\d)\s*p\b", text)
        if m:
            specs.nb_poles = int(m.group(1))

        # tension "600vdc", "275vac"
        m = re.search(r"(\d+)\s*v\s*(dc|ac)?", text)
        if m:
            specs.tension_v = float(m.group(1))
            if m.group(2):
                specs.tension_type = m.group(2).upper()
            elif "dc" in text:
                specs.tension_type = "DC"
            elif "ac" in text:
                specs.tension_type = "AC"

    # ── Coffrets ────────────────────────────────────────────────────────────
    if "coffret" in text or "ce8m" in text:
        m = re.search(r"(\d+)\s*mod", text)
        if m:
            specs.modules = int(m.group(1))

    return specs


# ─────────────────────────────────────────────────────────────────────────────
# Canonical name builder
# ─────────────────────────────────────────────────────────────────────────────

def build_nom_normalise(designation: str, specs: ArticleSpecifications, category: str) -> str:
    """
    Build a clean, canonical product name from the raw designation + specs.
    The goal is a human-readable name that is consistent across suppliers.
    Specs and category are used to append missing but important information
    (e.g. power rating or dimensions) when they were extracted but absent from
    the raw designation text.
    """
    # Start from a cleaned version of the original designation
    name = re.sub(r"<[^>]+>", "", designation.strip()).strip()

    # Unify spelling variants
    replacements = [
        (r"\bplastic\b",  "Plastique", re.IGNORECASE),
        (r"\bsouple\b",   "Souple",    re.IGNORECASE),
        (r"\brigide\b",   "Rigide",    re.IGNORECASE),
        (r"\balum\b",     "Aluminium", re.IGNORECASE),
        (r"\bpeinture\b", "Peinture",  re.IGNORECASE),
    ]
    for pattern, repl, flags in replacements:
        name = re.sub(pattern, repl, name, flags=flags)

    # Append key discriminating specs when not already present in the name
    name_lc = name.lower()
    if category == "Panneaux photovoltaïque" and specs.puissance_wc and str(int(specs.puissance_wc)) not in name_lc:
        name += f" {int(specs.puissance_wc)}Wc"
    if category == "Onduleur" and hasattr(specs, "puissance_kw") and specs.puissance_kw:
        kw_str = f"{specs.puissance_kw:g}".replace('.', ',')
        if kw_str not in name_lc and str(int(specs.puissance_kw * 1000)) not in name_lc:
            name += f" {kw_str}kW"
    if category == "Batterie" and hasattr(specs, "capacite_kwh") and specs.capacite_kwh:
        kwh_str = f"{specs.capacite_kwh:g}".replace('.', ',')
        if kwh_str not in name_lc:
            name += f" {kwh_str}kWh"
    if category == "Câbles" and specs.section_mm2 and "mm" not in name_lc:
        name += f" {specs.section_mm2}mm²"
    if category in ("Structure métallique", "Aluminium"):
        if specs.dim1_mm and specs.dim2_mm and "x" not in name_lc and "*" not in name_lc:
            name += f" {int(specs.dim1_mm)}x{int(specs.dim2_mm)}"
        if specs.longueur_m and str(specs.longueur_m) not in name_lc:
            name += f" {specs.longueur_m}M"
    if category in ("Disjoncteur diff", "Parafoudre"):
        if specs.amperage_a and str(int(specs.amperage_a)) + "a" not in name_lc:
            name += f" {int(specs.amperage_a)}A"

    return re.sub(r"\s+", " ", name).strip()


def build_reference_interne(designation: str, specs: ArticleSpecifications, category: str) -> str:
    """
    Build a stable internal reference key from category + specs.
    This is the canonical ID that stays consistent across suppliers.
    Examples:
      COLLIER-PLAST-250x4.7
      CABLE-PV-6-NOIR
      PANNEAU-PV-590WC-BIFACIAL
      STRUCT-CORNIERE-ALUM-40x40-6.5M
      DISJ-DIFF-2P-16A
    """
    parts = []

    # category prefix
    cat_map = {
        "Câbles":                  "CABLE",
        "Colliers":                "COLLIER",
        "Panneaux photovoltaïque": "PANNEAU-PV",
        "Onduleur":                "OND",
        "Batterie":                "BAT",
        "Structure métallique":    "STRUCT",
        "Aluminium":               "SUPPORT",
        "Disjoncteur diff":        "DISJ-DIFF",
        "Parafoudre":              "PARAFOUDRE",
        "Accessoires électrique":  "ACCES",
        "CES":                     "CES",
        "Divers":                  "DIVERS",
    }
    prefix = cat_map.get(category, "ART")
    parts.append(prefix)

    # append discriminating specs
    if specs.type_cable:
        parts.append(specs.type_cable)
    if specs.section_mm2 is not None:
        parts.append(f"{specs.section_mm2:.0f}MM2".rstrip(".0").replace(".0", ""))
    if specs.couleur:
        parts.append(specs.couleur.upper())
    if specs.nb_conducteurs is not None:
        parts.append(f"{specs.nb_conducteurs}C")

    if specs.longueur_mm is not None and specs.largeur_mm is not None:
        parts.append(f"{specs.longueur_mm:.0f}x{specs.largeur_mm}".replace(".0", ""))
    if specs.materiau:
        parts.append(specs.materiau.upper()[:4])

    if specs.puissance_wc is not None:
        parts.append(f"{int(specs.puissance_wc)}WC")
    if getattr(specs, "puissance_kw", None) is not None:
        parts.append(f"{specs.puissance_kw:g}KW".replace(".", ""))
    if getattr(specs, "capacite_kwh", None) is not None:
        parts.append(f"{specs.capacite_kwh:g}KWH".replace(".", ""))
    if specs.technologie:
        parts.append(specs.technologie.upper()[:6])

    if specs.profil:
        parts.append(specs.profil.upper().replace(" ", "")[:6])
    if specs.dim1_mm is not None:
        dim = f"{specs.dim1_mm:.0f}".rstrip("0").rstrip(".")
        if specs.dim2_mm is not None:
            dim += f"x{specs.dim2_mm:.0f}".rstrip("0").rstrip(".")
        parts.append(dim)
    if specs.longueur_m is not None:
        parts.append(f"{specs.longueur_m}M")
    if specs.hauteur_m is not None:
        parts.append(f"H{specs.hauteur_m}M")
    if specs.nb_panneaux is not None:
        parts.append(f"{specs.nb_panneaux}P")

    if specs.amperage_a is not None:
        parts.append(f"{int(specs.amperage_a)}A")
    if specs.nb_poles is not None:
        parts.append(f"{specs.nb_poles}P")
    if specs.tension_v is not None:
        t = f"{int(specs.tension_v)}V"
        if specs.tension_type:
            t += specs.tension_type
        parts.append(t)

    if specs.modules is not None:
        parts.append(f"{specs.modules}MOD")

    # if no discriminating specs extracted, fall back to cleaned designation slug
    if len(parts) == 1:
        slug = re.sub(r"[^A-Z0-9]", "-", designation.upper())
        slug = re.sub(r"-+", "-", slug).strip("-")[:40]
        parts.append(slug)

    return "-".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Spec similarity scorer
# ─────────────────────────────────────────────────────────────────────────────

def specs_similarity(a: ArticleSpecifications, b: ArticleSpecifications) -> float:
    """
    Returns 0.0 → 1.0 similarity between two spec objects.

    Rules:
    - Any field that is set in BOTH objects and differs → 0.0  (hard mismatch)
    - Fields that match                                → boost score
    - Fields where one side is None                   → neutral (ignored)
    """
    a_dict = {k: v for k, v in a.model_dump().items() if v is not None}
    b_dict = {k: v for k, v in b.model_dump().items() if v is not None}

    common_keys = set(a_dict) & set(b_dict)
    if not common_keys:
        # No shared spec fields — can't confirm match via specs alone
        return 0.5

    matches = 0
    for key in common_keys:
        va, vb = a_dict[key], b_dict[key]
        if isinstance(va, float) or isinstance(vb, float):
            # numeric tolerance ±1%
            if abs(float(va) - float(vb)) <= max(abs(float(va)), abs(float(vb))) * 0.01:
                matches += 1
            else:
                return 0.0   # hard mismatch on a numeric spec
        else:
            if str(va).lower() == str(vb).lower():
                matches += 1
            else:
                return 0.0   # hard mismatch on a string spec

    return matches / len(common_keys)


# ─────────────────────────────────────────────────────────────────────────────
# In-memory catalogue (used when no DB is available)
# ─────────────────────────────────────────────────────────────────────────────

class InMemoryCatalogue:
    """
    Lightweight in-memory catalogue for use when the DB layer is not wired.
    Stores articles as dicts keyed by reference_interne.
    Each entry: {id, reference_interne, nom_normalise, categorie, unite_mesure, specs}
    """
    def __init__(self) -> None:
        self._articles: dict[str, dict] = {}   # reference_interne → article dict
        # Supplier alias index: (fournisseur_id, reference_fournisseur) → article_id
        self._supplier_refs: dict[tuple[str, str], str] = {}
        # Stock movements: article_id → list of {type, quantite, fournisseur_id}
        self._movements: dict[str, list[dict]] = {}
        # Price aliases: article_id → list of alias dicts (one per fournisseur)
        # Each alias: {fournisseur_id, fournisseur_nom_court, nom_fournisseur,
        #              reference_fournisseur, prix_achat, prix_vente, tva_taux, marque}
        self._supplier_aliases: dict[str, list[dict]] = {}

    def find_by_supplier_ref(self, fournisseur_id: str, reference: str) -> Optional[dict]:
        key = (fournisseur_id, reference)
        art_id = self._supplier_refs.get(key)
        if art_id:
            return next((a for a in self._articles.values() if a["id"] == art_id), None)
        return None

    def find_candidates(
        self,
        nom: str,
        specs: ArticleSpecifications,
        categorie: str,
        top_k: int = 5,
    ) -> list[tuple[dict, float]]:
        """Return (article, score) pairs sorted by score desc."""
        results = []
        nom_n = _norm_text(nom)

        for art in self._articles.values():
            if art.get("categorie") != categorie:
                # Different category → skip (hard rule)
                continue

            art_specs = ArticleSpecifications(**(art.get("specifications") or {}))
            spec_score = specs_similarity(specs, art_specs)

            if spec_score == 0.0:
                continue   # hard spec mismatch

            text_score = _trigram_similarity(nom_n, _norm_text(art["nom_normalise"]))

            # When specs match perfectly, they are the canonical identity of the
            # product — a high base score is guaranteed regardless of text similarity
            if spec_score == 1.0:
                score = 0.90 + 0.10 * text_score
            elif spec_score == 0.5:
                # No shared specs to compare (e.g. "Divers" category). Rely heavily on text similarity.
                score = 0.2 * spec_score + 0.8 * text_score
            else:
                score = 0.6 * spec_score + 0.4 * text_score
            results.append((art, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def add_article(self, article: dict) -> None:
        self._articles[article["reference_interne"]] = article

    def register_supplier_ref(self, fournisseur_id: str, reference: str, article_id: str) -> None:
        if fournisseur_id and reference:
            self._supplier_refs[(fournisseur_id, reference)] = article_id

    def register_supplier_alias(self, article_id: str, alias: dict) -> None:
        """
        Store/update a price alias for an article.
        alias must contain at least: fournisseur_id, fournisseur_nom_court, nom_fournisseur.
        If a fournisseur_id already exists for this article, update it in-place
        (keeps latest price from that supplier).
        """
        if article_id not in self._supplier_aliases:
            self._supplier_aliases[article_id] = []
        existing = self._supplier_aliases[article_id]
        fid = alias.get("fournisseur_id", "")
        for i, a in enumerate(existing):
            if a.get("fournisseur_id") == fid:
                existing[i] = {**a, **alias}   # update in-place
                return
        existing.append(alias)


# ─────────────────────────────────────────────────────────────────────────────
# Main Normalizer service
# ─────────────────────────────────────────────────────────────────────────────

class ProductNormalizer:
    """
    Normalizes product lines from invoices/delivery notes into canonical articles.

    Usage (standalone, no DB):
        normalizer = ProductNormalizer()
        result = normalizer.normalize_ligne(ligne_request)

    Usage (with DB catalogue injected):
        normalizer = ProductNormalizer(catalogue=db_catalogue)
    """

    def __init__(self, catalogue: Optional[InMemoryCatalogue] = None):
        self.catalogue = catalogue or InMemoryCatalogue()

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def normalize_ligne(
        self,
        ligne: NormalizeLigneRequest,
        seuil: float = 0.80,
    ) -> NormalizeLigneResponse:
        """
        Normalize one invoice line.
        Returns a NormalizeLigneResponse with the matched or newly created article.
        """
        designation = ligne.designation.strip()
        reference   = (ligne.reference or "").strip()
        fournisseur = ligne.fournisseur_id or ""

        # 1. Exact supplier-reference match
        if reference and fournisseur:
            existing = self.catalogue.find_by_supplier_ref(fournisseur, reference)
            if existing:
                log.debug("normalizer.exact_ref_match", ref=reference)
                self._register_alias(existing["id"], ligne, fournisseur, fournisseur)
                return self._response_from_existing(designation, reference, existing, score=1.0)

        # 2. Detect category + extract specs
        category = detect_category(designation, reference)
        if ligne.categorie_hint:
            category = ligne.categorie_hint

        specs = extract_specs(designation, reference, category)

        # 3. Search catalogue for candidates
        candidates = self.catalogue.find_candidates(
            nom=designation, specs=specs, categorie=category
        )

        if candidates:
            best_art, best_score = candidates[0]
            if best_score >= seuil:
                # Confident match
                log.info(
                    "normalizer.matched",
                    designation=designation,
                    matched_to=best_art["nom_normalise"],
                    score=round(best_score, 3),
                )
                # Register this supplier reference for future exact matches
                if reference and fournisseur:
                    self.catalogue.register_supplier_ref(fournisseur, reference, best_art["id"])

                # Register price alias for the comparison table
                self._register_alias(best_art["id"], ligne, fournisseur, fournisseur)

                candidats = [
                    CandidatArticle(
                        article_id=a["id"],
                        reference_interne=a["reference_interne"],
                        nom_normalise=a["nom_normalise"],
                        categorie=a.get("categorie"),
                        score=round(s, 3),
                    )
                    for a, s in candidates[1:4]
                ]
                return self._response_from_existing(
                    designation, reference, best_art, score=best_score, candidats=candidats
                )

        # 4. No confident match → create new canonical article
        nom_normalise     = build_nom_normalise(designation, specs, category)
        reference_interne = build_reference_interne(designation, specs, category)

        # Ensure uniqueness of reference_interne in the catalogue
        base_ref = reference_interne
        counter  = 2
        while reference_interne in {a["reference_interne"] for a in self.catalogue._articles.values()}:
            reference_interne = f"{base_ref}-{counter}"
            counter += 1

        new_article = {
            "id":                uuid.uuid4().hex,
            "reference_interne": reference_interne,
            "nom_normalise":     nom_normalise,
            "categorie":         category,
            "unite_mesure":      ligne.unite,
            "specifications":    specs.model_dump(exclude_none=True),
        }
        self.catalogue.add_article(new_article)

        if reference and fournisseur:
            self.catalogue.register_supplier_ref(fournisseur, reference, new_article["id"])

        # Register price alias for the comparison table
        if fournisseur:
            self._register_alias(new_article["id"], ligne, fournisseur, fournisseur)

        log.info("normalizer.new_article", reference_interne=reference_interne, designation=designation)

        return NormalizeLigneResponse(
            designation_originale=designation,
            reference_fournisseur=reference or None,
            article_id=new_article["id"],
            reference_interne=reference_interne,
            nom_normalise=nom_normalise,
            categorie=category,
            specifications=new_article["specifications"],
            est_nouveau=True,
            score_confiance=1.0,
            candidats=[
                CandidatArticle(
                    article_id=a["id"],
                    reference_interne=a["reference_interne"],
                    nom_normalise=a["nom_normalise"],
                    categorie=a.get("categorie"),
                    score=round(s, 3),
                )
                for a, s in candidates[:3]
            ],
        )

    def import_document(self, request: ImportDocumentRequest) -> ImportDocumentResponse:
        """
        Normalize all lines from a document and record stock movements.
        Returns a summary + per-line results.
        """
        resultats = []
        nb_nouveaux = nb_matches = nb_ambigus = 0

        for ligne in request.lignes:
            updates: dict = {}
            if not ligne.fournisseur_id:
                updates["fournisseur_id"] = request.fournisseur_id
            if not ligne.fournisseur_nom:
                updates["fournisseur_nom"] = request.fournisseur_nom
            if not ligne.document_id:
                updates["document_id"] = request.document_id
            if updates:
                ligne = ligne.model_copy(update=updates)

            res = self.normalize_ligne(ligne, seuil=request.seuil_similarite)
            resultats.append(res)

            if res.est_nouveau:
                nb_nouveaux += 1
            elif len(res.candidats) > 1 and res.score_confiance < 0.95:
                nb_ambigus += 1
            else:
                nb_matches += 1

        return ImportDocumentResponse(
            document_id=request.document_id,
            nb_lignes=len(request.lignes),
            nb_nouveaux=nb_nouveaux,
            nb_matches=nb_matches,
            nb_ambigus=nb_ambigus,
            resultats=resultats,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _register_alias(
        self,
        article_id: str,
        ligne: "NormalizeLigneRequest",
        fournisseur_id: str,
        fournisseur_nom_court: str,
    ) -> None:
        """Store the supplier price alias for the price comparison table."""
        # Use fournisseur_nom from the ligne if available, else fall back to the passed name
        nom = (ligne.fournisseur_nom or fournisseur_nom_court or fournisseur_id).strip()
        self.catalogue.register_supplier_alias(article_id, {
            "fournisseur_id":        fournisseur_id,
            "fournisseur_nom_court": nom,
            "nom_fournisseur":       ligne.designation.strip(),
            "reference_fournisseur": (ligne.reference or "").strip() or None,
            "prix_achat":            float(ligne.prix_unitaire) if ligne.prix_unitaire is not None else None,
            "prix_vente":            float(ligne.prix_vente)    if ligne.prix_vente    is not None else None,
            "tva_taux":              float(ligne.tva_taux)      if ligne.tva_taux      is not None else None,
            "marque":                ligne.marque,
        })

    @staticmethod
    def _response_from_existing(
        designation: str,
        reference: str,
        article: dict,
        score: float,
        candidats: list[CandidatArticle] | None = None,
    ) -> NormalizeLigneResponse:
        return NormalizeLigneResponse(
            designation_originale=designation,
            reference_fournisseur=reference or None,
            article_id=article["id"],
            reference_interne=article["reference_interne"],
            nom_normalise=article["nom_normalise"],
            categorie=article.get("categorie"),
            specifications=article.get("specifications"),
            est_nouveau=False,
            score_confiance=round(score, 4),
            candidats=candidats or [],
        )
