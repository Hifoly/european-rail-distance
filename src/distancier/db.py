"""Chargement d'un calcul exporté dans PostgreSQL/PostGIS (dépendance facultative psycopg)."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from distancier.calcul import referentiel_gares

NUM = {"temps_theorique", "temps_theorique_350", "temps_score_350", "temps_pratique", "temps_score", "distance_km", "part_lgv_pct", "distance_plus_courte_km", "distance_sans_lgv_km", "distance_au_plus_court_km", "distance_tgv_commercial_km",
       "distance_controle_km", "ecart_controle_pct", "controle_pk_km", "ecart_pk_pct"}
COLS = ["id", "code_uic_origine", "code_uic_destination", "itineraire_retenu", "distance_km", "part_lgv_pct",
        "type_ligne", "lignes_empruntees", "distance_au_plus_court_km", "distance_tgv_commercial_km", "desserte_tgv",
        "temps_theorique", "temps_theorique_350", "temps_pratique", "temps_score", "temps_score_350", "distance_plus_courte_km", "distance_sans_lgv_km", "moteur",
        "distance_controle_km", "source_controle", "ecart_controle_pct", "controle_pk_km", "ecart_pk_pct",
        "statut", "source", "date_consultation", "remarques"]


def _val(c, v):
    if v == "":
        return None
    return float(v) if c in NUM else v


def charger(cfg: dict, fichier_csv: Path, dsn: str | None = None) -> int:
    import psycopg

    dsn = dsn or os.environ["DATABASE_URL"]
    racine = cfg["_racine"]
    suffixe = fichier_csv.stem.removeprefix("distances_")
    meta_path = fichier_csv.with_name(f"calcul_{suffixe}.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    with open(fichier_csv, encoding="utf-8-sig", newline="") as f:
        lignes = [r for r in csv.DictReader(f, delimiter=";") if r.get("distance_km")]
    vitesses = [c for c in (lignes[0].keys() if lignes else []) if c.startswith("dont_km_")]

    with psycopg.connect(dsn) as cx, cx.cursor() as cur:
        cur.execute((racine / "sql" / "schema.sql").read_text(encoding="utf-8"))
        gares = referentiel_gares(cfg)
        try:
            from distancier.sources import sncf
            ref = sncf.dernieres_gares(cfg)
            for g in gares.values():
                r = ref.get(g["uic"])
                if not g.get("lon") and r and r["lon"] is not None:
                    g["lon"], g["lat"] = r["lon"], r["lat"]
        except FileNotFoundError:
            pass
        for g in gares.values():
            cur.execute(
                """INSERT INTO distancier.gare (uic, nom, pays, uopid_rinf, geom, note)
                   VALUES (%s, %s, %s, %s, CASE WHEN %s::float IS NULL THEN NULL
                           ELSE ST_SetSRID(ST_MakePoint(%s, %s), 4326) END, %s)
                   ON CONFLICT (uic) DO UPDATE SET nom = EXCLUDED.nom, pays = EXCLUDED.pays,
                     uopid_rinf = EXCLUDED.uopid_rinf, geom = COALESCE(EXCLUDED.geom, distancier.gare.geom),
                     note = EXCLUDED.note""",
                (g["uic"], g["nom"], g["pays"], g["uopid_rinf"] or None, g.get("lon"), g.get("lon"), g.get("lat"),
                 g["note"] or None))
        cur.execute("INSERT INTO distancier.calcul (fichier, meta) VALUES (%s, %s) RETURNING id",
                    (fichier_csv.name, json.dumps(meta)))
        calcul_id = cur.fetchone()[0]
        for r in lignes:
            cur.execute(f"INSERT INTO distancier.relation (calcul_id, id, uic_origine, uic_destination, "
                        f"{', '.join(COLS[3:])}) VALUES ({', '.join(['%s'] * (len(COLS) + 1))})",
                        [calcul_id] + [_val(c, r.get(c, "")) for c in COLS])
            for c in vitesses:
                km = float(r[c] or 0)
                if km > 0:
                    v = c.removeprefix("dont_km_")
                    cur.execute("INSERT INTO distancier.relation_vitesse VALUES (%s, %s, %s, %s)",
                                (calcul_id, r["id"], None if v == "vitesse_inconnue" else int(v), km))
    return calcul_id
