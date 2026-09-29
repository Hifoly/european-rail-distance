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


# --- contrôle Adif (Espagne) -----------------------------------------------------------

def _cfg_adif(tmp_path):
    from distancier.config import charger
    cfg = charger()
    cfg["_racine"] = tmp_path
    (tmp_path / "troncons.csv").write_text(
        "de;a;km;calque;note\nMadrid;Bif;100;AV;\nBif;Sevilla;371;AV;\nBif;Cordoba;50;Adif;\n", encoding="utf-8")
    (tmp_path / "noeuds.csv").write_text(
        "nom;uopid_rinf;uic;lat;lon\nMadrid;ES60000;;40.40;-3.69\nSevilla;ES51003;;37.39;-5.97\n"
        "Cordoba;;7150100;37.89;-4.79\nBif;;;;\n", encoding="utf-8")
    cfg["sources"]["adif"] = {"troncons": "troncons.csv", "noeuds": "noeuds.csv", "marge_arrondi_km": 0.5}
    cfg["controles"] = {"ES": "adif"}
    return cfg


class _RinfFactice:
    nom, source, date, complementaires = "rinf", "RINF test", "2026-09-29", set()

    def __init__(self, km):
        self.km = km

    def calculer(self, go, gd, mode, lignes=None):
        import networkx as nx
        from distancier.routage import Resultat
        if self.km is None:
            raise nx.NetworkXNoPath("pas de chemin")
        return Resultat(km=self.km, lignes=[("L", self.km)], vitesses={None: self.km}, aretes=3), []


GARES_ES = {
    "7160000": {"uic": "7160000", "nom": "Madrid", "pays": "ES", "uopid_rinf": "ES60000", "note": ""},
    "7151003": {"uic": "7151003", "nom": "Sevilla", "pays": "ES", "uopid_rinf": "ES51003", "note": ""},
    "7150100": {"uic": "7150100", "nom": "Cordoba", "pays": "ES", "uopid_rinf": "", "note": ""},
}


@pytest.mark.parametrize("km_rinf,moteur,statut", [
    (470.0, "rinf", "vérifié (ADIF)"),      # 0,2 % d'écart
    (465.0, "rinf", "estimé"),             # 1,3 % : hors seuil, sous l'alerte
    (560.0, "adif", "à vérifier"),         # détour RINF > 10 % : distance Adif
    (None, "adif", "à vérifier"),          # pas de chemin RINF : distance Adif
])
def test_controle_et_repli_adif(tmp_path, km_rinf, moteur, statut):
    from distancier import calcul
    cfg = _cfg_adif(tmp_path)
    moteurs = {"rinf": _RinfFactice(km_rinf), "adif": calcul.MoteurAdif(cfg, None, {})}
    rel = {"id": "1", "uic_origine": "7160000", "uic_destination": "7151003", "itineraire": "grande_vitesse"}
    ligne = calcul._relation(rel, GARES_ES, moteurs, cfg, cfg["routage"])
    assert ligne["moteur"] == moteur and ligne["statut"] == statut
    if moteur == "adif":
        assert ligne["resultat"].km == 471 and "sans contrôle RINF" in ligne["remarques"][0]
    else:
        assert ligne["distance_controle_km"] == 471 and ligne["source_controle"] == "adif"


def test_adif_gare_par_uic_et_marge_arrondi(tmp_path):
    from distancier import calcul
    cfg = _cfg_adif(tmp_path)
    moteurs = {"rinf": _RinfFactice(148.6), "adif": calcul.MoteurAdif(cfg, None, {})}
    rel = {"id": "2", "uic_origine": "7160000", "uic_destination": "7150100", "itineraire": "grande_vitesse"}
    ligne = calcul._relation(rel, GARES_ES, moteurs, cfg, cfg["routage"])
    # 150 km sur 2 tronçons : 1,4 km d'écart < 0,5 + 2 × 0,5 km d'arrondi
    assert ligne["distance_controle_km"] == 150 and ligne["statut"] == "vérifié (ADIF)"


def test_adif_gare_absente_placee_sur_la_carte(tmp_path):
    """Gare hors carte : placée sur le tronçon voisin, contrôle « approché » qui ne vérifie jamais."""
    from distancier import calcul
    cfg = _cfg_adif(tmp_path)
    (tmp_path / "troncons.csv").write_text(
        "de;a;km;calque;note\nMadrid;Sevilla;471;AV;\nMadrid;Cordoba;400;Adif;\n", encoding="utf-8")
    gares = {**GARES_ES, "7160911": {"uic": "7160911", "nom": "Ciudad Real AV", "pays": "ES", "uopid_rinf": "ES37200",
                                     "note": "", "lat": 38.99, "lon": -3.92}}
    adif = calcul.MoteurAdif(cfg, None, {})
    res, notes = adif.calculer(gares["7160000"], gares["7160911"], "grande_vitesse")
    assert res.approche and "placée entre Madrid et Sevilla" in notes[0] and 150 < res.km < 190
    moteurs = {"rinf": _RinfFactice(res.km), "adif": adif}
    rel = {"id": "3", "uic_origine": "7160000", "uic_destination": "7160911", "itineraire": "grande_vitesse"}
    ligne = calcul._relation(rel, gares, moteurs, cfg, cfg["routage"])
    assert ligne["statut"] == "estimé" and "contrôle ADIF approché" in " ".join(ligne["remarques"])
    assert "O" not in adif.routeur.G   # nœud temporaire retiré


def test_adif_deux_gares_sur_le_meme_troncon(tmp_path):
    from distancier import calcul
    cfg = _cfg_adif(tmp_path)
    (tmp_path / "troncons.csv").write_text("de;a;km;calque;note\nMadrid;Sevilla;471;Adif;\nSevilla;Cordoba;130;Adif;\n",
                                          encoding="utf-8")
    g1 = {"uic": "1", "nom": "Aranjuez", "pays": "ES", "uopid_rinf": "", "lat": 40.03, "lon": -3.60}
    g2 = {"uic": "2", "nom": "Alcázar", "pays": "ES", "uopid_rinf": "", "lat": 39.39, "lon": -3.21}
    adif = calcul.MoteurAdif(cfg, None, {})
    km = adif.calculer(g1, g2, "plus_court")[0].km
    assert 60 < km < 120   # et non Aranjuez -> Madrid -> Alcázar
