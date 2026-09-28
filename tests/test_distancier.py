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
