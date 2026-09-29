"""Jeu de données simulé, au format des exports SNCF Réseau et RINF.

Réseau (vers 48° N) :
    P (2,0 ; 48,0) ── C1, 160 km/h ── Q (3,0 ; 48,0)
      \\__ LGV1, 300 km/h, via M (2,5 ; 48,1) __/
    une antenne C3 (vitesse inconnue) part du milieu de C1 vers S (2,5 ; 47,5).
"""
import csv
import json
from pathlib import Path

import pytest

from distancier import config

RACINE = Path(__file__).resolve().parents[1]
P, Q, M, X, S = (2.0, 48.0), (3.0, 48.0), (2.5, 48.1), (2.5, 48.0), (2.5, 47.5)


def _geojson(chemin, features):
    chemin.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": p, "geometry": {"type": "LineString", "coordinates": c}}
        for p, c in features]}), encoding="utf-8")


def _csv(chemin, lignes, bom=False):
    with open(chemin, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lignes[0]), delimiter=";")
        w.writeheader()
        w.writerows(lignes)


@pytest.fixture
def cfg(tmp_path):
    c = config.charger(RACINE / "config" / "settings.yaml")
    c["_racine"] = RACINE
    for cle in ("donnees_brutes", "intermediaire", "sorties"):
        c["chemins"][cle] = str(tmp_path / cle)
    c["chemins"]["corrections"] = str(tmp_path / "corrections.yaml")

    sncf = tmp_path / "donnees_brutes" / "sncf" / "2026-01-01"
    sncf.mkdir(parents=True)
    _geojson(sncf / "lignes.geojson", [
        ({"code_ligne": "C1", "mnemo": "EXPLOITE", "pk_debut_r": "000+000", "pk_fin_r": "074+400"}, [P, X, Q]),
        ({"code_ligne": "LGV1", "mnemo": "EXPLOITE", "pk_debut_r": "000+000", "pk_fin_r": "077+700"}, [P, M, Q]),
        ({"code_ligne": "C3", "mnemo": "EXPLOITE", "pk_debut_r": "000+000", "pk_fin_r": "055+600"}, [X, S]),
        ({"code_ligne": "F9", "mnemo": "FERME", "pk_debut_r": "000+000", "pk_fin_r": "010+000"}, [P, S]),
    ])
    _geojson(sncf / "vitesses.geojson", [
        ({"code_ligne": "C1", "v_max": 160, "pkd": "000+000", "pkf": "074+400"}, [P, X, Q]),
        ({"code_ligne": "LGV1", "v_max": 300, "pkd": "000+000", "pkf": "077+700"}, [P, M, Q]),
    ])
    gare = lambda uic, nom, lig, pk, pt: {"code_uic": uic, "libelle": nom, "code_ligne": lig, "pk": pk,
                                          "x_wgs84": pt[0], "y_wgs84": pt[1]}
    _csv(sncf / "gares.csv", [gare("87000001", "Pville", "C1", "000+000", P),
                              gare("87000002", "Qville", "C1", "074+400", Q),
                              gare("87000003", "Sville", "C3", "055+600", S)], bom=True)
    (sncf / "manifest.json").write_text(json.dumps({"source": "SNCF Réseau open data", "api": "https://exemple", "jeux": {
        k: {"id": k, "fichier": f, "date_consultation": "2026-01-01T10:00:00+00:00"}
        for k, f in (("lignes", "lignes.geojson"), ("vitesses", "vitesses.geojson"), ("gares", "gares.csv"))}}))

    rinf = tmp_path / "donnees_brutes" / "rinf" / "2026-01-02"
    rinf.mkdir(parents=True)
    _csv(rinf / "points_FR.csv", [
        {"op": "o1", "uopid": "FRP", "nom": "Pville", "wkt": f"POINT({P[0]} {P[1]})", "lat": "", "lon": "", "type": "station"},
        {"op": "o2", "uopid": "FRQ", "nom": "Qville", "wkt": "", "lat": Q[1], "lon": Q[0], "type": "station"},
        {"op": "o3", "uopid": "FRX", "nom": "Bif X", "wkt": f"POINT({X[0]} {X[1]})", "lat": "", "lon": "", "type": "junction"},
        {"op": "o4", "uopid": "FRS", "nom": "Sville", "wkt": f"POINT({S[0]} {S[1]})", "lat": "", "lon": "", "type": "station"},
    ])
    _csv(rinf / "sections_FR.csv", [
        {"sol": "s1", "longueur": 37250, "ligne": "C1", "op_debut": "FRP", "op_fin": "FRX", "v_max": 160, "ecartement": "1435"},
        {"sol": "s2", "longueur": 37250, "ligne": "C1", "op_debut": "FRX", "op_fin": "FRQ", "v_max": 160, "ecartement": "1668+1435"},
        {"sol": "s3", "longueur": 55600, "ligne": "C3", "op_debut": "FRX", "op_fin": "FRS", "v_max": "", "ecartement": ""},
    ])
    (rinf / "manifest.json").write_text(json.dumps({"source": "ERA RINF", "api": "https://exemple/sparql",
                                                    "pays": {"FR": {"date_consultation": "2026-01-02T10:00:00+00:00"}}}))

    _csv(tmp_path / "gares.csv", [
        {"uic": "87000001", "nom": "Pville", "pays": "FR", "lat": "", "lon": "", "uopid_rinf": "FRP", "note": ""},
        {"uic": "87000002", "nom": "Qville", "pays": "FR", "lat": "", "lon": "", "uopid_rinf": "", "note": ""},
        {"uic": "87000003", "nom": "Sville", "pays": "FR", "lat": "", "lon": "", "uopid_rinf": "", "note": ""},
        # absente du jeu SNCF des gares, à 20 km de toute voie SNCF, mais point RINF connu
        {"uic": "87000004", "nom": "Tville", "pays": "FR", "lat": "47.8", "lon": "2.0", "uopid_rinf": "FRS", "note": ""},
    ])
    _csv(tmp_path / "relations.csv", [
        {"id": 1, "uic_origine": "87000001", "uic_destination": "87000002", "itineraire": "grande_vitesse", "statut_force": "", "remarque": ""},
        {"id": 2, "uic_origine": "87000001", "uic_destination": "87000002", "itineraire": "plus_court", "statut_force": "", "remarque": ""},
        {"id": 3, "uic_origine": "87000001", "uic_destination": "87000003", "itineraire": "plus_court", "statut_force": "", "remarque": ""},
        {"id": 4, "uic_origine": "87000001", "uic_destination": "99999999", "itineraire": "plus_court", "statut_force": "", "remarque": ""},
        {"id": 5, "uic_origine": "87000001", "uic_destination": "87000004", "itineraire": "plus_court", "statut_force": "", "remarque": ""},
    ])
    c["chemins"]["gares"] = str(tmp_path / "gares.csv")
    c["chemins"]["relations"] = str(tmp_path / "relations.csv")
    return c
