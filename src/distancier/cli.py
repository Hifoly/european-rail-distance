"""Ligne de commande : distancier telecharger | calculer | charger-bdd."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from distancier import config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="distancier", description="Distancier ferroviaire européen")
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("-v", "--verbeux", action="store_true")
    sub = ap.add_subparsers(dest="commande", required=True)

    t = sub.add_parser("telecharger", help="récupère les données sources par API")
    t.add_argument("--source", choices=["sncf", "rinf", "tout"], default="tout")
    t.add_argument("--pays", nargs="*", help="pays RINF (FR DE IT ES BE CH PT) ; défaut : tous")

    c = sub.add_parser("calculer", help="calcule les distances des relations et exporte le tableau")
    c.add_argument("--jour-sncf", help="date du téléchargement SNCF à utiliser (défaut : le plus récent)")
    c.add_argument("--jour-rinf", help="date du téléchargement RINF à utiliser (défaut : le plus récent)")

    b = sub.add_parser("charger-bdd", help="charge un tableau exporté dans PostgreSQL/PostGIS")
    b.add_argument("fichier", type=Path, help="data/output/distances_AAAA-MM-JJ.csv")
    b.add_argument("--dsn", help="chaîne de connexion (défaut : variable DATABASE_URL)")

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbeux else logging.INFO, format="%(levelname)s %(message)s")
    cfg = config.charger(a.config)

    if a.commande == "telecharger":
        if a.source in ("sncf", "tout"):
            from distancier.sources import sncf
            print("SNCF Réseau ->", sncf.telecharger(cfg))
        if a.source in ("rinf", "tout"):
            from distancier.sources import rinf
            print("RINF ->", rinf.telecharger(cfg, a.pays))
    elif a.commande == "calculer":
        from distancier import calcul, export
        res = calcul.calculer(cfg, a.jour_sncf, a.jour_rinf)
        for f in export.ecrire(res, cfg, config.chemin(cfg, "sorties")):
            print(f)
        erreurs = [r for r in res["relations"] if str(r.get("statut", "")).startswith("erreur")]
        for r in erreurs:
            print(f"relation {r['id']} : {r['statut']}", file=sys.stderr)
    elif a.commande == "charger-bdd":
        from distancier import db
        print("calcul chargé, id", db.charger(cfg, a.fichier, a.dsn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
