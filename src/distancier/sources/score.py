"""Plan de transport TGV théorique (Extract_score, fichier local fourni par Aloïs, non versionné).

Une ligne par train, montée, descente et jour type (annee, periode, jour_type). Colonnes lues :
annee, arret_montee_iata, arret_descente_iata, duree_commerciale_minutes, nombre_jour (jours de
l'année représentés par le jour type) et compteur (0,5 pour chaque tranche d'un train couplé).
temps_score = médiane de duree_commerciale_minutes sur l'année, pondérée par nombre_jour × compteur,
les deux sens réunis. Les codes IATA sont reliés aux UIC par couples_montee_descente_FR_UIC.csv.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import logging
import pickle
from datetime import date
from pathlib import Path

from distancier import config

log = logging.getLogger(__name__)

COLONNES = ("annee", "arret_montee_iata", "arret_descente_iata", "duree_commerciale_minutes", "nombre_jour", "compteur")


def _chemin(cfg: dict, cle: str) -> Path:
    p = Path(cfg["sources"]["score"][cle])
    return p if p.is_absolute() else cfg["_racine"] / p


def table_iata(chemin: Path) -> dict[str, str]:
    """UIC -> code IATA, d'après le fichier des couples montée/descente."""
    uic_iata = {}
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            for sens in ("montee", "descente"):
                uic_iata[r[f"arret_{sens}_uic"].strip()] = r[f"arret_{sens}_iata"].strip()
    return uic_iata


def _lignes(chemin: Path):
    """Lignes du fichier (xlsx, premier onglet, ou csv `;`) sous forme de dict limité à COLONNES."""
    if chemin.suffix.lower() == ".csv":
        with open(chemin, encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")
        return
    from openpyxl import load_workbook
    wb = load_workbook(chemin, read_only=True, data_only=True)
    try:
        lignes = wb.worksheets[0].iter_rows(values_only=True)
        entete = [str(c).strip() if c is not None else "" for c in next(lignes)]
        idx = {c: entete.index(c) for c in COLONNES}
        for r in lignes:
            yield {c: r[i] for c, i in idx.items()}
    finally:
        wb.close()


def mediane_ponderee(valeurs: list[tuple[float, float]]) -> float | None:
    """Médiane de (valeur, poids) : plus petite valeur dont le poids cumulé atteint la moitié du total."""
    valeurs = sorted((v, p) for v, p in valeurs if p > 0)
    total = sum(p for _, p in valeurs)
    cumul = 0.0
    for v, p in valeurs:
        cumul += p
        if cumul >= total / 2:
            return v
    return None


def lire_temps(chemin: Path, annee: int) -> dict[frozenset, float]:
    """{frozenset({iata_a, iata_b}): temps médian en minutes} pour l'année demandée, deux sens réunis."""
    durees: dict[frozenset, list[tuple[float, float]]] = collections.defaultdict(list)
    for r in _lignes(chemin):
        try:
            if int(float(r["annee"])) != annee:
                continue
            duree = float(r["duree_commerciale_minutes"])
            poids = float(r["nombre_jour"] or 0) * float(r["compteur"] if r["compteur"] not in (None, "") else 1)
        except (TypeError, ValueError):
            continue
        a, b = str(r["arret_montee_iata"] or "").strip(), str(r["arret_descente_iata"] or "").strip()
        if a and b and a != b:
            durees[frozenset((a, b))].append((duree, poids))
    return {k: m for k, v in durees.items() if (m := mediane_ponderee(v)) is not None}


def charger(cfg: dict) -> dict:
    """Temps médians de l'année configurée, gardés en cache dans data/interim (lecture du xlsx : quelques minutes)."""
    src = cfg["sources"]["score"]
    fichier = _chemin(cfg, "fichier")
    if not fichier.exists():
        raise FileNotFoundError(f"{fichier} absent : temps_score non calculé")
    st = fichier.stat()
    cle = hashlib.sha1(f"{fichier.name}|{st.st_size}|{st.st_mtime_ns}|{src['annee']}".encode()).hexdigest()[:10]
    cache = config.chemin(cfg, "intermediaire") / f"score_{src['annee']}_{cle}.pkl"
    if cache.exists():
        temps = pickle.loads(cache.read_bytes())
    else:
        log.info("lecture de %s (année %s)…", fichier.name, src["annee"])
        temps = lire_temps(fichier, int(src["annee"]))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(temps))
    modifie = date.fromtimestamp(st.st_mtime).isoformat()
    return {"temps": temps, "iata": table_iata(_chemin(cfg, "couples")),
            "description": f"{src['nom']} ({fichier.name}, année {src['annee']})", "date_consultation": modifie}
