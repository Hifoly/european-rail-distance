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
import statistics
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


def _secondes(h: str) -> int | None:
    """« 25:10:00 » -> secondes depuis minuit du jour de service (les heures peuvent dépasser 24)."""
    try:
        hh, mm, ss = (int(x) for x in h.strip().split(":"))
    except (AttributeError, ValueError):
        return None
    return hh * 3600 + mm * 60 + ss


def lire_trajets(chemin: Path, produits: list[str]) -> tuple[list[tuple[str, ...]], list[tuple]]:
    """Pour chaque train dont le produit est dans `produits` : la suite des codes UIC desservis, et
    les heures (arrivée, départ) en secondes à chaque arrêt."""
    produits = {p.casefold() for p in produits}
    arrets: dict[str, list[tuple[int, str, int | None, int | None]]] = collections.defaultdict(list)
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
            arrets[trip].append((int(r["stop_sequence"]), m["uic"],
                                 _secondes(r.get("arrival_time")), _secondes(r.get("departure_time"))))
    trains = [sorted(a) for a in arrets.values() if len(a) >= 2]
    return [tuple(x[1] for x in a) for a in trains], [tuple((x[2], x[3]) for x in a) for a in trains]


def charger(cfg: dict, jour: str | None = None) -> dict:
    dossier = config.dossier_source(cfg, "gtfs_sncf", jour)
    man = http.lire_manifeste(dossier)
    trajets, horaires = lire_trajets(dossier / man["fichier"], cfg["sources"]["gtfs_sncf"]["produits_tgv"])
    log.info("horaires SNCF : %d trains TGV", len(trajets))
    return {"trajets": trajets, "horaires": horaires, "manifeste": man, "jour": dossier.name}


class Dessertes:
    """Index des trains TGV par gare, pour retrouver la desserte d'une relation."""

    def __init__(self, trajets: list[tuple[str, ...]], horaires: list[tuple] | None = None):
        self.trajets = trajets
        self.horaires = horaires
        self.par_gare: dict[str, set[int]] = collections.defaultdict(set)
        for i, t in enumerate(trajets):
            for uic in t:
                self.par_gare[uic].add(i)

    def desserte(self, o: str, d: str) -> tuple[tuple[str, ...], int, float | None] | None:
        """Arrêts (de o à d inclus) du TGV direct le plus fréquent, dans un sens ou dans l'autre,
        nombre de trains qui la font, et durée médiane en minutes de tous les TGV directs de l'OD,
        quelle que soit leur desserte (choix d'Aloïs le 2026-10-01). À fréquence égale, la desserte
        la plus courte en arrêts."""
        if o == d:
            return None
        durees: list[float] = []
        compte = collections.Counter()
        for i in self.par_gare.get(o, set()) & self.par_gare.get(d, set()):
            t = self.trajets[i]
            io_, id_ = t.index(o), t.index(d)
            arrets = t[io_:id_ + 1] if io_ < id_ else t[id_:io_ + 1][::-1]
            compte[arrets] += 1
            if self.horaires:
                a, b = sorted((io_, id_))
                depart, arrivee = self.horaires[i][a][1], self.horaires[i][b][0]
                if depart is not None and arrivee is not None and arrivee >= depart:
                    durees.append((arrivee - depart) / 60)
        if not compte:
            return None
        arrets, n = min(compte.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        return arrets, n, (statistics.median(durees) if durees else None)


def description_source(man: dict) -> str:
    return f"{man['source']} ({man['url']})"


def date_consultation(man: dict) -> str:
    return man["date_consultation"][:10]
