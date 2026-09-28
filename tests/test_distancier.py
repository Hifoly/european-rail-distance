import csv

import pytest
from pyproj import Geod

from distancier import calcul, export
from distancier.reseau import parse_pk
from tests.conftest import M, P, Q, S, X

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
