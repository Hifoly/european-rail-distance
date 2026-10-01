"""Horaires théoriques SNCF (GTFS) : dessertes des TGV, pour l'itinéraire « TGV commercial ».

Le GTFS SNCF (plandata) code le produit dans l'identifiant d'arrêt :
« StopPoint:OCETGV INOUI-87686006 », « StopPoint:OCEOUIGO-87391003 »… Le code UIC est
les 8 derniers chiffres. Seuls les trains dont le produit est listé dans
config/settings.yaml (sources.gtfs_sncf.produits_tgv) sont gardés.
"""
from __future__ import annotations

import collections
import csv
import io
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

from distancier import config
from distancier.sources import http

log = logging.getLogger(__name__)

_ARRET = re.compile(r"^StopPoint:OCE(?P<produit>.+)-(?P<uic>\d{8})$")


def telecharger(cfg: dict, jour: str | None = None) -> Path:
    src = cfg["sources"]["gtfs_sncf"]
    jour = jour or date.today().isoformat()
    dossier = config.dossier_source(cfg, "gtfs_sncf", jour)
    dest = http.telecharger(src["url"], dossier / "gtfs.zip")
    http.ecrire_manifeste(dossier, {"source": src["nom"], "url": src["url"], "fichier": dest.name,
                                    "octets": dest.stat().st_size, "date_consultation": http.maintenant()})
    return dossier


def _lire(z: zipfile.ZipFile, nom: str):
    with z.open(nom) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline=""))


def lire_trajets(chemin: Path, produits: list[str]) -> list[tuple[str, ...]]:
    """Suite des codes UIC desservis par chaque train dont le produit est dans `produits`."""
    produits = {p.casefold() for p in produits}
    arrets: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    exclus = set()
    with zipfile.ZipFile(chemin) as z:
        for r in _lire(z, "stop_times.txt"):
            trip = r["trip_id"]
            if trip in exclus:
                continue
            m = _ARRET.match(r["stop_id"].strip())
            if not m or m["produit"].strip().casefold() not in produits:
                exclus.add(trip)
                arrets.pop(trip, None)
                continue
            arrets[trip].append((int(r["stop_sequence"]), m["uic"]))
    return [tuple(uic for _, uic in sorted(a)) for a in arrets.values() if len(a) >= 2]


def charger(cfg: dict, jour: str | None = None) -> dict:
    dossier = config.dossier_source(cfg, "gtfs_sncf", jour)
    man = http.lire_manifeste(dossier)
    trajets = lire_trajets(dossier / man["fichier"], cfg["sources"]["gtfs_sncf"]["produits_tgv"])
    log.info("horaires SNCF : %d trains TGV", len(trajets))
    return {"trajets": trajets, "manifeste": man, "jour": dossier.name}


class Dessertes:
    """Index des trains TGV par gare, pour retrouver la desserte d'une relation."""

    def __init__(self, trajets: list[tuple[str, ...]]):
        self.trajets = trajets
        self.par_gare: dict[str, set[int]] = collections.defaultdict(set)
        for i, t in enumerate(trajets):
            for uic in t:
                self.par_gare[uic].add(i)

    def desserte(self, o: str, d: str) -> tuple[tuple[str, ...], int] | None:
        """Arrêts (de o à d inclus) du TGV direct le plus fréquent, dans un sens ou dans l'autre,
        et nombre de trains qui la font. À fréquence égale, la desserte la plus courte en arrêts."""
        compte = collections.Counter()
        for i in self.par_gare.get(o, set()) & self.par_gare.get(d, set()):
            t = self.trajets[i]
            io_, id_ = t.index(o), t.index(d)
            compte[t[io_:id_ + 1] if io_ < id_ else t[id_:io_ + 1][::-1]] += 1
        if not compte:
            return None
        arrets, n = min(compte.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        return arrets, n


def description_source(man: dict) -> str:
    return f"{man['source']} ({man['url']})"


def date_consultation(man: dict) -> str:
    return man["date_consultation"][:10]
