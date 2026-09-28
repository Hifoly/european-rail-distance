"""Open data SNCF Réseau (portail Opendatasoft, API Explore v2.1).

Jeux utilisés (identifiants dans config/settings.yaml) :
- formes-des-lignes-du-rfn : tracés des lignes (champs code_ligne, mnemo, pk_debut_r, pk_fin_r)
- vitesse-maximale-nominale-sur-ligne : v_max par tronçon (code_ligne, v_max, pkd, pkf)
- liste-des-gares : code_uic, libelle, code_ligne, pk, x_wgs84, y_wgs84 (une ligne par gare × ligne)
- fichier-de-formes-des-voies-du-reseau-ferre-national : tracés voie par voie (code_ligne, nom_voie,
  pk_debut_r, pk_fin_r). Sert seulement pour les lignes absentes de formes-des-lignes-du-rfn, listées
  dans config/corrections.yaml (lignes_complementaires).
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path

from shapely.geometry import shape

from distancier import config
from distancier.reseau import Troncon, TronconVitesse, parse_pk
from distancier.sources import http

log = logging.getLogger(__name__)
csv.field_size_limit(2**31 - 1)


def telecharger(cfg: dict, jour: str | None = None) -> Path:
    src = cfg["sources"]["sncf"]
    base = src["base_url"].rstrip("/")
    jour = jour or date.today().isoformat()
    dossier = config.dossier_source(cfg, "sncf", jour)
    s = http.session()
    manifeste = {"source": src["nom"], "api": base, "jeux": {}}
    for cle, jeu in src["jeux"].items():
        url = f"{base}/catalog/datasets/{jeu['id']}/exports/{jeu['format']}"
        params = {"delimiter": ";"} if jeu["format"] == "csv" else None
        dest = http.telecharger(url, dossier / f"{jeu['id']}.{jeu['format']}", params, s)
        meta = http.avec_reprise(lambda: s.get(f"{base}/catalog/datasets/{jeu['id']}", timeout=60).json())
        metas = (meta.get("metas") or {}).get("default") or {}
        manifeste["jeux"][cle] = {
            "id": jeu["id"],
            "url": url,
            "fichier": dest.name,
            "octets": dest.stat().st_size,
            "date_consultation": http.maintenant(),
            "modifie_a_la_source": metas.get("modified") or metas.get("data_processed"),
            "licence": metas.get("license"),
        }
        log.info("%s : %s octets", jeu["id"], dest.stat().st_size)
    http.ecrire_manifeste(dossier, manifeste)
    return dossier


def _features(chemin: Path):
    for f in json.loads(chemin.read_text(encoding="utf-8"))["features"]:
        if f.get("geometry"):
            yield f["properties"], shape(f["geometry"])


def _lignes_simples(geom):
    return list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]


def lire_gares(chemin: Path) -> dict[str, dict]:
    """Gares SNCF par code UIC : nom, position, PK sur chaque ligne."""
    gares: dict[str, dict] = {}
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            uic = r["code_uic"].strip()
            g = gares.setdefault(uic, {"uic": uic, "nom": r["libelle"], "lon": None, "lat": None, "pks": {}})
            try:
                g["lon"], g["lat"] = float(r["x_wgs84"]), float(r["y_wgs84"])
            except (KeyError, TypeError, ValueError):
                pass
            pk = parse_pk(r.get("pk"))
            if pk is not None:
                g["pks"][r["code_ligne"]] = pk
    return gares


def dernieres_gares(cfg: dict) -> dict[str, dict]:
    dossier = config.dossier_source(cfg, "sncf")
    man = http.lire_manifeste(dossier)
    return lire_gares(dossier / man["jeux"]["gares"]["fichier"])


def _voies_complementaires(chemin: Path, codes: set[str]) -> list[Troncon]:
    """Pour chaque ligne demandée, la voie la plus longue du fichier des voies (une seule, pour ne pas doubler)."""
    meilleures: dict[str, tuple] = {}
    for p, g in _features(chemin):
        code = str(p["code_ligne"])
        if code not in codes:
            continue
        for part in _lignes_simples(g):
            if code not in meilleures or part.length > meilleures[code][1].length:
                meilleures[code] = (p, part)
    manquantes = sorted(codes - set(meilleures))
    if manquantes:
        log.warning("lignes complémentaires absentes du fichier des voies : %s", ", ".join(manquantes))
    return [Troncon(code, g, parse_pk(p.get("pk_debut_r")), parse_pk(p.get("pk_fin_r")))
            for code, (p, g) in sorted(meilleures.items())]


def charger(cfg: dict, jour: str | None = None, complementaires: set[str] | frozenset = frozenset()) -> dict:
    src = cfg["sources"]["sncf"]
    dossier = config.dossier_source(cfg, "sncf", jour)
    man = http.lire_manifeste(dossier)
    fichiers = {cle: dossier / j["fichier"] for cle, j in man["jeux"].items()}
    statuts = set(src.get("statuts_lignes") or [])

    troncons = []
    for p, g in _features(fichiers["lignes"]):
        if statuts and p.get("mnemo") not in statuts:
            continue
        for part in _lignes_simples(g):
            troncons.append(Troncon(str(p["code_ligne"]), part, parse_pk(p.get("pk_debut_r")), parse_pk(p.get("pk_fin_r"))))

    if complementaires:
        if "voies" in fichiers:
            deja = {t.ligne for t in troncons}
            troncons += _voies_complementaires(fichiers["voies"], set(complementaires) - deja)
        else:
            log.warning("jeu des voies non téléchargé : lignes complémentaires ignorées (relancer « distancier telecharger »)")

    vitesses = []
    for p, g in _features(fichiers["vitesses"]):
        try:
            v = int(float(p["v_max"]))
        except (TypeError, ValueError):
            v = None
        vitesses.append(TronconVitesse(str(p["code_ligne"]), v, g))

    gares = lire_gares(fichiers["gares"])

    log.info("SNCF %s : %d tracés, %d tronçons de vitesse, %d gares", dossier.name, len(troncons), len(vitesses), len(gares))
    return {"troncons": troncons, "vitesses": vitesses, "gares": gares, "manifeste": man, "jour": dossier.name}


def description_source(man: dict) -> str:
    ids = ", ".join(j["id"] for j in man["jeux"].values())
    return f"{man['source']} ({ids}) via {man['api']}"


def date_consultation(man: dict) -> str:
    return min(j["date_consultation"] for j in man["jeux"].values())[:10]
