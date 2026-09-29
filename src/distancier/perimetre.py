"""Construction du périmètre d'un pays à partir des horaires (Espagne : GTFS Renfe).

Les gares et relations du pays sont ajoutées à config/gares.csv et config/relations.csv.
Une gare déjà présente garde sa ligne (nom, coordonnées, uopid, note saisis à la main) ;
les relations du pays sont réécrites à chaque construction, numérotées à partir de
`premier_id` et triées par codes UIC, pour que les identifiants restent stables.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from distancier import config
from distancier.calcul import lire_csv
from distancier.sources import renfe

log = logging.getLogger(__name__)
COLONNES_GARES = ["uic", "nom", "pays", "lat", "lon", "uopid_rinf", "note"]
COLONNES_RELATIONS = ["id", "uic_origine", "uic_destination", "itineraire", "statut_force", "remarque"]


def _ecrire(chemin: Path, lignes: list[dict], colonnes: list[str]) -> None:
    with open(chemin, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, delimiter=";", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(lignes)


def points_rinf(cfg: dict, pays: str, jour: str | None = None) -> set[str]:
    try:
        dossier = config.dossier_source(cfg, "rinf", jour)
    except FileNotFoundError:
        return set()
    chemin = dossier / f"points_{pays}.csv"
    if not chemin.exists():
        return set()
    with open(chemin, encoding="utf-8") as f:
        return {r["uopid"] for r in csv.DictReader(f, delimiter=";")}


def espagne(cfg: dict, jour_renfe: str | None = None, jour_rinf: str | None = None) -> dict:
    src = cfg["sources"]["renfe"]
    dossier = config.dossier_source(cfg, "renfe", jour_renfe)
    paires, arrets = renfe.couples(dossier / renfe.FICHIER, set(src["produits"]))
    etranger = tuple(src.get("prefixes_etrangers") or ())
    paires = {k: v for k, v in paires.items() if not any(c.startswith(etranger) for c in k)}
    rinf = points_rinf(cfg, "ES", jour_rinf)

    chemin_gares, chemin_rel = config.chemin(cfg, "gares"), config.chemin(cfg, "relations")
    gares = lire_csv(chemin_gares)
    connues = {g["uic"] for g in gares}
    codes = sorted({c for k in paires for c in k})
    sans_rinf = []
    for c in codes:
        if renfe.uic(c) in connues:
            continue
        a = arrets[c]
        u = renfe.uopid(c)
        note = ""
        if u not in rinf:
            sans_rinf.append(a["stop_name"])
            u, note = "", f"code Adif {c} absent de RINF : point RINF le plus proche"
        gares.append({"uic": renfe.uic(c), "nom": a["stop_name"], "pays": "ES", "lat": a["stop_lat"],
                      "lon": a["stop_lon"], "uopid_rinf": u, "note": note})

    pays = {g["uic"]: g["pays"] for g in gares}
    relations = [r for r in lire_csv(chemin_rel)
                 if not (pays.get(r["uic_origine"]) == "ES" and pays.get(r["uic_destination"]) == "ES")]
    premier = int(src.get("premier_id", 10001))
    for i, (a, b) in enumerate(sorted(paires, key=lambda k: (renfe.uic(k[0]), renfe.uic(k[1])))):
        relations.append({"id": premier + i, "uic_origine": renfe.uic(a), "uic_destination": renfe.uic(b),
                          "itineraire": "grande_vitesse", "statut_force": "",
                          "remarque": "périmètre Renfe : " + ", ".join(sorted(paires[(a, b)]))})
    _ecrire(chemin_gares, gares, COLONNES_GARES)
    _ecrire(chemin_rel, relations, COLONNES_RELATIONS)
    if sans_rinf:
        log.warning("gares sans point RINF à leur code Adif : %s", ", ".join(sans_rinf))
    return {"relations": len(paires), "gares": len(codes), "sans_rinf": sans_rinf}
