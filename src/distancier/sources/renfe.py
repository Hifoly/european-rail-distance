"""Horaires Renfe (GTFS grandes lignes : alta velocidad, larga y media distancia).

Sert à construire le périmètre espagnol : une relation par couple de gares desservies
par un même train (montée possible à la première, descente à la seconde), pour les
produits retenus dans config/settings.yaml. Les identifiants d'arrêt du GTFS sont les
codes de gare Adif à 5 chiffres : le point RINF est « ES » + code, le code UIC « 71 » + code.
"""
from __future__ import annotations

import collections
import csv
import io
import logging
import zipfile
from datetime import date
from pathlib import Path

from distancier import config
from distancier.sources import http

log = logging.getLogger(__name__)
FICHIER = "google_transit.zip"


def telecharger(cfg: dict, jour: str | None = None) -> Path:
    src = cfg["sources"]["renfe"]
    jour = jour or date.today().isoformat()
    dossier = config.dossier_source(cfg, "renfe", jour)
    dest = http.telecharger(src["gtfs"], dossier / FICHIER)
    http.ecrire_manifeste(dossier, {"source": src["nom"], "url": src["gtfs"], "licence": src.get("licence"),
                                    "fichier": dest.name, "octets": dest.stat().st_size,
                                    "date_consultation": http.maintenant()})
    return dossier


def _lire(z: zipfile.ZipFile, nom: str):
    with z.open(nom) as f:
        for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            yield {k.strip(): (v or "").strip() for k, v in r.items() if k}


def couples(chemin_zip: Path, produits: set[str]) -> tuple[dict, dict]:
    """({(code_a, code_b) triés: {produits}}, {code: arrêt GTFS}) pour les trains des produits donnés."""
    with zipfile.ZipFile(chemin_zip) as z:
        routes = {r["route_id"]: r["route_short_name"] for r in _lire(z, "routes.txt")}
        trips = {t["trip_id"]: routes.get(t["route_id"]) for t in _lire(z, "trips.txt")}
        arrets = {s["stop_id"]: s for s in _lire(z, "stops.txt")}
        passages = collections.defaultdict(list)
        for r in _lire(z, "stop_times.txt"):
            if trips.get(r["trip_id"]) in produits:
                passages[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"],
                                               r["pickup_type"] != "1", r["drop_off_type"] != "1"))
    paires = collections.defaultdict(set)
    for trip, seq in passages.items():
        seq.sort()
        for i, (_, a, montee, _) in enumerate(seq):
            if not montee:
                continue
            for _, b, _, descente in seq[i + 1:]:
                if descente and a != b:
                    paires[tuple(sorted((a, b)))].add(trips[trip])
    return dict(paires), arrets


def uic(code: str) -> str:
    return "71" + code


def uopid(code: str) -> str:
    return "ES" + code
