"""Collecte par API, avec un faux serveur (aucun accès réseau)."""
import json

import pytest

from distancier import config
from distancier.sources import http, rinf, sncf


class Reponse:
    def __init__(self, donnees=None, contenu=b""):
        self.donnees, self.contenu = donnees, contenu

    def raise_for_status(self):
        pass

    def json(self):
        return self.donnees

    def iter_content(self, _):
        yield self.contenu

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FausseSession:
    def __init__(self):
        self.appels = []
        self.headers = {}

    def get(self, url, params=None, stream=False, timeout=None):
        self.appels.append(url)
        if "/exports/" in url:
            return Reponse(contenu=b"{}")
        return Reponse({"metas": {"default": {"modified": "2026-09-01T00:00:00Z", "license": "ODbL"}}})

    def post(self, url, data=None, timeout=None, headers=None):
        q = data["query"]
        offset = int(q.rsplit("OFFSET", 1)[1])
        n = 2 if offset == 0 else 1          # taille de page 2 : deux pages
        if "SectionOfLine" in q:
            rows = [{"sol": {"value": f"s{offset + i}"}, "longueur": {"value": "1000"}} for i in range(n)]
        else:
            rows = [{"uopid": {"value": f"FR{offset + i}"}} for i in range(n)]
        return Reponse({"results": {"bindings": rows}})


def test_telechargement_sncf(cfg, monkeypatch):
    fausse = FausseSession()
    monkeypatch.setattr(http, "session", lambda: fausse)
    dossier = sncf.telecharger(cfg, jour="2026-09-28")
    man = http.lire_manifeste(dossier)
    assert set(man["jeux"]) == {"lignes", "vitesses", "gares", "voies"}
    assert man["jeux"]["gares"]["url"].endswith("/catalog/datasets/liste-des-gares/exports/csv")
    assert man["jeux"]["lignes"]["modifie_a_la_source"] == "2026-09-01T00:00:00Z"
    assert config.dossier_source(cfg, "sncf") == dossier   # le plus récent


def test_telechargement_rinf_pagine(cfg, monkeypatch):
    fausse = FausseSession()
    monkeypatch.setattr(http, "session", lambda: fausse)
    cfg["sources"]["rinf"]["taille_page"] = 2
    dossier = rinf.telecharger(cfg, ["BE"], jour="2026-09-28")
    man = json.loads((dossier / "manifest.json").read_text())
    assert man["pays"]["BE"]["sections"] == 3 and man["pays"]["BE"]["points"] == 3
    assert (dossier / "sections_BE.csv").read_text().count("\n") == 4


def test_rinf_section_plus_courte_que_la_ligne_droite(cfg):
    import csv
    import pytest
    from pyproj import Geod
    from distancier import config
    from distancier.sources import rinf
    from tests.conftest import S, X

    chemin = config.dossier_source(cfg, "rinf", None) / "sections_FR.csv"
    lignes = list(csv.DictReader(open(chemin, encoding="utf-8"), delimiter=";"))
    lignes[2]["longueur"] = "0"                      # s3 (X -> S) déclarée à 0 m
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lignes[0]), delimiter=";")
        w.writeheader()
        w.writerows(lignes)
    G = rinf.charger(cfg)["graphe"]
    droite = Geod(ellps="GRS80").inv(*X, *S)[2] / 1000
    assert min(d["km"] for d in G["FRX"]["FRS"].values()) == pytest.approx(droite)


def test_rinf_ecartement_exclu_et_lgv_presumee(cfg):
    import csv
    chemin = config.dossier_source(cfg, "rinf", None) / "sections_FR.csv"
    lignes = list(csv.DictReader(open(chemin, encoding="utf-8"), delimiter=";"))
    lignes[2]["ecartement"] = "1000"                 # s3 (X -> S) à voie métrique
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lignes[0]), delimiter=";")
        w.writeheader()
        w.writerows(lignes)
    G = rinf.charger(cfg)["graphe"]
    assert not G.has_edge("FRX", "FRS")
    assert not any(d["lgv_presumee"] for *_, d in G.edges(data=True))   # vitesses connues, pays FR
    cfg["sources"]["rinf"]["ecartements_exclus"] = []
    cfg["sources"]["rinf"]["lgv_presumee_ecartement"] = {"FR": "1000"}
    G = rinf.charger(cfg)["graphe"]
    assert [d["lgv_presumee"] for d in G["FRX"]["FRS"].values()] == [True]   # s3 sans vitesse


def test_lignes_complementaires_depuis_le_fichier_des_voies(cfg):
    """Une ligne absente des tracés de lignes est reprise du fichier des voies (voie la plus longue)."""
    dossier = config.dossier_source(cfg, "sncf")
    voie = lambda code, nom, coords: {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                                      "properties": {"code_ligne": code, "nom_voie": nom,
                                                     "pk_debut_r": "000+000", "pk_fin_r": "010+000"}}
    (dossier / "voies.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        voie("C9", "V1", [[3.0, 48.0], [3.1, 48.0]]),
        voie("C9", "J1", [[3.0, 48.0], [3.01, 48.0]]),          # aiguille courte : ignorée
        voie("C1", "V1", [[2.0, 48.0], [3.0, 48.0]]),            # déjà dans les tracés de lignes
    ]}), encoding="utf-8")
    man = json.loads((dossier / "manifest.json").read_text())
    man["jeux"]["voies"] = {"id": "voies", "fichier": "voies.geojson", "date_consultation": "2026-01-01T10:00:00+00:00"}
    (dossier / "manifest.json").write_text(json.dumps(man))

    sans = sncf.charger(cfg)["troncons"]
    avec = sncf.charger(cfg, complementaires={"C9", "C1"})["troncons"]
    ajout = [t for t in avec if t.ligne == "C9"]
    assert len(avec) == len(sans) + 1 and len(ajout) == 1
    assert ajout[0].geom.length > 0.05 and ajout[0].pk_fin == 10.0


@pytest.mark.parametrize("valeur, attendu", [("1668", "1668"), ("1435+1668", "mixte"), ("1435+1435", "1435"),
                                             ("", None), (None, None)])
def test_ecartement_rinf(valeur, attendu):
    assert rinf.ecartement(valeur) == attendu


def test_perimetre_renfe(cfg, tmp_path):
    """Couples montée/descente d'un GTFS Renfe, produits filtrés, arrêts étrangers exclus."""
    import zipfile
    from distancier import perimetre
    fichiers = {
        "routes.txt": "route_id,agency_id,route_short_name,route_type\nr1,1,AVE,2\nr2,1,MD,2\n",
        "trips.txt": "route_id,service_id,trip_id\nr1,s,t1\nr2,s,t2\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n60000,Madrid,40.4,-3.7\n04040,Zaragoza,41.6,-0.9\n"
                     "71801,Barcelona,41.4,2.1\n87374,Perpignan,42.7,2.9\n11111,Pueblo,41.0,-1.0\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
                          "t1,8:00:00,8:00:00,60000,1,0,1\nt1,9:00:00,9:05:00,04040,2,0,0\n"
                          "t1,10:00:00,10:05:00,71801,3,0,0\nt1,11:00:00,11:00:00,87374,4,1,0\n"
                          "t2,8:00:00,8:00:00,60000,1,0,1\nt2,9:00:00,9:00:00,11111,2,1,0\n",
    }
    dossier = config.dossier_source(cfg, "renfe", "2026-09-29")
    dossier.mkdir(parents=True)
    with zipfile.ZipFile(dossier / "google_transit.zip", "w") as z:
        for nom, texte in fichiers.items():
            z.writestr(nom, texte)
    (dossier / "manifest.json").write_text("{}")
    r = perimetre.espagne(cfg)
    assert (r["gares"], r["relations"]) == (3, 3)   # MD et Perpignan exclus
    rel = [l for l in perimetre.lire_csv(config.chemin(cfg, "relations")) if int(l["id"]) >= 10001]
    assert [(l["uic_origine"], l["uic_destination"]) for l in rel] == [
        ("7104040", "7160000"), ("7104040", "7171801"), ("7160000", "7171801")]
    gares = {g["uic"]: g for g in perimetre.lire_csv(config.chemin(cfg, "gares"))}
    assert gares["7104040"]["nom"] == "Zaragoza" and gares["7104040"]["pays"] == "ES"
    assert gares["87000001"]["pays"] == "FR"                         # gares existantes conservées
    assert perimetre.espagne(cfg)["relations"] == 3                   # reconstruction idempotente
    assert len(perimetre.lire_csv(config.chemin(cfg, "relations"))) == 5 + 3
