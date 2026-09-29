"""Registre RINF de l'ERA, interrogé en SPARQL (ERA Knowledge Graph).

Le graphe RINF est topologique : nœuds = points d'exploitation (uopid),
arêtes = sections de ligne avec leur longueur officielle et la vitesse maximale
de leurs voies. Il couvre les 7 pays et permet les relations transfrontalières.
"""
from __future__ import annotations

import csv
import logging
import math
import re
import statistics
from datetime import date
from pathlib import Path

import networkx as nx
from pyproj import Geod

from distancier import config
from distancier.sources import http

log = logging.getLogger(__name__)
GEOD = Geod(ellps="GRS80")


def _sparql(s, endpoint: str, requete: str) -> list[dict]:
    def _post():
        r = s.post(endpoint, data={"query": requete}, timeout=600,
                   headers={"Accept": "application/sparql-results+json"})
        r.raise_for_status()
        return r.json()

    res = http.avec_reprise(_post)
    return [{k: v["value"] for k, v in b.items()} for b in res["results"]["bindings"]]


def _paginer(s, endpoint: str, gabarit: str, pays: str, taille: int) -> list[dict]:
    lignes, offset = [], 0
    while True:
        q = gabarit.replace("%%PAYS%%", pays).replace("%%LIMIT%%", str(taille)).replace("%%OFFSET%%", str(offset))
        page = _sparql(s, endpoint, q)
        lignes += page
        log.info("RINF %s : %d résultats", pays, len(lignes))
        if len(page) < taille:
            return lignes
        offset += taille


def _ecrire_csv(chemin: Path, lignes: list[dict], colonnes: list[str]) -> None:
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(lignes)


def telecharger(cfg: dict, pays: list[str] | None = None, jour: str | None = None) -> Path:
    src = cfg["sources"]["rinf"]
    pays = pays or list(src["pays"])
    jour = jour or date.today().isoformat()
    dossier = config.dossier_source(cfg, "rinf", jour)
    dossier.mkdir(parents=True, exist_ok=True)
    s = http.session()
    requetes = cfg["_racine"] / "queries"
    q_sections = (requetes / "rinf_sections.rq").read_text(encoding="utf-8")
    q_points = (requetes / "rinf_points.rq").read_text(encoding="utf-8")
    manifeste = {"source": src["nom"], "api": src["endpoint"], "pays": {}}
    for p in pays:
        code = src["pays"][p]
        sections = _paginer(s, src["endpoint"], q_sections, code, src["taille_page"])
        points = _paginer(s, src["endpoint"], q_points, code, src["taille_page"])
        _ecrire_csv(dossier / f"sections_{p}.csv", sections, ["sol", "longueur", "ligne", "op_debut", "op_fin", "v_max", "ecartement"])
        _ecrire_csv(dossier / f"points_{p}.csv", points, ["op", "uopid", "nom", "wkt", "lat", "lon", "type"])
        manifeste["pays"][p] = {"sections": len(sections), "points": len(points), "date_consultation": http.maintenant()}
    http.ecrire_manifeste(dossier, manifeste)
    return dossier


def _position(r: dict):
    m = re.search(r"POINT\s*\(\s*([-\d.eE]+)\s+([-\d.eE]+)", r.get("wkt") or "")
    if m:
        return float(m.group(1)), float(m.group(2))
    try:
        return float(r["lon"]), float(r["lat"])
    except (KeyError, TypeError, ValueError):
        return None


def ecartement(valeur: str | None) -> str | None:
    """'1668', '1435', '1000' ; plusieurs écartements sur une section (voies de
    largeurs différentes ou troisième rail) -> 'mixte' ; non renseigné -> None."""
    mm = sorted({x.strip() for x in (valeur or "").split("+") if x.strip()})
    return None if not mm else (mm[0] if len(mm) == 1 else "mixte")


def charger(cfg: dict, jour: str | None = None, raccordements=()) -> dict:
    dossier = config.dossier_source(cfg, "rinf", jour)
    man = http.lire_manifeste(dossier)
    G = nx.MultiGraph()
    for p in man["pays"]:
        with open(dossier / f"points_{p}.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r["uopid"] in G:
                    continue
                pos = _position(r)
                G.add_node(r["uopid"], nom=r["nom"], pays=p, type=r["type"],
                           lon=pos[0] if pos else None, lat=pos[1] if pos else None)

    sections = []
    for p in man["pays"]:
        with open(dossier / f"sections_{p}.csv", encoding="utf-8") as f:
            sections += [dict(r, pays=p) for r in csv.DictReader(f, delimiter=";")]
    lgv_presumee = cfg["sources"]["rinf"].get("lgv_presumee_ecartement") or {}
    exclus = set(cfg["sources"]["rinf"].get("ecartements_exclus") or ())
    longueurs = [float(r["longueur"]) for r in sections if r["longueur"]]
    # era:length est attendu en mètres ; si la médiane est < 100, les valeurs sont en km
    facteur = 1.0 if longueurs and statistics.median(longueurs) < 100 else 0.001
    if facteur == 1.0:
        log.warning("longueurs RINF interprétées en km (médiane %.1f)", statistics.median(longueurs))
    # Longueur déclarée plus courte que la ligne droite entre les deux points : physiquement
    # impossible (fréquent autour des faisceaux, souvent 0 km). On retient la ligne droite.
    vues, corrigees = set(), 0
    for r in sections:
        if r["sol"] in vues or not r["longueur"]:
            continue
        vues.add(r["sol"])
        v = int(float(r["v_max"])) if r.get("v_max") else None
        a, b = r["op_debut"], r["op_fin"]
        km = float(r["longueur"]) * facteur
        if a in G and b in G and G.nodes[a]["lon"] is not None and G.nodes[b]["lon"] is not None:
            droite = GEOD.inv(G.nodes[a]["lon"], G.nodes[a]["lat"], G.nodes[b]["lon"], G.nodes[b]["lat"])[2] / 1000
            if km < droite:
                corrigees += droite - km > 0.05
                km = droite
        ec = ecartement(r.get("ecartement"))
        if ec in exclus:
            continue
        G.add_edge(a, b, km=km, ligne=r.get("ligne") or "", profil=[v], ecartement=ec,
                   lgv_presumee=v is None and ec is not None and ec == lgv_presumee.get(r["pays"]),
                   sens=(a, b), manuel=None, sol=r["sol"])
    if corrigees:
        log.warning("RINF : %d sections plus courtes de plus de 50 m que la ligne droite entre leurs points, "
                    "portées à cette distance", corrigees)

    for rac in raccordements:
        a, b = point_le_plus_proche(G, *rac.de), point_le_plus_proche(G, *rac.a)
        km = GEOD.inv(*rac.de, *rac.a)[2] / 1000
        G.add_edge(a[0], b[0], km=km, ligne="MANUEL", profil=[rac.v_max], sens=(a[0], b[0]), manuel=rac.nom)

    log.info("RINF %s : %d points, %d sections", dossier.name, G.number_of_nodes(), G.number_of_edges())
    return {"graphe": G, "manifeste": man, "jour": dossier.name,
            "vitesses": sorted({v for _, _, d in G.edges(data=True) for v in d["profil"] if v is not None})}


TYPES_GARE = {"station", "small station", "passenger stop"}


def point_le_plus_proche(G: nx.MultiGraph, lon: float, lat: float, rayon_m: float = math.inf,
                         nom: str | None = None) -> tuple[str, float]:
    """uopid du point d'exploitation relié au réseau le plus proche.

    Avec `nom` (rattachement d'une gare), préférence dans l'ordre : une gare RINF de même nom,
    une gare RINF à moins de 500 m, puis n'importe quel point (faisceau, bifurcation...).
    """
    candidats = []
    cle_nom = _simplifie(nom) if nom else ""
    for n, d in G.nodes(data=True):
        if d.get("lon") is None or G.degree(n) == 0:
            continue
        dist = GEOD.inv(lon, lat, d["lon"], d["lat"])[2]
        if dist > rayon_m:
            continue
        if nom:
            autre = _simplifie(d.get("nom") or "")
            gare = d.get("type") in TYPES_GARE
            meme_nom = gare and bool(autre) and (cle_nom in autre or autre in cle_nom)
            rang = 0 if meme_nom else 1 if gare and dist <= 500 else 2
        else:
            rang = 0
        candidats.append((rang, dist, n))
    if not candidats:
        raise LookupError(f"aucun point d'exploitation RINF à moins de {rayon_m:.0f} m de ({lon}, {lat})")
    _, dist, n = min(candidats)
    return n, dist


def _simplifie(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("saint", "st"))


def description_source(man: dict) -> str:
    return f"{man['source']} ({', '.join(man['pays'])}) via {man['api']}"


def date_consultation(man: dict) -> str:
    return min(p["date_consultation"] for p in man["pays"].values())[:10]
