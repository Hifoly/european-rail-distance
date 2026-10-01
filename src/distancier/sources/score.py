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
# lecture par relation et sous-relation : arrêts de chaque train dans l'ordre, et horaires
COLONNES_SOUS_RELATIONS = COLONNES + ("relation", "sous_relation", "train_uid", "ordre_montee", "ordre_descente",
                                      "depart_service_seconds", "arrivee_service_seconds")


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


def _lignes(chemin: Path, colonnes: tuple = COLONNES):
    """Lignes du fichier (xlsx, premier onglet, ou csv `;`) sous forme de dict limité à `colonnes`."""
    if chemin.suffix.lower() == ".csv":
        with open(chemin, encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f, delimiter=";")
        return
    from openpyxl import load_workbook
    wb = load_workbook(chemin, read_only=True, data_only=True)
    try:
        lignes = wb.worksheets[0].iter_rows(values_only=True)
        entete = [str(c).strip() if c is not None else "" for c in next(lignes)]
        idx = {c: entete.index(c) for c in colonnes}
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


def _nombre(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def lire_sous_relations(chemin: Path, annee: int) -> dict:
    """Par relation, sous-relation, montée et descente (dans le sens du train) : temps médian, et
    dessertes (arrêts de la montée à la descente) avec leur poids. Par tronçon entre deux arrêts
    consécutifs d'une sous-relation : temps de parcours médian, de départ à arrivée.

    Un train est un train_uid (numéro de train × periode_label) ; ses arrêts et leur ordre viennent
    de ordre_montee / ordre_descente de toutes ses lignes. Poids : nombre_jour × compteur."""
    trains: dict[str, dict] = {}
    for r in _lignes(chemin, COLONNES_SOUS_RELATIONS):
        try:
            if int(float(r["annee"])) != annee:
                continue
            om, od = int(float(r["ordre_montee"])), int(float(r["ordre_descente"]))
            duree = float(r["duree_commerciale_minutes"])
            poids = float(r["nombre_jour"] or 0) * float(r["compteur"] if r["compteur"] not in (None, "") else 1)
        except (TypeError, ValueError):
            continue
        m, d = str(r["arret_montee_iata"] or "").strip(), str(r["arret_descente_iata"] or "").strip()
        if not m or not d or m == d or om >= od:
            continue
        t = trains.setdefault(str(r["train_uid"]), {
            "cle": (str(r["relation"] or "").strip(), str(r["sous_relation"] or "").strip()),
            "poids": poids, "arrets": {}, "durees": {}})
        for ordre, iata, cle_h, h in ((om, m, "depart", r["depart_service_seconds"]),
                                      (od, d, "arrivee", r["arrivee_service_seconds"])):
            a = t["arrets"].setdefault(ordre, {"iata": iata})
            if _nombre(h) is not None:
                a[cle_h] = _nombre(h)
        t["durees"].setdefault((om, od), []).append((duree, poids))   # poids de la ligne, pas du train

    od_durees: dict[tuple, list] = collections.defaultdict(list)
    od_dessertes: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    troncons: dict[tuple, list] = collections.defaultdict(list)
    for t in trains.values():
        rel, srel = t["cle"]
        ordres = sorted(t["arrets"])
        arrets = [t["arrets"][o] for o in ordres]
        for x, y in zip(arrets, arrets[1:]):
            if "depart" in x and "arrivee" in y and y["arrivee"] >= x["depart"]:
                troncons[(rel, srel, x["iata"], y["iata"])].append(((y["arrivee"] - x["depart"]) / 60, t["poids"]))
        for (om, od), lignes in t["durees"].items():
            cle = (rel, srel, t["arrets"][om]["iata"], t["arrets"][od]["iata"])
            sous = [t["arrets"][o] for o in ordres if om <= o <= od]
            desserte = tuple(a["iata"] for a in sous)
            # temps de ce train sur chaque tronçon (None si l'heure manque)
            temps_tr = tuple((y["arrivee"] - x["depart"]) / 60 if "depart" in x and "arrivee" in y else None
                             for x, y in zip(sous, sous[1:]))
            for duree, poids in lignes:
                od_durees[cle].append((duree, poids, desserte, temps_tr))
                od_dessertes[cle][desserte] += poids
    ods = {}
    for cle, trains_od in od_durees.items():
        durees = [(d, p) for d, p, *_ in trains_od]
        dessertes = od_dessertes[cle]
        total = sum(dessertes.values())
        # la plus fréquente ; à poids égal, celle qui a le moins d'arrêts
        arrets, poids = min(dessertes.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        mediane = mediane_ponderee(durees)
        roulent = [d for d, p in durees if p > 0] or [d for d, _ in durees]   # trains qui circulent dans l'année
        temps = {"min": min(roulent), "max": max(roulent), "": mediane}
        ods[cle] = {"temps": mediane, "desserte": arrets,
                    **{f"temps{'_' + k if k else ''}": v for k, v in temps.items() if k},
                    **{f"desserte{'_' + k if k else '_mediane'}": _desserte_du_temps(trains_od, v) for k, v in temps.items()},
                    "part_desserte_pct": round(100 * poids / total, 1) if total else None,
                    "circulations": round(sum(p for _, p in durees), 1)}
    return {"od": ods, "troncons": {k: m for k, v in troncons.items() if (m := mediane_ponderee(v)) is not None}}


def _desserte_du_temps(trains_od: list[tuple], minutes: float | None) -> tuple | None:
    """(desserte, temps par tronçon) des trains qui mettent `minutes`. Desserte : arrêts de la montée à
    la descente ; s'ils diffèrent, la plus fréquente (poids nombre_jour × compteur), puis celle qui a le
    moins d'arrêts. Temps par tronçon : ceux du train de plus grand poids qui fait cette desserte."""
    poids = collections.Counter()
    for d, p, desserte, _ in trains_od:
        if d == minutes:
            poids[desserte] += p
    if not poids:
        return None
    desserte = min(poids, key=lambda x: (-poids[x], len(x), x))
    temps = max((x for x in trains_od if x[0] == minutes and x[2] == desserte), key=lambda x: x[1])[3]
    return desserte, temps


def _charger(cfg: dict, nom_cache: str, lire) -> dict:
    """Lecture du fichier configuré, gardée en cache dans data/interim (lecture du xlsx : quelques minutes)."""
    src = cfg["sources"]["score"]
    fichier = _chemin(cfg, "fichier")
    if not fichier.exists():
        raise FileNotFoundError(f"{fichier} absent : temps_score non calculé")
    st = fichier.stat()
    cle = hashlib.sha1(f"{fichier.name}|{st.st_size}|{st.st_mtime_ns}|{src['annee']}".encode()).hexdigest()[:10]
    cache = config.chemin(cfg, "intermediaire") / f"{nom_cache}_{src['annee']}_{cle}.pkl"
    if cache.exists():
        donnees = pickle.loads(cache.read_bytes())
    else:
        log.info("lecture de %s (année %s)…", fichier.name, src["annee"])
        donnees = lire(fichier, int(src["annee"]))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps(donnees))
    modifie = date.fromtimestamp(st.st_mtime).isoformat()
    return {"donnees": donnees, "iata": table_iata(_chemin(cfg, "couples")),
            "description": f"{src['nom']} ({fichier.name}, année {src['annee']})", "date_consultation": modifie}


def charger(cfg: dict) -> dict:
    """Temps médians de l'année configurée par couple de gares, deux sens réunis."""
    d = _charger(cfg, "score", lire_temps)
    d["temps"] = d.pop("donnees")
    return d


def charger_sous_relations(cfg: dict) -> dict:
    """Dessertes et temps par relation, sous-relation, montée et descente (voir lire_sous_relations)."""
    d = _charger(cfg, "score_srela_v5", lire_sous_relations)
    d.update(d.pop("donnees"))
    return d
