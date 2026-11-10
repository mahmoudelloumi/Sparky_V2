from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import uuid


# ============================================================
# ENUMS
# ============================================================

class DocumentType(str, Enum):
    facture = "facture"
    bon_livraison = "bon_livraison"
    bon_commande = "bon_commande"
    avoir = "avoir"
    devis = "devis"


class DocumentStatus(str, Enum):
    pending = "pending"
    classified = "classified"
    extracted = "extracted"
    error = "error"


# ============================================================
# CLASSIFICATION
# ============================================================

class ClassifyRequest(BaseModel):
    document_id: Optional[str] = None
    file_base64: Optional[str] = None   # base64 encoded file
    file_url: Optional[str] = None       # Supabase storage URL
    mime_type: str = "application/pdf"

class ClassifyPrediction(BaseModel):
    type_document: DocumentType
    score: float = Field(ge=0.0, le=1.0)

class ClassifyResponse(BaseModel):
    document_id: Optional[str]
    type_document: DocumentType
    score_confiance: float = Field(ge=0.0, le=1.0)
    top_predictions: List[ClassifyPrediction]
    modele_version: str
    duree_ms: int


# ============================================================
# EXTRACTION - Shared line item
# ============================================================

class LigneDocument(BaseModel):
    reference: Optional[str] = None
    designation: str
    quantite: Optional[Decimal] = None
    unite: Optional[str] = None
    prix_unitaire: Optional[Decimal] = None
    remise_pct: Optional[Decimal] = None
    montant_ht: Optional[Decimal] = None
    tva_taux: Optional[Decimal] = None
    montant_ttc: Optional[Decimal] = None
    marque: Optional[str] = None
    puissance_wc: Optional[Decimal] = None  # Photovoltaic specific
    rendement_pct: Optional[Decimal] = None


# ============================================================
# EXTRACTION - Facture
# ============================================================

class ExtractedFacture(BaseModel):
    numero_facture: Optional[str] = None
    date_facture: Optional[date] = None
    date_echeance: Optional[date] = None
    fournisseur_nom: Optional[str] = None
    fournisseur_siret: Optional[str] = None
    montant_ht: Optional[Decimal] = None
    tva_taux: Optional[Decimal] = None
    montant_tva: Optional[Decimal] = None
    montant_ttc: Optional[Decimal] = None
    devise: str = "TND"
    reference_commande: Optional[str] = None
    lignes: List[LigneDocument] = []


# ============================================================
# EXTRACTION - Bon de Livraison
# ============================================================

class ExtractedBonLivraison(BaseModel):
    numero_bl: Optional[str] = None
    date_livraison: Optional[date] = None
    fournisseur_nom: Optional[str] = None
    reference_commande: Optional[str] = None
    transporteur: Optional[str] = None
    numero_suivi: Optional[str] = None
    adresse_livraison: Optional[str] = None
    lignes: List[LigneDocument] = []


# ============================================================
# EXTRACTION - Bon de Commande
# ============================================================

class ExtractedBonCommande(BaseModel):
    numero_bc: Optional[str] = None
    date_commande: Optional[date] = None
    date_livraison_prev: Optional[date] = None
    fournisseur_nom: Optional[str] = None
    montant_ht: Optional[Decimal] = None
    montant_ttc: Optional[Decimal] = None
    devise: str = "TND"
    conditions_paiement: Optional[str] = None
    lignes: List[LigneDocument] = []


# ============================================================
# EXTRACTION - Avoir
# ============================================================

class ExtractedAvoir(BaseModel):
    numero_avoir: Optional[str] = None
    date_avoir: Optional[date] = None
    facture_reference: Optional[str] = None
    fournisseur_nom: Optional[str] = None
    montant_ht: Optional[Decimal] = None
    montant_ttc: Optional[Decimal] = None
    devise: str = "TND"
    motif: Optional[str] = None


# ============================================================
# EXTRACTION - Devis
# ============================================================

class ExtractedDevis(BaseModel):
    numero_devis: Optional[str] = None
    date_devis: Optional[date] = None
    date_validite: Optional[date] = None
    fournisseur_nom: Optional[str] = None
    montant_ht: Optional[Decimal] = None
    montant_ttc: Optional[Decimal] = None
    devise: str = "TND"
    conditions: Optional[str] = None
    lignes: List[LigneDocument] = []


# ============================================================
# EXTRACT REQUEST / RESPONSE
# ============================================================

class ExtractRequest(BaseModel):
    document_id: str
    type_document: DocumentType
    file_base64: Optional[str] = None
    file_url: Optional[str] = None
    mime_type: str = "application/pdf"

class ExtractResponse(BaseModel):
    document_id: str
    type_document: DocumentType
    donnees: dict   # ExtractedFacture | ExtractedDevis | etc.
    duree_ms: int
    saved: bool = False


# ============================================================
# COMPARE REQUEST / RESPONSE (Devis)
# ============================================================

class CompareItem(BaseModel):
    devis_id: str
    fournisseur_nom: str
    prix_unitaire: Decimal
    quantite: Optional[Decimal] = Decimal("1")
    montant_total: Optional[Decimal] = None

class CompareRequest(BaseModel):
    reference_produit: str
    designation: Optional[str] = None
    items: List[CompareItem]

class ComparePrixResult(BaseModel):
    devis_id: str
    fournisseur_nom: str
    prix_unitaire: Decimal
    quantite: Decimal
    montant_total: Decimal
    est_meilleur_prix: bool
    economie_vs_meilleur: Optional[Decimal] = None  # Difference vs cheapest
    devise: str = "TND"

class CompareResponse(BaseModel):
    reference_produit: str
    designation: Optional[str]
    resultats: List[ComparePrixResult]
    meilleur_prix: ComparePrixResult
    economie_max: Decimal   # Max savings vs most expensive


# ============================================================
# DOCUMENT UPLOAD
# ============================================================

class DocumentUploadResponse(BaseModel):
    document_id: str
    storage_url: str
    nom_fichier: str
    statut: DocumentStatus = DocumentStatus.pending


# ============================================================
# EXPORT EXCEL
# ============================================================

class ExportExcelRequest(BaseModel):
    nom_document: Optional[str] = None
    fournisseur_nom: Optional[str] = None
    type_document: Optional[str] = None
    lignes: List[LigneDocument]


# ============================================================
# ARTICLES — Catalogue canonique
# ============================================================

class ArticleSpecifications(BaseModel):
    """Specs structurées extraites du nom du produit pour le matching."""
    # Câbles
    section_mm2: Optional[float] = None        # 6, 16, 4 …
    couleur: Optional[str] = None              # rouge, noir, vert/jaune
    type_cable: Optional[str] = None           # PV, souple, rigide, acier
    nb_conducteurs: Optional[int] = None       # 1, 2, 3 …
    tension_v: Optional[float] = None          # 600, 275 …
    tension_type: Optional[str] = None         # DC, AC
    # Colliers / fixations
    longueur_mm: Optional[float] = None        # 250 → collier 250*4.7
    largeur_mm: Optional[float] = None         # 4.7
    materiau: Optional[str] = None             # plastique, inox, aluminium
    # Panneaux / modules
    puissance_wc: Optional[float] = None       # 590
    technologie: Optional[str] = None          # bifacial, monocristallin …
    marque_panneau: Optional[str] = None
    # Structure métallique
    profil: Optional[str] = None               # cornière, tube carré, IPE
    dim1_mm: Optional[float] = None            # 40 (40×40)
    dim2_mm: Optional[float] = None            # 40
    longueur_m: Optional[float] = None         # 6.5 m
    hauteur_m: Optional[float] = None          # H0.7, H1 …
    nb_panneaux: Optional[int] = None          # double, triple → 2, 3
    # Disjoncteurs / protection
    amperage_a: Optional[float] = None         # 16A, 32A
    nb_poles: Optional[int] = None             # 2P, 3P
    # Divers
    modules: Optional[int] = None              # coffret 8 modules
    puissance_kw: Optional[float] = None       # 8, 5 ... (Onduleurs)
    capacite_kwh: Optional[float] = None       # 5.12, 4.8 ... (Batteries)


class ArticleCanonique(BaseModel):
    id: Optional[str] = None
    reference_interne: str
    nom_normalise: str
    categorie: Optional[str] = None
    unite_mesure: Optional[str] = None
    specifications: Optional[ArticleSpecifications] = None
    description: Optional[str] = None
    actif: bool = True


class ArticleFournisseur(BaseModel):
    id: Optional[str] = None
    article_id: str
    fournisseur_id: str
    reference_fournisseur: Optional[str] = None
    nom_fournisseur: str
    prix_achat: Optional[Decimal] = None
    prix_vente: Optional[Decimal] = None
    marge_pct: Optional[Decimal] = None
    tva_taux: Optional[Decimal] = Decimal("19.0")
    marque: Optional[str] = None
    score_similarite: float = 1.0


class MouvementStock(BaseModel):
    id: Optional[str] = None
    article_id: str
    fournisseur_id: Optional[str] = None
    document_id: Optional[str] = None
    type_mouvement: str = "entree"          # entree | sortie | correction
    quantite: Decimal
    prix_unitaire: Optional[Decimal] = None
    date_mouvement: Optional[date] = None
    notes: Optional[str] = None


class StockArticle(BaseModel):
    """Résultat de la vue v_stock_articles."""
    id: str
    reference_interne: str
    nom_normalise: str
    categorie: Optional[str]
    unite_mesure: Optional[str]
    specifications: Optional[dict]
    quantite_totale: Decimal
    nb_fournisseurs: int
    derniere_entree: Optional[date]


class StockParFournisseur(BaseModel):
    """Résultat de la vue v_stock_par_fournisseur."""
    article_id: str
    reference_interne: str
    nom_normalise: str
    categorie: Optional[str]
    fournisseur_id: str
    fournisseur_nom: str
    nom_fournisseur: str
    reference_fournisseur: Optional[str]
    prix_achat: Optional[Decimal]
    prix_vente: Optional[Decimal]
    quantite: Decimal
    unite_mesure: Optional[str]


# ============================================================
# NORMALISATION — Requêtes / Réponses
# ============================================================

class NormalizeLigneRequest(BaseModel):
    """Demande de normalisation d'une ligne de document."""
    reference: Optional[str] = None
    designation: str
    quantite: Optional[Decimal] = None
    unite: Optional[str] = None
    prix_unitaire: Optional[Decimal] = None
    prix_vente: Optional[Decimal] = None
    marge_pct: Optional[Decimal] = None
    tva_taux: Optional[Decimal] = None
    marque: Optional[str] = None
    categorie_hint: Optional[str] = None    # hint de catégorie si déjà connu
    fournisseur_id: Optional[str] = None
    fournisseur_nom: Optional[str] = None   # nom lisible du fournisseur
    document_id: Optional[str] = None


class CandidatArticle(BaseModel):
    article_id: str
    reference_interne: str
    nom_normalise: str
    categorie: Optional[str]
    score: float                            # 0.0 → 1.0 similarité


class NormalizeLigneResponse(BaseModel):
    designation_originale: str
    reference_fournisseur: Optional[str]
    article_id: Optional[str]               # None si nouveau
    reference_interne: str
    nom_normalise: str
    categorie: Optional[str]
    specifications: Optional[dict]
    est_nouveau: bool                        # True = article créé, False = existant
    score_confiance: float
    candidats: List[CandidatArticle] = []   # autres candidats potentiels


class ImportDocumentRequest(BaseModel):
    """Importe toutes les lignes d'un document dans le stock."""
    document_id: str
    fournisseur_id: str
    fournisseur_nom: str                    # nom lisible, ex: "Energika", "SolarTech"
    type_mouvement: str = "entree"
    lignes: List[NormalizeLigneRequest]
    date_mouvement: Optional[date] = None
    seuil_similarite: float = 0.80          # score min pour matcher un article existant


class ImportDocumentResponse(BaseModel):
    document_id: str
    nb_lignes: int
    nb_nouveaux: int
    nb_matches: int
    nb_ambigus: int                         # lignes avec plusieurs candidats proches
    resultats: List[NormalizeLigneResponse]


# ============================================================
# COMPARAISON PRIX — Tableau croisé automatique
# ============================================================

class PrixFournisseur(BaseModel):
    fournisseur_id: str
    fournisseur_nom: str
    nom_fournisseur: str                    # désignation telle qu'écrite sur la facture
    reference_fournisseur: Optional[str]
    prix_achat: Optional[Decimal]
    prix_vente: Optional[Decimal]
    tva_taux: Optional[Decimal]
    marque: Optional[str]
    est_meilleur_prix: bool = False
    surcout_pct: Optional[float] = None     # % au-dessus du meilleur prix


class ArticlePrixComparaison(BaseModel):
    article_id: str
    reference_interne: str
    nom_normalise: str
    categorie: Optional[str]
    unite_mesure: Optional[str]
    quantite_stock: Decimal = Decimal("0")
    meilleur_prix_achat: Optional[Decimal]
    meilleur_fournisseur: Optional[str]
    economie_max_pct: Optional[float]       # % entre le moins cher et le plus cher
    prix_par_fournisseur: List[PrixFournisseur]


class ComparaisonPrixResponse(BaseModel):
    nb_articles: int
    fournisseurs: List[str]                 # liste ordonnée pour les en-têtes de colonnes
    articles: List[ArticlePrixComparaison]


# ============================================================
# COMPARAISON INTELLIGENTE — Cross-supplier avec normalisation
# ============================================================

class SmartCompareLigne(BaseModel):
    """Une ligne de document pour la comparaison intelligente."""
    designation: str
    reference: Optional[str] = None
    quantite: Optional[Decimal] = None
    unite: Optional[str] = None
    prix_unitaire: Optional[Decimal] = None
    remise_pct: Optional[Decimal] = None
    tva_taux: Optional[Decimal] = None
    montant_ht: Optional[Decimal] = None
    montant_ttc: Optional[Decimal] = None
    marque: Optional[str] = None


class SmartCompareDocument(BaseModel):
    """Un document (devis/facture) d'un fournisseur."""
    doc_id: str
    fournisseur: str
    lignes: List[SmartCompareLigne]


class SmartCompareRequest(BaseModel):
    """Requête de comparaison intelligente multi-fournisseurs."""
    documents: List[SmartCompareDocument]


class SmartCompareArticleCell(BaseModel):
    """Prix d'un fournisseur pour un article normalisé."""
    fournisseur: str
    designation_originale: str
    prix_unitaire: Optional[Decimal] = None
    quantite: Optional[Decimal] = None
    remise_pct: Optional[Decimal] = None
    tva_taux: Optional[Decimal] = None
    montant_ht: Optional[Decimal] = None
    reference: Optional[str] = None
    marque: Optional[str] = None
    est_meilleur_prix: bool = False


class SmartCompareArticleRow(BaseModel):
    """Un article normalisé avec les prix de chaque fournisseur."""
    nom_normalise: str
    reference_interne: str
    categorie: Optional[str] = None
    items: List[SmartCompareArticleCell]
    meilleur_prix: Optional[Decimal] = None
    ecart_pct: Optional[float] = None


class SmartCompareResponse(BaseModel):
    """Réponse de la comparaison intelligente."""
    nb_articles: int
    fournisseurs: List[str]
    articles: List[SmartCompareArticleRow]
    total_par_fournisseur: dict = {}      # fournisseur → total TTC
