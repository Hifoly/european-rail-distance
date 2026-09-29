"""Calcul d'itinéraires sur un graphe ferroviaire (commun aux moteurs sncf et rinf).

Chaque arête porte : km, ligne, profil (liste de v_max échantillonnées le long
de l'arête, dans le sens de `sens`), manuel (nom du raccordement ajouté à la
main, sinon None) et, pour les graphes issus de tracés, geom (en mètres).
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

import networkx as nx
from pyproj import Transformer
from shapely.geometry import Point
from shapely.strtree import STRtree

from distancier.reseau import km_par_vitesse

MODES = ("grande_vitesse", "plus_court", "sans_lgv")
PENALITE_HORS_ITINERAIRE = 5.0   # coût d'un km hors des lignes de l'itinéraire à suivre


def code_ligne(ligne) -> str:
    """'830000-1' (RINF, ligne-voie) -> '830000' ; '830000' (SNCF) inchangé."""
    return str(ligne or "").split("-")[0].strip()


@dataclass
class Resultat:
    km: float
    lignes: list = field(default_factory=list)       # [(ligne, km)] dans l'ordre du trajet
    vitesses: dict = field(default_factory=dict)     # {v_max ou None: km}
    manuels: list = field(default_factory=list)      # raccordements manuels empruntés
    ecartements: dict = field(default_factory=dict)  # {'1668', '1435', '1000', 'mixte' ou None: km}
    aretes: int = 0                                  # nombre d'arêtes du graphe parcourues
    approche: bool = False                           # extrémité placée par interpolation (carte Adif)
    km_vitesse_adif: float = 0.0                     # km RINF dont la vitesse vient de la carte Adif

    def km_lgv(self, seuil: int) -> float:
        return sum(k for v, k in self.vitesses.items() if v is not None and v >= seuil)

    def ligne_dominante(self) -> tuple[str, float]:
        cumul = collections.Counter()
        for ligne, km in self.lignes:
            cumul[ligne] += km
        return cumul.most_common(1)[0] if cumul else ("", 0.0)


class Routeur:
    def __init__(self, G: nx.MultiGraph, seuil_lgv_kmh: int = 250, facteur_lgv: float = 0.75,
                 distance_max_rattachement_m: float = 300, **_):
        self.G = G
        self.seuil = seuil_lgv_kmh
        self.facteur = facteur_lgv
        self.dmax = distance_max_rattachement_m
        for _, _, d in G.edges(data=True):
            d["vitesses"] = km_par_vitesse(d)
            d["part_lgv"] = self._part_lgv(d)
        self._aretes = [(u, v, k, d) for u, v, k, d in G.edges(keys=True, data=True) if "geom" in d and not d.get("manuel") and not d.get("doublon")]
        self._arbre = STRtree([a[3]["geom"] for a in self._aretes]) if self._aretes else None
        # arêtes dupliquées par un virage interdit (reseau.interdire_virage), par géométrie d'origine
        self._doublons = collections.defaultdict(list)
        for u, v, d in G.edges(data=True):
            if d.get("doublon"):
                self._doublons[id(d["geom"])].append(d)
        crs = G.graph.get("crs")
        self._vers_m = Transformer.from_crs(4326, crs, always_xy=True).transform if crs else None
        self._rattachements: dict = {}

    def _part_lgv(self, d: dict) -> float:
        if d.get("lgv_presumee"):   # LAV sans vitesse publiée (voir settings.yaml, lgv_presumee_ecartement)
            return 1.0
        lgv = sum(k for v, k in d["vitesses"].items() if v is not None and v >= self.seuil)
        return lgv / d["km"] if d["km"] else 0.0

    # --- rattachement des gares -------------------------------------------------
    def rattacher(self, tag: str, lon: float, lat: float, lignes_preferees=()) -> float:
        """Ajoute un nœud temporaire `tag` sur la voie la plus proche. Renvoie la distance (m)."""
        if self._arbre is None:
            raise ValueError("graphe sans géométrie : rattacher par identifiant de nœud")
        p = Point(self._vers_m(lon, lat))
        candidats = [i for i in self._arbre.query(p.buffer(self.dmax))]
        if not candidats:
            raise LookupError(f"aucune voie à moins de {self.dmax} m de ({lon}, {lat})")
        dist = {i: self._aretes[i][3]["geom"].distance(p) for i in candidats}
        preferes = [i for i in candidats if self._aretes[i][3]["ligne"] in lignes_preferees and dist[i] < 100]
        i = min(preferes or candidats, key=dist.get)
        u, v, k, d = self._aretes[i]
        g = d["geom"]
        t = g.project(p) / g.length if g.length else 0.0
        debut, fin = d["sens"]
        self._ajouter(tag, debut, d, 0.0, t)
        self._ajouter(tag, fin, d, t, 1.0)
        for dd in self._doublons.get(id(g), ()):   # la gare est aussi sur les copies de l'arête
            self._ajouter(tag, dd["sens"][0], dd, 0.0, t)
            self._ajouter(tag, dd["sens"][1], dd, t, 1.0)
        # deux gares sur la même arête : relier directement
        for autre, (i2, t2) in self._rattachements.items():
            if i2 == i and autre in self.G:
                a, b = sorted((t, t2))
                self._ajouter(tag, autre, d, a, b)
        self._rattachements[tag] = (i, t)
        return dist[i]

    def _ajouter(self, tag, noeud, d, a, b):
        vit = km_par_vitesse(d, a, b)
        km = d["km"] * (b - a)
        attrs = dict(km=km, ligne=d["ligne"], vitesses=vit, manuel=d.get("manuel"), ecartement=d.get("ecartement"),
                     lgv_presumee=d.get("lgv_presumee"), temporaire=True)
        attrs["part_lgv"] = self._part_lgv(attrs)
        self.G.add_edge(tag, noeud, **attrs)

    def detacher(self, *tags):
        for tag in tags:
            if tag in self.G:
                self.G.remove_node(tag)
            self._rattachements.pop(tag, None)

    # --- itinéraire -------------------------------------------------------------
    def _cout(self, d: dict, mode: str, lignes: frozenset | None = None):
        lgv = d["part_lgv"] >= 0.5
        if mode == "sans_lgv" and lgv:
            return None
        cout = d["km"] * (self.facteur if mode == "grande_vitesse" and lgv else 1.0)
        if lignes is not None and code_ligne(d["ligne"]) not in lignes:
            cout *= PENALITE_HORS_ITINERAIRE
        return cout

    def chemin(self, origine, destination, mode: str = "plus_court", lignes=None) -> Resultat:
        """`lignes` (codes de ligne) : itinéraire à suivre, les autres lignes sont pénalisées
        (sert à contrôler une distance sur le même itinéraire que l'autre moteur)."""
        assert mode in MODES, mode
        lignes = frozenset(code_ligne(l) for l in lignes) if lignes is not None else None

        def poids(u, v, dd):
            couts = [c for c in (self._cout(d, mode, lignes) for d in dd.values()) if c is not None]
            return min(couts) if couts else None

        noeuds = nx.shortest_path(self.G, origine, destination, weight=poids)
        res = Resultat(km=0.0)
        vit, ecart = collections.Counter(), collections.Counter()
        for u, v in zip(noeuds[:-1], noeuds[1:]):
            d = min((d for d in self.G[u][v].values() if self._cout(d, mode, lignes) is not None),
                    key=lambda d: self._cout(d, mode, lignes))
            res.km += d["km"]
            res.aretes += 1
            vit.update(d["vitesses"])
            ecart[d.get("ecartement")] += d["km"]
            if d.get("vitesse_adif"):
                res.km_vitesse_adif += d["km"]
            if res.lignes and res.lignes[-1][0] == d["ligne"]:
                res.lignes[-1] = (d["ligne"], res.lignes[-1][1] + d["km"])
            else:
                res.lignes.append((d["ligne"], d["km"]))
            if d.get("manuel") and d["manuel"] not in res.manuels:
                res.manuels.append(d["manuel"])
        res.vitesses = dict(vit)
        res.ecartements = dict(ecart)
        return res
