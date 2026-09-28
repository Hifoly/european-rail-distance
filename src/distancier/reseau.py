"""Construction d'un graphe ferroviaire à partir de tracés géographiques.

Utilisé par le moteur « sncf » (et réutilisable pour tout gestionnaire qui publie
des tracés de lignes avec leur vitesse maximale) :

1. les tracés sont projetés en mètres et découpés à chaque point où l'extrémité
   d'un autre tracé les touche (bifurcations en T) ;
2. les extrémités proches de moins de `tolerance_noeud_m` deviennent un même nœud ;
3. chaque arête reçoit sa longueur géodésique (km) et un profil de vitesse
   maximale échantillonné tous les `pas_echantillonnage_vitesse_m`.
"""
from __future__ import annotations

import collections
import logging
import math
from dataclasses import dataclass, field

import networkx as nx
from pyproj import Geod, Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring, transform
from shapely.strtree import STRtree

log = logging.getLogger(__name__)
GEOD = Geod(ellps="GRS80")


@dataclass
class Troncon:
    ligne: str
    geom: LineString               # WGS84 (lon, lat)
    pk_debut: float | None = None  # PK au premier point du tracé, en km
    pk_fin: float | None = None


@dataclass
class TronconVitesse:
    ligne: str
    v_max: int | None
    geom: object                   # LineString ou MultiLineString WGS84


@dataclass
class Raccordement:
    nom: str
    de: tuple[float, float]
    a: tuple[float, float]
    v_max: int | None = None
    note: str = ""


def longueur_km(geom_wgs84: LineString) -> float:
    xs, ys = geom_wgs84.xy
    return GEOD.line_length(xs, ys) / 1000


def parse_pk(texte: str | None) -> float | None:
    """'123+456' -> 123.456 ; '012-200' -> 11.8 (PK négatif relatif) ; sinon None."""
    if texte is None:
        return None
    s = str(texte).strip()
    try:
        if "+" in s:
            a, b = s.split("+")
            return int(a) + int(b) / 1000
        if "-" in s[1:]:
            a, b = s[0] + s[1:].split("-")[0], s[1:].split("-")[1]
            return int(a) - int(b) / 1000
        return float(s.replace(",", "."))
    except ValueError:
        return None


@dataclass
class Grille:
    """Regroupe des points proches en nœuds (recherche sur grille)."""
    tol: float
    reps: list = field(default_factory=list)
    cases: dict = field(default_factory=dict)

    def noeud(self, c) -> int:
        gx, gy = int(c[0] // self.tol), int(c[1] // self.tol)
        meilleur = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in self.cases.get((gx + dx, gy + dy), ()):
                    d = math.dist(self.reps[k], c)
                    if d <= self.tol and (meilleur is None or d < meilleur[0]):
                        meilleur = (d, k)
        if meilleur:
            return meilleur[1]
        self.reps.append(tuple(c[:2]))
        self.cases.setdefault((gx, gy), []).append(len(self.reps) - 1)
        return len(self.reps) - 1


def construire_graphe(troncons: list[Troncon], vitesses: list[TronconVitesse], crs: int = 3035,
                      tolerance_noeud_m: float = 50, pas_echantillonnage_vitesse_m: float = 100,
                      tolerance_vitesse_m: float = 25, raccordements: list[Raccordement] = (),
                      **_) -> nx.MultiGraph:
    vers_m = Transformer.from_crs(4326, crs, always_xy=True).transform
    vers_deg = Transformer.from_crs(crs, 4326, always_xy=True).transform
    tol = tolerance_noeud_m

    geoms = [transform(vers_m, t.geom) for t in troncons]
    arbre = STRtree(geoms)
    coupes = [{0.0, g.length} for g in geoms]
    for i, g in enumerate(geoms):
        for extremite in (Point(g.coords[0]), Point(g.coords[-1])):
            for j in arbre.query(extremite.buffer(tol)):
                if j != i and geoms[j].distance(extremite) <= tol:
                    coupes[j].add(geoms[j].project(extremite))
    # un raccordement manuel peut aboutir en pleine ligne : on y coupe aussi le tracé
    for r in raccordements:
        for bout in (Point(vers_m(*r.de)), Point(vers_m(*r.a))):
            for j in arbre.query(bout.buffer(tol)):
                if geoms[j].distance(bout) <= tol:
                    coupes[j].add(geoms[j].project(bout))

    vgeoms = [transform(vers_m, v.geom) for v in vitesses]
    varbre = STRtree(vgeoms) if vgeoms else None

    def profil(ligne: str, g: LineString) -> list:
        n = max(1, round(g.length / pas_echantillonnage_vitesse_m))
        out = []
        for k in range(n):
            p = g.interpolate((k + 0.5) * g.length / n)
            best = None
            if varbre is not None:
                for j in varbre.query(p.buffer(tolerance_vitesse_m)):
                    d = vgeoms[j].distance(p)
                    if d > tolerance_vitesse_m:
                        continue
                    cle = (vitesses[j].ligne != ligne, d)  # même ligne d'abord
                    if best is None or cle < best[0]:
                        best = (cle, vitesses[j].v_max)
            out.append(best[1] if best else None)
        return out

    grille = Grille(tol)
    G = nx.MultiGraph(crs=crs)
    for i, (t, g) in enumerate(zip(troncons, geoms)):
        ds = sorted(coupes[i])
        ds = [d for k, d in enumerate(ds) if k == 0 or d - ds[k - 1] > 1.0]
        if g.length - ds[-1] <= 1.0:
            ds[-1] = g.length
        else:
            ds.append(g.length)
        for a, b in zip(ds[:-1], ds[1:]):
            morceau = substring(g, a, b)
            if morceau.length < 0.5:
                continue
            fa, fb = a / g.length, b / g.length
            pka = pkb = None
            if t.pk_debut is not None and t.pk_fin is not None:
                pka = t.pk_debut + (t.pk_fin - t.pk_debut) * fa
                pkb = t.pk_debut + (t.pk_fin - t.pk_debut) * fb
            u, v = grille.noeud(morceau.coords[0]), grille.noeud(morceau.coords[-1])
            G.add_edge(u, v, km=longueur_km(transform(vers_deg, morceau)), ligne=t.ligne, geom=morceau,
                       pk_u=pka, pk_v=pkb, sens=(u, v), profil=profil(t.ligne, morceau), manuel=None)

    for r in raccordements:
        pa, pb = vers_m(*r.de), vers_m(*r.a)
        u, v = grille.noeud(pa), grille.noeud(pb)
        seg = LineString([pa, pb])
        G.add_edge(u, v, km=longueur_km(LineString([r.de, r.a])), ligne="MANUEL", geom=seg,
                   pk_u=None, pk_v=None, sens=(u, v), profil=[r.v_max], manuel=r.nom)

    for k, c in enumerate(grille.reps):
        if k in G:
            G.nodes[k]["xy"] = c
    composantes = sorted((len(c) for c in nx.connected_components(G)), reverse=True)
    log.info("graphe : %d nœuds, %d arêtes, %d composantes (plus grandes : %s)",
             G.number_of_nodes(), G.number_of_edges(), len(composantes), composantes[:5])
    return G


def km_par_vitesse(arete: dict, debut: float = 0.0, fin: float = 1.0) -> dict:
    """Répartit les km de la portion [debut, fin] (fractions depuis pk_u) par vitesse."""
    profil = arete["profil"]
    n = len(profil)
    km = arete["km"] * (fin - debut)
    if km <= 0:
        return {}
    compte = collections.Counter()
    for k, v in enumerate(profil):
        centre = (k + 0.5) / n
        if debut <= centre < fin or (n == 1):
            compte[v] += 1
    if not compte:  # portion plus courte qu'un échantillon : prendre le plus proche
        compte[profil[min(n - 1, int((debut + fin) / 2 * n))]] += 1
    total = sum(compte.values())
    return {v: km * c / total for v, c in compte.items()}
