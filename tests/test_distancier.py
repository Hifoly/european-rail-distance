import csv
from pathlib import Path

import pytest
from pyproj import Geod

from distancier import calcul, export
from distancier.reseau import parse_pk
from tests.conftest import M, P, Q, S, X, _csv

GEOD = Geod(ellps="GRS80")


def km(*pts):
    return sum(GEOD.inv(*a, *b)[2] for a, b in zip(pts[:-1], pts[1:])) / 1000


@pytest.mark.parametrize("texte,attendu", [("123+456", 123.456), ("000+000", 0.0), ("012-200", 11.8),
                                           ("", None), (None, None), ("abc", None)])
def test_parse_pk(texte, attendu):
    assert parse_pk(texte) == (pytest.approx(attendu) if attendu is not None else None)


def test_arrondir_somme_conserve_le_total():
    r = export.arrondir_somme({300: 263.46, 270: 133.04, None: 33.04}, 429.54)
    assert round(sum(r.values()), 1) == 429.5


@pytest.fixture
def resultat(cfg):
    return calcul.calculer(cfg)


def par_id(resultat):
    return {int(r["id"]): r for r in resultat["relations"]}


def test_itineraires(resultat):
    r = par_id(resultat)
    assert r[1]["moteur"] == "sncf"
    assert r[1]["resultat"].km == pytest.approx(km(P, M, Q), rel=1e-3)   # LGV privilégiée
    assert r[2]["resultat"].km == pytest.approx(km(P, X, Q), rel=1e-3)   # plus court
    assert r[1]["distance_sans_lgv_km"] == pytest.approx(km(P, X, Q), abs=0.1)
    assert r[3]["resultat"].km == pytest.approx(km(P, X, S), rel=1e-3)   # bifurcation en T
    assert "itinéraire LGV privilégié" in " ".join(r[1]["remarques"])


def test_repartition_par_vitesse(resultat):
    r = par_id(resultat)
    assert r[1]["resultat"].vitesses == {300: pytest.approx(km(P, M, Q), rel=1e-3)}
    v3 = r[3]["resultat"].vitesses
    assert v3[160] == pytest.approx(km(P, X), rel=0.01)
    assert v3[None] == pytest.approx(km(X, S), rel=0.01)
    assert resultat["vitesses"] == [300, 160]


def test_controles_et_statuts(resultat):
    r = par_id(resultat)
    assert r[2]["source_controle"] == "rinf"
    assert r[2]["statut"] == "vérifié (RINF)"
    assert r[2]["controle_pk_km"] == pytest.approx(74.4)
    assert r[1]["statut"] == "estimé"          # RINF n'a pas la LGV : écart > 1 %
    assert r[4]["statut"].startswith("erreur")


def test_repli_rinf_gare_hors_reseau_sncf(resultat):
    r = par_id(resultat)[5]
    assert r["moteur"] == "rinf"
    assert r["resultat"].km == pytest.approx(37.25 + 55.6, rel=1e-3)
    assert r["statut"] == "à vérifier"
    assert "distance_controle_km" not in r
    assert "calcul SNCF impossible" in r["remarques"][0]


def test_export(cfg, resultat, tmp_path):
    fichiers = export.ecrire(resultat, cfg, tmp_path / "sortie", "test")
    principal = fichiers[0]
    with open(principal, encoding="utf-8-sig") as f:
        lignes = list(csv.DictReader(f, delimiter=";"))
    entete = list(lignes[0])
    assert entete[8:12] == ["distance_km", "dont_km_300", "dont_km_160", "dont_km_vitesse_inconnue"]
    for l in lignes[:3]:
        somme = sum(float(l[c]) for c in ("dont_km_300", "dont_km_160", "dont_km_vitesse_inconnue"))
        assert somme == pytest.approx(float(l["distance_km"]), abs=1e-6)
        assert l["date_consultation"] == "2026-01-01"
    assert lignes[0]["type_ligne"] == "LGV" and lignes[2]["type_ligne"] == "classique"
    assert any(f.suffix == ".xlsx" for f in fichiers)


def test_cache_du_graphe(cfg):
    calcul.calculer(cfg)
    assert list((cfg["_racine"] / cfg["chemins"]["intermediaire"]).glob("graphe_sncf_2026-01-01_*.pkl"))


def test_detour_sncf_excessif_bascule_sur_rinf(cfg):
    cfg["routage"]["seuil_alerte_pct"] = 3.0   # LGV (SNCF, ~4 % plus longue) contre ligne classique (RINF)
    r = par_id(calcul.calculer(cfg))[1]
    assert r["moteur"] == "rinf"
    assert r["resultat"].km == pytest.approx(km(P, X, Q), rel=1e-3)
    assert r["statut"] == "à vérifier"
    assert "distance_controle_km" not in r
    assert "détour SNCF" in r["remarques"][0]


def test_raccordement_manuel_en_pleine_ligne():
    import networkx as nx
    from shapely.geometry import LineString
    from distancier.reseau import Raccordement, Troncon, construire_graphe

    a = Troncon("A", LineString([(2.0, 48.0), (2.5, 48.0), (3.0, 48.0)]))
    b = Troncon("B", LineString([(2.5, 48.01), (2.5, 48.5)]))   # s'arrête à ~1,1 km de A
    assert nx.number_connected_components(construire_graphe([a, b], [])) == 2
    r = Raccordement("test", (2.5, 48.01), (2.5, 48.0))
    G = construire_graphe([a, b], [], raccordements=[r])
    assert nx.number_connected_components(G) == 1
    assert sum(1 for *_, d in G.edges(data=True) if d["ligne"] == "A") == 2   # A coupée au raccord


@pytest.mark.parametrize("valide,statut", [(None, "à vérifier"), ("OpenRailwayMap, 2026-01-03", "estimé")])
def test_raccordement_valide_ne_force_plus_a_verifier(cfg, valide, statut):
    import yaml
    racc = {"nom": "raccourci P-S", "source": "sncf", "de": list(P), "a": list(S)}
    if valide:
        racc["valide"] = valide
    (cfg["_racine"] / cfg["chemins"]["corrections"]).write_text(
        yaml.safe_dump({"raccordements": [racc], "lignes_exclues": []}, allow_unicode=True), encoding="utf-8")
    cfg["routage"]["seuil_alerte_pct"] = 1000.0   # garder SNCF malgré l'écart avec RINF
    r = par_id(calcul.calculer(cfg))[3]
    assert r["resultat"].manuels == ["raccourci P-S"]
    assert r["statut"] == statut
    assert any("raccourci P-S" in x for x in r["remarques"])


def test_chemin_suivant_les_lignes_d_un_autre_itineraire():
    import networkx as nx
    from distancier.routage import Routeur

    G = nx.MultiGraph()
    for u, v, km, ligne in (("A", "B", 10.0, "L1-1"), ("A", "C", 6.0, "L2-1"), ("C", "B", 6.0, "L2-2")):
        G.add_edge(u, v, km=km, ligne=ligne, profil=[160], sens=(u, v), manuel=None)
    r = Routeur(G)
    assert r.chemin("A", "B").km == pytest.approx(10.0)                        # plus court : L1
    assert r.chemin("A", "B", lignes=["L2"]).km == pytest.approx(12.0)         # suit L2 (code SNCF sans voie)
    assert [l for l, _ in r.chemin("A", "B", lignes=["L2"]).lignes] == ["L2-1", "L2-2"]


def test_concordance_en_pourcentage_ou_en_km():
    from distancier.calcul import _concorde
    rt = {"seuil_verification_pct": 1.0, "seuil_verification_km": 0.5}
    assert _concorde(10.7, 10.3, 100 * (10.7 / 10.3 - 1), rt)        # 3,9 % mais 0,4 km
    assert not _concorde(10.7, 9.9, 100 * (10.7 / 9.9 - 1), rt)      # 0,8 km
    assert _concorde(300.0, 302.0, 100 * (300 / 302 - 1), rt)        # 0,7 %


def test_plafond_du_detour_lgv(cfg):
    cfg["routage"]["plafond_detour_lgv_pct"] = 3.0   # LGV ~4 % plus longue que la ligne classique
    r = par_id(calcul.calculer(cfg))[1]
    assert r["moteur"] == "sncf"
    assert r["resultat"].km == pytest.approx(km(P, X, Q), rel=1e-3)
    assert any("plus court chemin retenu" in x for x in r["remarques"])


def test_virage_interdit_supprime_le_demi_tour():
    import networkx as nx
    from shapely.geometry import LineString
    from distancier.reseau import Troncon, VirageInterdit, construire_graphe
    from distancier.routage import Routeur

    # Ligne classique C nord-sud ; raccord R qui arrive du nord-est et rejoint C en J (48,0) en direction du sud.
    c = Troncon("C", LineString([(2.0, 48.1), (2.0, 48.0), (2.0, 47.9)]))
    r = Troncon("R", LineString([(2.0, 48.0), (2.01, 48.02), (2.1, 48.1)]))
    G = construire_graphe([c, r], [])
    rt = Routeur(G)
    rt.rattacher("E", 2.1, 48.1)        # bout du raccord
    rt.rattacher("N", 2.0, 48.09)       # sur C, au nord de J
    rt.rattacher("S", 2.0, 47.91)       # sur C, au sud de J
    avant = {d: rt.chemin("E", d).km for d in "NS"}
    rt.detacher("E", "N", "S")

    vi = VirageInterdit("test", (2.0, 48.0), "R", "C")
    G = construire_graphe([c, r], [], virages_interdits=[vi])
    rt = Routeur(G)
    for tag, lon, lat in (("E", 2.1, 48.1), ("N", 2.0, 48.09), ("S", 2.0, 47.91)):
        rt.rattacher(tag, lon, lat)
    assert rt.chemin("E", "S").km == pytest.approx(avant["S"])     # vers le sud : permis
    # vers le nord : plus de demi-tour en J, il ne reste que le rebroussement au bout sud de C
    assert rt.chemin("E", "N").km > avant["N"] + 2 * 9.0
    assert rt.chemin("N", "S").km == pytest.approx(20.0, abs=0.1)                # C reste continue

    G = construire_graphe([c, r], [], virages_interdits=[VirageInterdit("t", (2.0, 48.0), "R", "C", separer=True)])
    assert nx.number_connected_components(G) == 2


def test_pk_sncf_priment_sur_l_alerte_rinf(cfg):
    from pathlib import Path
    sections = Path(cfg["chemins"]["donnees_brutes"]) / "rinf" / "2026-01-02" / "sections_FR.csv"
    texte = sections.read_text(encoding="utf-8")
    sections.write_text(texte.replace(";37250;", ";50000;"), encoding="utf-8")   # RINF P-Q : 100 km
    r = par_id(calcul.calculer(cfg))[2]
    assert r["ecart_controle_pct"] < -10                     # alerte RINF...
    assert r["controle_pk_km"] == pytest.approx(74.4)        # ...mais les PK confirment SNCF
    assert r["statut"] == "vérifié (PK SNCF)"
    assert any("PK SNCF" in x and "RINF" in x for x in r["remarques"])


def _gtfs(cfg, trains):
    """Horaires simulés au format GTFS SNCF : {trip_id: (produit, [UIC…])}."""
    import json
    import zipfile
    from pathlib import Path
    dossier = Path(cfg["chemins"]["donnees_brutes"]) / "gtfs_sncf" / "2026-01-03"
    dossier.mkdir(parents=True)
    lignes = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    for trip, (produit, arrets, *heures) in trains.items():
        heures = heures[0] if heures else [f"{8 + i // 2:02d}:{30 * (i % 2):02d}:00" for i in range(len(arrets))]
        lignes += [f"{trip},{h},{h},StopPoint:OCE{produit}-{u},{i}" for i, (u, h) in enumerate(zip(arrets, heures))]
    with zipfile.ZipFile(dossier / "gtfs.zip", "w") as z:
        z.writestr("stop_times.txt", "\n".join(lignes) + "\n")
    (dossier / "manifest.json").write_text(json.dumps({"source": "Horaires SNCF", "url": "https://exemple/gtfs.zip",
                                                       "fichier": "gtfs.zip",
                                                       "date_consultation": "2026-01-03T10:00:00+00:00"}))


def test_tgv_commercial_suit_les_arrets_du_tgv_le_plus_frequent(cfg):
    p, q, s = "87000001", "87000002", "87000003"
    _gtfs(cfg, {"T1": ("TGV INOUI", [p, s, q]), "T2": ("TGV INOUI", [q, s, p]),   # le plus fréquent, dans les deux sens
                "T3": ("OUIGO", [p, q]),
                "R1": ("TER", [p, q]), "R2": ("TER", [p, q]), "R3": ("TER", [p, q])})  # pas des TGV
    res = calcul.calculer(cfg)
    r = par_id(res)[1]
    detour = km(P, X, S) + km(S, X, Q)
    # distance_km = distance du TGV, la distance par le rail reste dans distance_au_plus_court_km
    assert r["resultat"].km == pytest.approx(detour, rel=1e-3)
    assert r["distance_tgv_commercial_km"] == pytest.approx(detour, abs=0.1)
    assert r["distance_au_plus_court_km"] == pytest.approx(km(P, M, Q), abs=0.1)
    assert r["itineraire_retenu"] == "tgv_commercial"
    assert r["desserte_tgv"] == "Pville > Sville > Qville (2 trains)"
    # vitesses, lignes et contrôles suivent les tronçons P-S et S-Q
    assert r["resultat"].vitesses[160] == pytest.approx(km(P, X) + km(X, Q), rel=0.01)
    assert r["resultat"].vitesses[None] == pytest.approx(2 * km(X, S), rel=0.01)
    assert [l for l, _ in r["resultat"].lignes] == ["C1", "C3", "C1"]
    assert r["source_controle"] == "rinf"
    assert r["distance_controle_km"] == pytest.approx(37.25 + 2 * 55.6 + 37.25, abs=0.2)
    assert r["statut"] == "vérifié (RINF)"
    assert "le TGV fait un détour" in " ".join(r["remarques"])
    assert "desserte TGV : Horaires SNCF" in r["source"] and "2026-01-03" in r["source"]
    assert res["sources"]["gtfs_sncf"]["date_consultation"] == "2026-01-03"

    r3 = par_id(res)[3]                       # P-S en plus court : le TGV direct fait le même trajet
    assert r3["resultat"].km == pytest.approx(km(P, X, S), rel=1e-3)
    assert r3["distance_au_plus_court_km"] == pytest.approx(km(P, X, S), abs=0.1)

    with open(export.ecrire(res, cfg, Path(cfg["chemins"]["sorties"]), "test")[0], encoding="utf-8-sig") as f:
        l = {x["id"]: x for x in csv.DictReader(f, delimiter=";")}["1"]
    assert float(l["distance_km"]) == pytest.approx(detour, abs=0.1)
    somme = sum(float(l[c]) for c in ("dont_km_300", "dont_km_160", "dont_km_vitesse_inconnue"))
    assert somme == pytest.approx(float(l["distance_km"]), abs=1e-6)
    assert l["type_ligne"] == "classique"


def test_tgv_commercial_sans_tgv_direct_ni_horaires(cfg):
    r = par_id(calcul.calculer(cfg))
    assert "distance_tgv_commercial_km" not in r[1] and "desserte_tgv" not in r[1]   # horaires non téléchargés
    assert r[1]["distance_au_plus_court_km"] == pytest.approx(r[1]["resultat"].km, abs=0.05)
    _gtfs(cfg, {"T1": ("TGV INOUI", ["87000001", "87000003"])})
    r = par_id(calcul.calculer(cfg))
    assert r[1]["desserte_tgv"] == "aucun TGV direct"
    assert "distance_tgv_commercial_km" not in r[1]
    assert r[1]["itineraire_retenu"] == "grande_vitesse"


def test_tgv_commercial_meme_gare_au_depart_et_a_l_arrivee(cfg):
    from distancier.sources.gtfs import Dessertes
    assert Dessertes([("87000001", "87000002")]).desserte("87000001", "87000001") is None
    rel = Path(cfg["chemins"]["relations"])
    rel.write_text(rel.read_text(encoding="utf-8") + "6;87000001;87000001;grande_vitesse;;\n", encoding="utf-8")
    _gtfs(cfg, {"T1": ("TGV INOUI", ["87000001", "87000002"])})
    r = par_id(calcul.calculer(cfg))[6]
    assert r["resultat"].km == 0 and "desserte_tgv" not in r


@pytest.mark.parametrize("statuts,attendu", [
    (["vérifié (RINF)", "vérifié (RINF)"], "vérifié (RINF)"),
    (["vérifié (RINF)", "vérifié (PK SNCF)"], "vérifié (PK SNCF et RINF)"),
    (["vérifié (RINF)", "estimé"], "estimé"),
    (["estimé", "à vérifier", "vérifié (RINF)"], "à vérifier"),
])
def test_statut_d_une_distance_tgv_par_ses_troncons(statuts, attendu):
    assert calcul._statut_troncons([{"statut": s} for s in statuts]) == attendu


def test_distance_tgv_verifiee_par_le_controle_de_tout_le_trajet(cfg):
    from distancier.routage import Resultat
    from distancier.sources.gtfs import Dessertes
    troncons = {("A", "B"): {"resultat": Resultat(km=100.0, lignes=[("L1", 100.0)], vitesses={300: 100.0}),
                             "moteur": "sncf", "statut": "estimé", "source": "S", "date_consultation": "2026-01-01",
                             "gare_origine": "A", "gare_destination": "B", "remarques": [],
                             "source_controle": "rinf", "distance_controle_km": 98.0},
                ("B", "C"): {"resultat": Resultat(km=50.0, lignes=[("L2", 50.0)], vitesses={160: 50.0}),
                             "moteur": "sncf", "statut": "vérifié (RINF)", "source": "S", "date_consultation": "2026-01-01",
                             "gare_origine": "B", "gare_destination": "C", "remarques": [],
                             "source_controle": "rinf", "distance_controle_km": 51.0}}

    class Tgv:
        dessertes, source, date = Dessertes([("A", "B", "C")]), "H", "2026-01-03"
        troncon = staticmethod(lambda a, b, _: troncons[(a, b)])
        nom = staticmethod(lambda u: u)

    ligne = {"code_uic_origine": "A", "code_uic_destination": "C", "remarques": [], "source": "S",
             "resultat": Resultat(km=120.0), "statut": "vérifié (RINF)"}
    calcul._tgv_commercial(ligne, {"itineraire": "grande_vitesse"}, Tgv, None, cfg["routage"])
    assert ligne["resultat"].km == 150.0 and ligne["distance_controle_km"] == 149.0
    assert ligne["statut"] == "vérifié (RINF)"          # un tronçon estimé, mais le trajet entier concorde
    troncons[("A", "B")]["distance_controle_km"] = 80.0
    ligne.update(resultat=Resultat(km=120.0), remarques=[])
    calcul._tgv_commercial(ligne, {"itineraire": "grande_vitesse"}, Tgv, None, cfg["routage"])
    assert ligne["statut"] == "estimé"


def test_temps_theorique_et_pratique(cfg):
    p, q, s = "87000001", "87000002", "87000003"
    _gtfs(cfg, {"T1": ("TGV INOUI", [p, q], ["08:00:00", "08:20:00"]),
                "T2": ("TGV INOUI", [q, p], ["23:50:00", "24:16:00"]),   # après minuit
                "T3": ("TGV INOUI", [p, q], ["10:00:00", "10:24:00"]),
                "T4": ("TGV INOUI", [s, p], ["07:00:00", "08:00:00"])})
    r = par_id(calcul.calculer(cfg))
    assert r[1]["temps_pratique"] == 24                                    # médiane de 20, 24 et 26 min
    assert r[1]["temps_theorique"] == pytest.approx(60 * km(P, M, Q) / 300, abs=0.1)   # tout à 300 km/h
    # P-S : 37 km à 160 km/h, 55 km sans vitesse connue comptés à la médiane (160 km/h)
    assert r[3]["temps_theorique"] == pytest.approx(60 * km(P, X, S) / 160, abs=0.2)
    assert "sans vitesse connue comptés à 160 km/h" in " ".join(r[3]["remarques"])
    assert r[3]["temps_pratique"] == 60


def test_vitesse_mediane_ponderee_par_les_km():
    assert calcul._vitesse_mediane({300: 100.0, 160: 30.0, 80: 10.0}) == 300
    assert calcul._vitesse_mediane({300: 40.0, 160: 50.0, 80: 20.0}) == 160


def test_temps_score_mediane_ponderee_de_l_annee_dans_les_deux_sens(cfg):
    from openpyxl import Workbook
    from distancier.sources.score import mediane_ponderee
    assert mediane_ponderee([(100, 1), (120, 1), (200, 5)]) == 200
    assert mediane_ponderee([(100, 3), (120, 1), (200, 1)]) == 100

    _csv(Path(cfg["sources"]["score"]["couples"]), [
        {"arret_montee_iata": "FRPPP", "arret_montee_uic": "87000001", "arret_descente_iata": "FRQQQ", "arret_descente_uic": "87000002"}])
    wb = Workbook()
    ws = wb.active
    ws.append(["train_uid", "annee", "periode", "arret_montee_iata", "arret_descente_iata",
               "duree_commerciale_minutes", "nombre_jour", "compteur"])
    for ligne in ([1, 2025, "A", "FRPPP", "FRQQQ", 30, 10, 1],
                  [2, 2025, "B", "FRQQQ", "FRPPP", 34, 12, 1],      # autre sens, compté avec
                  [3, 2025, "B", "FRPPP", "FRQQQ", 60, 12, 0.5],    # tranche d'un train couplé
                  [4, 2024, "A", "FRPPP", "FRQQQ", 10, 100, 1]):    # autre année : ignorée
        ws.append(ligne)
    wb.save(cfg["sources"]["score"]["fichier"])
    res = calcul.calculer(cfg)
    r = par_id(res)
    assert r[1]["temps_score"] == 34          # poids 10 (30 min), 12 (34 min), 6 (60 min)
    assert r[2]["temps_score"] == 34
    assert "temps_score" not in r[3]          # OD absente du plan de transport
    assert res["sources"]["score"]["description"].startswith("Plan de transport TGV théorique")
    assert list(Path(cfg["chemins"]["intermediaire"]).glob("score_2025_*.pkl"))   # cache


def test_temps_pratique_sur_tous_les_tgv_directs():
    from distancier.sources.gtfs import Dessertes
    trajets = [("A", "B"), ("A", "B"), ("A", "X", "B")]
    horaires = [((0, 0), (600, 600)), ((0, 0), (720, 720)), ((0, 0), (900, 900), (1800, 1800))]
    arrets, n, duree = Dessertes(trajets, horaires).desserte("A", "B")
    assert arrets == ("A", "B") and n == 2
    assert duree == 12                     # médiane de 10, 12 et 30 min, desserte par X comprise
