import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';
import {
  DocumentUploadResponse,
  ClassifyResponse,
  ExtractResponse,
  CompareRequest,
  CompareResponse,
  ComparaisonPrixResponse,
  SmartCompareRequest,
  SmartCompareResponse,
  DocumentType,
} from '../models/document.model';

export interface ClassifyRequest {
  document_id?: string;
  file_base64?: string;
  file_url?: string;
  mime_type?: string;
}

export interface ExtractRequest {
  document_id: string;
  type_document: DocumentType;
  file_base64?: string;
  file_url?: string;
  mime_type?: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly base = environment.apiUrl;

  constructor(private http: HttpClient) {}

  uploadDocument(file: File): Observable<DocumentUploadResponse> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http
      .post<DocumentUploadResponse>(`${this.base}/upload`, form)
      .pipe(catchError(this.handleError));
  }

  processDocument(file: File): Observable<any> {
    const form = new FormData();
    form.append('file', file, file.name);
    return this.http
      .post<any>(`${this.base}/process`, form)
      .pipe(catchError(this.handleError));
  }

  classifyDocument(payload: ClassifyRequest): Observable<ClassifyResponse> {
    return this.http
      .post<ClassifyResponse>(`${this.base}/classify`, payload)
      .pipe(catchError(this.handleError));
  }

  extractDocument(payload: ExtractRequest): Observable<ExtractResponse> {
    return this.http
      .post<ExtractResponse>(`${this.base}/extract`, payload)
      .pipe(catchError(this.handleError));
  }

  comparePrices(payload: CompareRequest): Observable<CompareResponse> {
    return this.http
      .post<CompareResponse>(`${this.base}/compare`, payload)
      .pipe(catchError(this.handleError));
  }

  smartCompare(payload: SmartCompareRequest): Observable<SmartCompareResponse> {
    return this.http
      .post<SmartCompareResponse>(`${this.base}/compare/smart`, payload)
      .pipe(catchError(this.handleError));
  }

  exportExcel(lignes: any[], nomDocument?: string, fournisseurNom?: string): Observable<Blob> {
    return this.http.post(
      `${this.base}/export/excel`,
      { lignes, nom_document: nomDocument, fournisseur_nom: fournisseurNom },
      { responseType: 'blob' }
    ).pipe(catchError(this.handleError));
  }

  getComparaisonPrix(categorie?: string): Observable<ComparaisonPrixResponse> {
    const params = categorie ? `?categorie=${encodeURIComponent(categorie)}` : '';
    return this.http
      .get<ComparaisonPrixResponse>(`${this.base}/articles/comparaison-prix${params}`)
      .pipe(catchError(this.handleError));
  }

  getDocuments(limit = 100): Observable<any[]> {
    return this.http
      .get<any[]>(`${this.base}/documents?limit=${limit}`)
      .pipe(catchError(this.handleError));
  }

  deleteDocument(documentId: string): Observable<{ deleted: string }> {
    return this.http
      .delete<{ deleted: string }>(`${this.base}/documents/${documentId}`)
      .pipe(catchError(this.handleError));
  }

  login(email: string, password: string): Observable<{ user_id: string; email: string; nom: string; role: string }> {
    return this.http
      .post<{ user_id: string; email: string; nom: string; role: string }>(
        `${this.base}/auth/login`,
        { email, password },
      )
      .pipe(catchError(this.handleError));
  }

  health(): Observable<{ status: string }> {
    return this.http
      .get<{ status: string }>(`${this.base}/health`)
      .pipe(catchError(this.handleError));
  }

  private handleError(error: HttpErrorResponse): Observable<never> {
    let message = 'Une erreur inattendue est survenue';
    if (error.status === 0) {
      message = 'Impossible de joindre le serveur. Vérifiez votre connexion.';
    } else if (error.error?.detail) {
      message = error.error.detail;
    } else if (error.message) {
      message = error.message;
    }
    return throwError(() => new Error(message));
  }
}
