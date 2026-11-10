"""
DB persistence layer for articles, articles_fournisseurs, fournisseurs.

All functions are async and use the SQLAlchemy AsyncSession already wired in database.py.

Typical flow:
  1. import_to_db(session, fournisseur_nom, lignes_result)
       → upserts fournisseur, articles, articles_fournisseurs
  2. get_comparaison_prix(session, categorie=None)
       → returns ComparaisonPrixResponse built from DB tables
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    ArticlePrixComparaison,
    ComparaisonPrixResponse,
    ImportDocumentResponse,
    NormalizeLigneResponse,
    PrixFournisseur,
)

import structlog
log = structlog.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# Upsert helpers
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_fournisseur(session: AsyncSession, fournisseur_id: str, nom: str) -> str:
    """
    Insert fournisseur if not exists, return its UUID.
    fournisseur_id may be a plain UUID or an opaque string ('fourn-A').
    We treat it as external_ref and map it to a stable PG UUID.
    """
    # Try to find by the external id stored in metadata
    row = await session.execute(
        text("SELECT id FROM fournisseurs WHERE metadata->>'external_id' = :eid LIMIT 1"),
        {"eid": fournisseur_id},
    )
    existing = row.fetchone()
    if existing:
        return str(existing[0])

    # Also try by name (same supplier, different import)
    row = await session.execute(
        text("SELECT id FROM fournisseurs WHERE nom = :nom LIMIT 1"),
        {"nom": nom},
    )
    existing = row.fetchone()
    if existing:
        return str(existing[0])

    # Insert
    new_id = str(uuid.uuid4())
    await session.execute(
        text("""
            INSERT INTO fournisseurs (id, nom, metadata)
            VALUES (:id, :nom, jsonb_build_object('external_id', :eid))
            ON CONFLICT DO NOTHING
        """),
        {"id": new_id, "nom": nom, "eid": fournisseur_id},
    )
    await session.commit()
    log.info("db.fournisseur_created", nom=nom, id=new_id)
    return new_id


async def upsert_article(
    session: AsyncSession,
    reference_interne: str,
    nom_normalise: str,
    categorie: Optional[str],
    unite_mesure: Optional[str],
    specifications: Optional[dict],
) -> str:
    """
    Insert canonical article if not exists (keyed on reference_interne), return UUID.
    """
    import json

    row = await session.execute(
        text("SELECT id FROM articles WHERE reference_interne = :ref LIMIT 1"),
        {"ref": reference_interne},
    )
    existing = row.fetchone()
    if existing:
        return str(existing[0])

    new_id = str(uuid.uuid4())
    specs_json = json.dumps(specifications or {})
    await session.execute(
        text("""
            INSERT INTO articles
                (id, reference_interne, nom_normalise, categorie, unite_mesure, specifications)
            VALUES (:id, :ref, :nom, :cat, :unite, :specs::jsonb)
            ON CONFLICT (reference_interne) DO NOTHING
        """),
        {
            "id": new_id, "ref": reference_interne, "nom": nom_normalise,
            "cat": categorie, "unite": unite_mesure, "specs": specs_json,
        },
    )
    await session.commit()
    log.info("db.article_created", reference_interne=reference_interne)
    return new_id


async def upsert_article_fournisseur(
    session: AsyncSession,
    article_id: str,
    fournisseur_id: str,
    nom_fournisseur: str,
    reference_fournisseur: Optional[str],
    prix_achat: Optional[float],
    prix_vente: Optional[float],
    tva_taux: Optional[float],
    marque: Optional[str],
) -> None:
    """
    Insert or update the supplier alias for an article.
    Key: (article_id, fournisseur_id).
    """
    # Check if row already exists
    row = await session.execute(
        text("""
            SELECT id FROM articles_fournisseurs
            WHERE article_id = :art AND fournisseur_id = :fourn
            LIMIT 1
        """),
        {"art": article_id, "fourn": fournisseur_id},
    )
    existing = row.fetchone()

    if existing:
        # Update prices (keep latest)
        await session.execute(
            text("""
                UPDATE articles_fournisseurs
                SET nom_fournisseur = :nom,
                    reference_fournisseur = COALESCE(:ref, reference_fournisseur),
                    prix_achat = COALESCE(:pa, prix_achat),
                    prix_vente = COALESCE(:pv, prix_vente),
                    tva_taux   = COALESCE(:tva, tva_taux),
                    marque     = COALESCE(:marque, marque),
                    updated_at = NOW()
                WHERE article_id = :art AND fournisseur_id = :fourn
            """),
            {
                "nom": nom_fournisseur, "ref": reference_fournisseur,
                "pa": prix_achat, "pv": prix_vente, "tva": tva_taux,
                "marque": marque, "art": article_id, "fourn": fournisseur_id,
            },
        )
    else:
        new_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO articles_fournisseurs
                    (id, article_id, fournisseur_id, nom_fournisseur, reference_fournisseur,
                     prix_achat, prix_vente, tva_taux, marque)
                VALUES (:id, :art, :fourn, :nom, :ref, :pa, :pv, :tva, :marque)
            """),
            {
                "id": new_id, "art": article_id, "fourn": fournisseur_id,
                "nom": nom_fournisseur, "ref": reference_fournisseur,
                "pa": prix_achat, "pv": prix_vente, "tva": tva_taux, "marque": marque,
            },
        )
    await session.commit()


async def add_mouvement_stock(
    session: AsyncSession,
    article_id: str,
    fournisseur_id: str,
    document_id: Optional[str],
    quantite: float,
    prix_unitaire: Optional[float],
    date_mouvement: Optional[str],
    type_mouvement: str = "entree",
) -> None:
    await session.execute(
        text("""
            INSERT INTO mouvements_stock
                (id, article_id, fournisseur_id, document_id, type_mouvement,
                 quantite, prix_unitaire, date_mouvement)
            VALUES (:id, :art, :fourn, :doc, :type, :qty, :pu,
                    COALESCE(:date::date, CURRENT_DATE))
        """),
        {
            "id": str(uuid.uuid4()),
            "art": article_id, "fourn": fournisseur_id, "doc": document_id,
            "type": type_mouvement, "qty": quantite, "pu": prix_unitaire,
            "date": date_mouvement,
        },
    )
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Persist a full import result to DB
# ─────────────────────────────────────────────────────────────────────────────

async def persist_import(
    session: AsyncSession,
    import_request,           # ImportDocumentRequest
    import_result: ImportDocumentResponse,
    lignes_map: dict,         # designation → NormalizeLigneRequest
) -> None:
    """
    After normalizing all lines in memory, persist articles + supplier prices + movements to DB.
    lignes_map: maps designation → original NormalizeLigneRequest for price lookup.
    """
    # Upsert the fournisseur
    fourn_db_id = await upsert_fournisseur(
        session, import_request.fournisseur_id, import_request.fournisseur_nom
    )

    for res in import_result.resultats:
        if not res.article_id:
            continue

        # Upsert the canonical article
        art_db_id = await upsert_article(
            session,
            reference_interne=res.reference_interne,
            nom_normalise=res.nom_normalise,
            categorie=res.categorie,
            unite_mesure=None,
            specifications=res.specifications,
        )

        # Find the original ligne for pricing
        ligne = lignes_map.get(res.designation_originale)
        if ligne:
            # Upsert supplier alias with price
            await upsert_article_fournisseur(
                session,
                article_id=art_db_id,
                fournisseur_id=fourn_db_id,
                nom_fournisseur=res.designation_originale,
                reference_fournisseur=res.reference_fournisseur,
                prix_achat=float(ligne.prix_unitaire) if ligne.prix_unitaire else None,
                prix_vente=float(ligne.prix_vente) if ligne.prix_vente else None,
                tva_taux=float(ligne.tva_taux) if ligne.tva_taux else None,
                marque=ligne.marque,
            )

            # Record stock movement if quantity > 0
            qty = float(ligne.quantite) if ligne.quantite else 0.0
            if qty > 0:
                await add_mouvement_stock(
                    session,
                    article_id=art_db_id,
                    fournisseur_id=fourn_db_id,
                    document_id=import_request.document_id,
                    quantite=qty,
                    prix_unitaire=float(ligne.prix_unitaire) if ligne.prix_unitaire else None,
                    date_mouvement=str(import_request.date_mouvement) if import_request.date_mouvement else None,
                    type_mouvement=import_request.type_mouvement,
                )

    log.info("db.import_persisted", document_id=import_request.document_id,
             nb_lignes=import_result.nb_lignes)


# ─────────────────────────────────────────────────────────────────────────────
# Read comparison from DB
# ─────────────────────────────────────────────────────────────────────────────

async def get_comparaison_prix(
    session: AsyncSession,
    categorie: Optional[str] = None,
) -> ComparaisonPrixResponse:
    """
    Build the price comparison table directly from devis/factures lines,
    grouping by designation across suppliers.
    """
    sql = """
        WITH all_lines AS (
            SELECT
                ld.designation,
                ld.reference,
                ld.unite,
                ld.prix_unitaire,
                NULL AS tva_taux,
                NULL AS marque,
                f.id   AS fournisseur_id,
                f.nom  AS fournisseur_nom,
                d.created_at AS line_date
            FROM lignes_devis ld
            JOIN devis dv      ON dv.id = ld.devis_id
            JOIN documents d   ON d.id = dv.document_id
            LEFT JOIN fournisseurs f ON f.id = dv.fournisseur_id
            WHERE ld.prix_unitaire IS NOT NULL
              AND ld.designation IS NOT NULL
            UNION ALL
            SELECT
                lf.designation,
                lf.reference,
                lf.unite,
                lf.prix_unitaire,
                lf.tva_taux,
                NULL AS marque,
                f.id   AS fournisseur_id,
                f.nom  AS fournisseur_nom,
                d.created_at AS line_date
            FROM lignes_facture lf
            JOIN factures fa   ON fa.id = lf.facture_id
            JOIN documents d   ON d.id = fa.document_id
            LEFT JOIN fournisseurs f ON f.id = fa.fournisseur_id
            WHERE lf.prix_unitaire IS NOT NULL
              AND lf.designation IS NOT NULL
        )
        SELECT
            md5(LOWER(REGEXP_REPLACE(designation, '[^a-zA-Z0-9]', '', 'g')))::text AS article_id,
            LOWER(REGEXP_REPLACE(designation, '[^a-zA-Z0-9]', '', 'g')) AS reference_interne,
            designation                         AS nom_normalise,
            NULL::text                          AS categorie,
            unite                               AS unite_mesure,
            0::numeric                          AS quantite_stock,
            COALESCE(fournisseur_id::text,
                     md5(COALESCE(fournisseur_nom, 'inconnu'))::text) AS fournisseur_id,
            COALESCE(fournisseur_nom, 'Inconnu') AS fournisseur_nom,
            designation                         AS nom_fournisseur,
            reference                           AS reference_fournisseur,
            prix_unitaire                       AS prix_achat,
            NULL::numeric                       AS prix_vente,
            tva_taux                            AS tva_taux,
            marque                              AS marque
        FROM all_lines
        ORDER BY nom_normalise, fournisseur_nom
    """
    params: dict = {}

    result = await session.execute(text(sql), params)
    rows = result.mappings().all()

    if not rows:
        return ComparaisonPrixResponse(nb_articles=0, fournisseurs=[], articles=[])

    if not rows:
        return ComparaisonPrixResponse(nb_articles=0, fournisseurs=[], articles=[])

    # Reconstruire le catalogue dynamiquement avec le normalizer pour le matching intelligent
    from services.normalizer import ProductNormalizer
    from models import NormalizeLigneRequest
    norm = ProductNormalizer()
    
    fournisseurs_set = {}
    
    for row in rows:
        fourn_id = str(row["fournisseur_id"])
        fourn_nom = row["fournisseur_nom"]
        if fourn_id not in fournisseurs_set:
            fournisseurs_set[fourn_id] = fourn_nom
            
        req = NormalizeLigneRequest(
            document_id="db",
            designation=row["nom_fournisseur"],
            reference=row["reference_fournisseur"],
            quantite=1.0,
            unite=row["unite_mesure"],
            prix_unitaire=row["prix_achat"],
            prix_vente=row["prix_vente"],
            remise_pct=0.0,
            tva_taux=row["tva_taux"],
            fournisseur_id=fourn_id,
            fournisseur_nom=fourn_nom,
            marque=row["marque"]
        )
        norm.normalize_ligne(req)

    # Construire la réponse depuis le catalogue en mémoire de norm
    articles_out = []
    cat = norm.catalogue
    
    for art in cat._articles.values():
        art_id = art["id"]
        aliases = cat._supplier_aliases.get(art_id, [])
        prix_disponibles = [a for a in aliases if a.get("prix_achat") is not None]
        if not prix_disponibles:
            continue
            
        meilleur = min(prix_disponibles, key=lambda a: float(a["prix_achat"]))
        meilleur_p = float(meilleur["prix_achat"])
        pire_p = max(float(a["prix_achat"]) for a in prix_disponibles)
        ecart_pct = round((pire_p - meilleur_p) / meilleur_p * 100, 1) if meilleur_p > 0 and pire_p != meilleur_p else 0.0
        
        prix_par_fourn = []
        for p in sorted(prix_disponibles, key=lambda x: float(x["prix_achat"])):
            prix_par_fourn.append(
                PrixFournisseur(
                    fournisseur_id=p["fournisseur_id"],
                    fournisseur_nom=p["fournisseur_nom_court"],
                    nom_fournisseur=p["nom_fournisseur"],
                    reference_fournisseur=p["reference_fournisseur"],
                    prix_achat=p["prix_achat"],
                    prix_vente=p.get("prix_vente"),
                    tva_taux=p.get("tva_taux"),
                    marque=p.get("marque"),
                    est_meilleur_prix=(p["fournisseur_id"] == meilleur["fournisseur_id"]),
                    surcout_pct=round((float(p["prix_achat"]) - meilleur_p) / meilleur_p * 100, 1) if meilleur_p > 0 and float(p["prix_achat"]) != meilleur_p else 0.0
                )
            )
            
        articles_out.append(ArticlePrixComparaison(
            article_id=art_id,
            reference_interne=art["reference_interne"],
            nom_normalise=art["nom_normalise"],
            categorie=art.get("categorie"),
            unite_mesure=art.get("unite_mesure"),
            quantite_stock=0,
            meilleur_prix_achat=meilleur["prix_achat"],
            meilleur_fournisseur=meilleur["fournisseur_nom_court"],
            economie_max_pct=ecart_pct,
            prix_par_fournisseur=prix_par_fourn,
        ))

    return ComparaisonPrixResponse(
        nb_articles=len(articles_out),
        fournisseurs=list(fournisseurs_set.values()),
        articles=articles_out,
    )
