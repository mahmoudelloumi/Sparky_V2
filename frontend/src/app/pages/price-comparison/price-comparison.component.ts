import { Component, OnInit, signal, computed } from '@angular/core';
import { ApiService } from '../../services/api.service';
import { DocumentService } from '../../services/document.service';
import {
  DocumentCard,
  LigneDocument,
  SmartCompareRequest,
  SmartCompareResponse,
  SmartCompareArticleRow,
  SmartCompareArticleCell,
} from '../../models/document.model';
import { of } from 'rxjs';
import { catchError } from 'rxjs/operators';

// ── Types ───────────────────────────────────────────────────────────────────

export interface DocResult {
  id: string;
  nom_fichier: string;
  fournisseur: string;
  type: string;
  lignes: LigneDocument[];
  loading: boolean;
  error: string | null;
}

// ── Component ───────────────────────────────────────────────────────────────

@Component({
  standalone: false,
  selector: 'app-price-comparison',
  templateUrl: './price-comparison.component.html',
  styleUrls: ['./price-comparison.component.scss'],
})
export class PriceComparisonComponent implements OnInit {

  // ── state ──
  existingDocs   = signal<DocumentCard[]>([]);
  selectedIds    = signal<Set<string>>(new Set());
  docResults     = signal<DocResult[]>([]);
  isDragging     = signal(false);
  globalError    = signal<string | null>(null);
  stockMap       = signal<Map<string, number>>(new Map());

  // Smart comparison state
  smartResult       = signal<SmartCompareResponse | null>(null);
  smartLoading      = signal(false);
  smartError        = signal<string | null>(null);

  // ── computed ──
  fournisseurs = computed(() => {
    const smart = this.smartResult();
    if (smart) return smart.fournisseurs;
    return [...new Set(this.docResults().map(d => d.fournisseur).filter(Boolean))];
  });

  articleRows = computed((): SmartCompareArticleRow[] => {
    const smart = this.smartResult();
    if (smart) return smart.articles;
    return [];
  });

  anyLoading = computed(() =>
    this.docResults().some(d => d.loading) || this.smartLoading()
  );

  articlesAvecEcart = computed(() => {
    const smart = this.smartResult();
    if (!smart || !smart.articles) return 0;
    return smart.articles.filter(a => (a.ecart_pct || 0) > 0).length;
  });

  // ────────────────────────────────────────────────────────────────────────

  constructor(
    private api: ApiService,
    private docService: DocumentService,
  ) {}

  ngOnInit(): void {
    this.existingDocs.set(
      this.docService.documents().filter(d =>
        d.statut === 'extracted' &&
        (d.type_document === 'devis' || d.type_document === 'facture' ||
         d.type_document === 'bon_commande')
      )
    );
    this._loadStock();
  }

  // ── Import from existing documents ─────────────────────────────────────

  toggleExistingDoc(doc: DocumentCard): void {
    const next = new Set(this.selectedIds());
    if (next.has(doc.document_id)) {
      next.delete(doc.document_id);
      this.docResults.update(r => r.filter(d => d.id !== doc.document_id));
    } else {
      next.add(doc.document_id);
      this._addExistingDoc(doc);
    }
    this.selectedIds.set(next);
    this._triggerSmartCompare();
  }

  isSelected(doc: DocumentCard): boolean {
    return this.selectedIds().has(doc.document_id);
  }

  private _addExistingDoc(doc: DocumentCard): void {
    const lignes = doc.lignes || [];
    const result: DocResult = {
      id: doc.document_id,
      nom_fichier: doc.nom_fichier,
      fournisseur: doc.fournisseur_nom || doc.nom_fichier.replace(/\.[^.]+$/, ''),
      type: doc.type_document || 'devis',
      lignes,
      loading: false,
      error: lignes.length === 0 ? 'Aucun article extrait — ré-importez ce fichier.' : null,
    };
    this.docResults.update(r => [...r, result]);
  }

  // ── File drag & drop / upload ─────────────────────────────────────────

  onDragOver(e: DragEvent): void {
    e.preventDefault();
    this.isDragging.set(true);
  }

  onDragLeave(): void { this.isDragging.set(false); }

  onDrop(e: DragEvent): void {
    e.preventDefault();
    this.isDragging.set(false);
    const files = Array.from(e.dataTransfer?.files || []);
    this._processFiles(files);
  }

  onFilePick(e: Event): void {
    const input = e.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    this._processFiles(files);
    input.value = '';
  }

  private _processFiles(files: File[]): void {
    for (const file of files) {
      const id = 'file-' + Math.random().toString(36).slice(2);
      const isJfif = /\.jfif$/i.test(file.name);
      const placeholder: DocResult = {
        id,
        nom_fichier: file.name,
        fournisseur: file.name.replace(/\.[^.]+$/, ''),
        type: 'devis',
        lignes: [],
        loading: !isJfif,
        error: isJfif ? 'Les fichiers JFIF ne sont pas acceptés. Convertissez-les en JPG, PNG ou PDF.' : null,
      };
      this.docResults.update(r => [...r, placeholder]);
      if (isJfif) continue;

      this.api.processDocument(file).subscribe({
        next: (res: any) => {
          const lignes: LigneDocument[] = res.extraction?.donnees?.lignes || [];
          const fournisseur = res.extraction?.donnees?.fournisseur_nom || placeholder.fournisseur;
          const type = res.classification?.type_document || 'devis';
          this.docResults.update(r => r.map(d =>
            d.id === id ? { ...d, loading: false, lignes, fournisseur, type } : d
          ));
          this._triggerSmartCompare();
        },
        error: (err: any) => {
          this.docResults.update(r => r.map(d =>
            d.id === id ? { ...d, loading: false, error: err.message } : d
          ));
        },
      });
    }
  }

  // ── Remove doc ────────────────────────────────────────────────────────

  removeDoc(id: string): void {
    this.docResults.update(r => r.filter(d => d.id !== id));
    const next = new Set(this.selectedIds());
    next.delete(id);
    this.selectedIds.set(next);
    this._triggerSmartCompare();
  }

  // ── Fournisseur name edit ─────────────────────────────────────────────

  updateFournisseur(id: string, value: string): void {
    this.docResults.update(r => r.map(d =>
      d.id === id ? { ...d, fournisseur: value } : d
    ));
    this._triggerSmartCompare();
  }

  // ── Reset ─────────────────────────────────────────────────────────────

  reset(): void {
    this.docResults.set([]);
    this.selectedIds.set(new Set());
    this.globalError.set(null);
    this.smartResult.set(null);
    this.smartError.set(null);
  }

  // ── Smart Compare ─────────────────────────────────────────────────────

  private _smartDebounce: ReturnType<typeof setTimeout> | null = null;

  private _triggerSmartCompare(): void {
    // Debounce to avoid spamming the backend during rapid changes
    if (this._smartDebounce) clearTimeout(this._smartDebounce);
    this._smartDebounce = setTimeout(() => this._runSmartCompare(), 300);
  }

  private _runSmartCompare(): void {
    const docs = this.docResults().filter(d => !d.loading && !d.error && d.lignes.length > 0);

    // Need at least 1 document for comparison (or just to display items)
    if (docs.length < 1) {
      this.smartResult.set(null);
      this.smartError.set(null);
      return;
    }

    const payload: SmartCompareRequest = {
      documents: docs.map(d => ({
        doc_id: d.id,
        fournisseur: d.fournisseur,
        lignes: d.lignes.map(l => ({
          designation: l.designation,
          reference: l.reference,
          quantite: l.quantite,
          unite: l.unite,
          prix_unitaire: l.prix_unitaire,
          remise_pct: l.remise_pct,
          tva_taux: l.tva_taux,
          montant_ht: l.montant_ht,
          marque: l.marque,
        })),
      })),
    };

    this.smartLoading.set(true);
    this.smartError.set(null);

    this.api.smartCompare(payload).pipe(
      catchError(err => {
        this.smartError.set(err.message || 'Erreur de comparaison intelligente');
        this.smartLoading.set(false);
        return of(null);
      })
    ).subscribe(res => {
      this.smartLoading.set(false);
      if (res) {
        this.smartResult.set(res);
      }
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────

  bestPrice(row: SmartCompareArticleRow): number | null {
    const prices = row.items
      .map(i => i.prix_unitaire != null ? Number(i.prix_unitaire) : null)
      .filter((p): p is number => p != null && p > 0);
    return prices.length ? Math.min(...prices) : null;
  }

  isBest(cell: SmartCompareArticleCell): boolean {
    return cell.est_meilleur_prix;
  }

  getCellForFournisseur(row: SmartCompareArticleRow, fournisseur: string): SmartCompareArticleCell | null {
    return row.items.find(i => i.fournisseur === fournisseur) || null;
  }

  getDocResult(id: string): DocResult | undefined {
    return this.docResults().find(d => d.id === id);
  }

  totalParFournisseur(fournisseur: string): number {
    const smart = this.smartResult();
    if (smart && smart.total_par_fournisseur) {
      return smart.total_par_fournisseur[fournisseur] || 0;
    }
    return 0;
  }

  isBestTotal(fournisseur: string): boolean {
    const smart = this.smartResult();
    if (!smart || !smart.total_par_fournisseur) return false;
    const totals = Object.values(smart.total_par_fournisseur).filter(t => t > 0);
    if (!totals.length) return false;
    const best = Math.min(...totals);
    return Math.abs(this.totalParFournisseur(fournisseur) - best) < 0.001;
  }

  private _loadStock(): void {
    this.api.getComparaisonPrix().pipe(
      catchError(() => of(null))
    ).subscribe(res => {
      if (!res) return;
      const map = new Map<string, number>();
      for (const article of res.articles) {
        map.set(article.nom_normalise.trim().toLowerCase(), article.quantite_stock);
      }
      this.stockMap.set(map);
    });
  }

  getStock(row: SmartCompareArticleRow): number | null {
    return this.stockMap().get(row.nom_normalise.trim().toLowerCase()) ?? null;
  }
}
