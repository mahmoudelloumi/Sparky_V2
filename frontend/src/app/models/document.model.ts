// ============================================================
// SPARKY - Document Models (mirroring backend Pydantic models)
// ============================================================

export type DocumentType =
  | 'facture'
  | 'bon_livraison'
  | 'bon_commande'
  | 'avoir'
  | 'devis';

export type DocumentStatus =
  | 'pending'
  | 'classified'
  | 'extracted'
  | 'error';

export interface DocumentUploadResponse {
  document_id: string;
  storage_url: string;
  nom_fichier: string;
  statut: DocumentStatus;
}

// ---- Classification ----

export interface ClassifyPrediction {
  type_document: DocumentType;
  score: number;
}

export interface ClassifyResponse {
  document_id: string | null;
  type_document: DocumentType;
  score_confiance: number;
  top_predictions: ClassifyPrediction[];
  modele_version: string;
  duree_ms: number;
}

// ---- Line item ----

export interface LigneDocument {
  reference?: string;
  designation: string;
  quantite?: number;
  unite?: string;
  prix_unitaire?: number;
  remise_pct?: number;
  montant_ht?: number;
  tva_taux?: number;
  marque?: string;
  puissance_wc?: number;
  rendement_pct?: number;
}

// ---- Extracted types ----

export interface ExtractedFacture {
  numero_facture?: string;
  date_facture?: string;
  date_echeance?: string;
  fournisseur_nom?: string;
  montant_ht?: number;
  tva_taux?: number;
  montant_tva?: number;
  montant_ttc?: number;
  devise: string;
  reference_commande?: string;
  lignes: LigneDocument[];
}

export interface ExtractedDevis {
  numero_devis?: string;
  date_devis?: string;
  date_validite?: string;
  fournisseur_nom?: string;
  montant_ht?: number;
  montant_ttc?: number;
  devise: string;
  conditions?: string;
  lignes: LigneDocument[];
}

export interface ExtractedBonLivraison {
  numero_bl?: string;
  date_livraison?: string;
  fournisseur_nom?: string;
  reference_commande?: string;
  transporteur?: string;
  numero_suivi?: string;
  lignes: LigneDocument[];
}

export interface ExtractedBonCommande {
  numero_bc?: string;
  date_commande?: string;
  fournisseur_nom?: string;
  montant_ht?: number;
  montant_ttc?: number;
  devise: string;
  lignes: LigneDocument[];
}

export interface ExtractedAvoir {
  numero_avoir?: string;
  date_avoir?: string;
  facture_reference?: string;
  fournisseur_nom?: string;
  montant_ht?: number;
  montant_ttc?: number;
  devise: string;
  motif?: string;
}

export type ExtractedData =
  | ExtractedFacture
  | ExtractedDevis
  | ExtractedBonLivraison
  | ExtractedBonCommande
  | ExtractedAvoir;

export interface ExtractResponse {
  document_id: string;
  type_document: DocumentType;
  donnees: ExtractedData;
  duree_ms: number;
  saved: boolean;
}

// ---- Price comparison ----

export interface CompareItem {
  devis_id: string;
  fournisseur_nom: string;
  prix_unitaire: number;
  quantite?: number;
  montant_total?: number;
}

export interface CompareRequest {
  reference_produit: string;
  designation?: string;
  items: CompareItem[];
}

export interface ComparePrixResult {
  devis_id: string;
  fournisseur_nom: string;
  prix_unitaire: number;
  quantite: number;
  montant_total: number;
  est_meilleur_prix: boolean;
  economie_vs_meilleur?: number;
  devise: string;
}

export interface CompareResponse {
  reference_produit: string;
  designation?: string;
  resultats: ComparePrixResult[];
  meilleur_prix: ComparePrixResult;
  economie_max: number;
}

// ---- Catalogue prix ----

export interface PrixFournisseur {
  fournisseur_id: string;
  fournisseur_nom: string;
  nom_fournisseur: string;
  reference_fournisseur?: string;
  prix_achat?: number;
  prix_vente?: number;
  tva_taux?: number;
  marque?: string;
  est_meilleur_prix: boolean;
  surcout_pct?: number;
}

export interface ArticlePrixComparaison {
  article_id: string;
  reference_interne: string;
  nom_normalise: string;
  categorie?: string;
  unite_mesure?: string;
  quantite_stock: number;
  meilleur_prix_achat?: number;
  meilleur_fournisseur?: string;
  economie_max_pct?: number;
  prix_par_fournisseur: PrixFournisseur[];
}

export interface ComparaisonPrixResponse {
  nb_articles: number;
  fournisseurs: string[];
  articles: ArticlePrixComparaison[];
}

// ---- Smart Compare (cross-supplier intelligent grouping) ----

export interface SmartCompareLigne {
  designation: string;
  reference?: string;
  quantite?: number;
  unite?: string;
  prix_unitaire?: number;
  remise_pct?: number;
  tva_taux?: number;
  montant_ht?: number;
  montant_ttc?: number;
  marque?: string;
}

export interface SmartCompareDocument {
  doc_id: string;
  fournisseur: string;
  lignes: SmartCompareLigne[];
}

export interface SmartCompareRequest {
  documents: SmartCompareDocument[];
}

export interface SmartCompareArticleCell {
  fournisseur: string;
  designation_originale: string;
  prix_unitaire?: number;
  quantite?: number;
  remise_pct?: number;
  tva_taux?: number;
  montant_ht?: number;
  reference?: string;
  marque?: string;
  est_meilleur_prix: boolean;
}

export interface SmartCompareArticleRow {
  nom_normalise: string;
  reference_interne: string;
  categorie?: string;
  items: SmartCompareArticleCell[];
  meilleur_prix?: number;
  ecart_pct?: number;
}

export interface SmartCompareResponse {
  nb_articles: number;
  fournisseurs: string[];
  articles: SmartCompareArticleRow[];
  total_par_fournisseur: { [key: string]: number };
}

// ---- UI-only helper ----

export interface DocumentCard {
  document_id: string;
  nom_fichier: string;
  storage_url: string;
  thumbnail_url?: string;
  type_document?: DocumentType;
  statut: DocumentStatus;
  score_confiance?: number;
  fournisseur_nom?: string;
  montant_ttc?: number;
  numero_document?: string;
  created_at: string;
  lignes?: LigneDocument[];
}

export const DOCUMENT_TYPE_LABELS: { [key: string]: string } = {
  facture: 'Facture',
  bon_livraison: 'Bon de Livraison',
  bon_commande: 'Bon de Commande',
  avoir: 'Avoir',
  devis: 'Devis',
};

export const DOCUMENT_TYPE_ICONS: { [key: string]: string } = {
  facture: 'receipt',
  bon_livraison: 'local_shipping',
  bon_commande: 'shopping_cart',
  avoir: 'undo',
  devis: 'request_quote',
};

export const DOCUMENT_TYPE_COLORS: { [key: string]: string } = {
  facture: '#1976d2',
  bon_livraison: '#388e3c',
  bon_commande: '#f57c00',
  avoir: '#d32f2f',
  devis: '#7b1fa2',
};
