"""Distances de la carte 1 de la Declaración sobre la Red d'Adif (contrôle espagnol).

Adif ne publie pas de fichier de points kilométriques. Sa Declaración sobre la Red 2026
(www.adif.es, « 20260227_03_DR_Adif_2026_Mapas.pdf », carte 1, calques « distancias AV » et
« distancias Adif ») donne en km entiers les distances entre les principales gares et
bifurcations. Elles ont été transcrites le 2026-09-29 dans config/adif/carte1_2026.csv
(de ; a ; km ; calque ; note) ; config/adif/noeuds.csv associe chaque nœud de la carte au
point RINF de même gare (uopid), vide pour les jonctions sans équivalent sûr, ou au code UIC
pour les gares absentes de RINF.

Une gare absente de la carte (gare intermédiaire) est placée sur le tronçon dont elle
s'écarte le moins (somme des distances à vol d'oiseau aux deux bouts / longueur du tronçon),
au prorata de ces distances : distance « approchée », qui sert à détecter un détour du RINF
mais jamais à passer une relation en « vérifié ».

Le graphe est topologique, sans vitesse ni écartement. Pour l'itinéraire « grande_vitesse »,
les tronçons du calque AV (lignes d'Adif Alta Velocidad) comptent comme LGV.
"""
from __future__ import annotations

import csv
import logging
import math
from pathlib import Path

import networkx as nx

log = logging.getLogger(__name__)
SOURCE = ("Adif, Declaración sobre la Red 2026, carte 1 (distances en km entiers), "
          "www.adif.es, transcrite dans config/adif/carte1_2026.csv")
DATE = "2026-09-29"


def geodesique_km(a: tuple, b: tuple) -> float:
    """Distance à vol d'oiseau (sphère de 6 371 km) entre deux points (lon, lat) : sert
    seulement à placer une gare sur un tronçon, jamais comme distance."""
    lo1, la1, lo2, la2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _lire(chemin: Path) -> list[dict]:
    with open(chemin, encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f, delimiter=";")]


def charger(cfg: dict) -> dict:
    src = cfg["sources"]["adif"]
    racine = cfg["_racine"]
    G = nx.MultiGraph()
    for r in _lire(racine / src["troncons"]):
        G.add_edge(r["de"], r["a"], km=float(r["km"]), ligne=f"Adif {r['calque']}", profil=[None],
                   ecartement=None, lgv_presumee=r["calque"] == "AV", sens=(r["de"], r["a"]), manuel=None,
                   note=r["note"])
    points = {}   # uopid RINF ou code UIC -> nœud de la carte
    coords = {}   # nœud -> (lon, lat), coordonnées du point RINF (ou de la gare), pour l'interpolation
    for r in _lire(racine / src["noeuds"]):
        if r.get("lat") and r.get("lon"):
            coords[r["nom"]] = (float(r["lon"]), float(r["lat"]))
        for cle in (r.get("uopid_rinf"), r.get("uic")):
            if cle:
                if cle in points:
                    log.warning("Adif : %s et %s ont le même identifiant %s", points[cle], r["nom"], cle)
                points[cle] = r["nom"]
    # bifurcation sans point RINF : coordonnées du voisin à moins de 5 km sur la carte
    for u, v, d in G.edges(data=True):
        if d["km"] <= 5:
            for a, b in ((u, v), (v, u)):
                if a not in coords and b in coords:
                    coords[a] = coords[b]
    inconnus = [n for n in points.values() if n not in G]
    if inconnus:
        raise ValueError(f"noeuds Adif sans tronçon : {', '.join(inconnus)}")
    return {"graphe": G, "points": points, "coords": coords}
