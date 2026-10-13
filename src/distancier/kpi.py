"""KPI par vitesse maximale (distancier kpi, demande d'Aloïs le 2026-10-13).

Pour chaque v_max des tracés SNCF Réseau (réseau exploité, celui du graphe de calcul des distances) :
nombre de lignes (codes ligne) concernées, km, part des km du réseau, km en voie unique, en voie
double ou plus et non renseignés (régime d'exploitation SNCF), et trains-km de l'année configurée :
pour chaque train d'Extract_score, nombre_jour × compteur × km à cette vitesse entre deux arrêts
consécutifs (année sources.score.annee), sur l'itinéraire du tableau par sous-relation (LGV ou ligne classique selon le temps),
partie française seulement (gares étrangères ramenées au point frontière, sinon non comptées).
"""
from __future__ import annotations

import collections
import csv
import json
import logging
from datetime import date
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import transform
from shapely.strtree import STRtree

log = logging.getLogger(__name__)

CATEGORIES_VOIES = ("voie_unique", "voie_double_ou_plus", "voie_non_renseignee")
COLONNES = ["v_max_kmh", "nb_lignes", "km", "part_reseau_pct", "km_voie_unique", "km_voie_double_ou_plus",
            "km_voie_non_renseignee", "tkm", "part_tkm_pct", "source", "date_consultation"]


def _categorie(regime: str | None, cfg: dict) -> str:
    for cat, libelles in cfg["kpi"]["regimes"].items():
        if regime in libelles:
            return cat
    return "voie_non_renseignee"


def reseau(G, regimes: list | None, cfg: dict) -> dict:
    """{v_max: {"lignes": set, "km": float, <catégorie de voies>: km}} sur les arêtes du graphe SNCF
    (hors raccordements ajoutés à la main et arêtes dupliquées par un virage interdit). La vitesse est
    celle du profil de l'arête (un point tous les pas_echantillonnage_vitesse_m) ; le régime est lu aux
    mêmes points, sur la même ligne d'abord, comme la vitesse."""
    tol = cfg["reseau"]["tolerance_vitesse_m"]
    rgeoms = rarbre = None
    if regimes:
        vers_m = Transformer.from_crs(4326, G.graph.get("crs") or cfg["crs_metrique"], always_xy=True).transform
        rgeoms = [transform(vers_m, r.geom) for r in regimes]
        rarbre = STRtree(rgeoms)

    def regime(ligne: str, p: Point) -> str | None:
        best = None
        for j in rarbre.query(p.buffer(tol)):
            d = rgeoms[j].distance(p)
            if d <= tol and (best is None or (regimes[j].ligne != ligne, d) < best[0]):
                best = ((regimes[j].ligne != ligne, d), regimes[j].v_max)
        return best[1] if best else None

    kpi: dict = collections.defaultdict(lambda: {"lignes": set(), "km": 0.0, **{c: 0.0 for c in CATEGORIES_VOIES}})
    for _, _, d in G.edges(data=True):
        if d.get("manuel") or d.get("doublon") or "geom" not in d:
            continue
        n = len(d["profil"])
        km = d["km"] / n
        for k, v in enumerate(d["profil"]):
            cat = "voie_non_renseignee"
            if rarbre is not None:
                p = d["geom"].interpolate((k + 0.5) * d["geom"].length / n)
                cat = _categorie(regime(d["ligne"], p), cfg)
            x = kpi[v]
            x["lignes"].add(d["ligne"])
            x["km"] += km
            x[cat] += km
    return dict(kpi)


def trains_km(d: dict, ctx: dict) -> tuple[dict, dict]:
    """({v_max: trains-km}, bilan) : passages entre arrêts consécutifs (score.lire_sous_relations) ×
    km par vitesse du tronçon, calculé comme dans le tableau par sous-relation."""
    from distancier.calcul import _frontieres, _troncons_desserte
    tkm: dict = collections.Counter()
    bilan = {"passages": 0.0, "passages_non_comptes": 0.0, "non_comptes": collections.Counter(), "moteurs": collections.Counter()}
    for (rel, srel, a, b), poids in sorted(d["passages"].items()):
        bilan["passages"] += poids
        prep = _frontieres((a, b), ctx)
        troncons = None
        if isinstance(prep, str):
            motif = prep
        else:
            desserte, uics, frontiere, _ = prep
            troncons, _, remarques = _troncons_desserte(desserte, uics, frontiere, rel, srel, ctx)
            motif = remarques[-1] if remarques else "tronçon impossible"
        if not troncons:
            bilan["passages_non_comptes"] += poids
            bilan["non_comptes"][motif.split(" (")[0]] += poids
            continue
        for t in troncons:
            bilan["moteurs"][t["moteur"]] += poids * t["resultat"].km
            for v, km in t["resultat"].vitesses.items():
                tkm[v] += poids * km
    return dict(tkm), bilan


def calculer(cfg: dict, jour_sncf: str | None = None, jour_rinf: str | None = None) -> dict:
    from distancier.calcul import contexte_sous_relations
    from distancier.sources import sncf
    moteurs, sources, d, ctx = contexte_sous_relations(cfg, jour_sncf, jour_rinf)
    try:
        r = sncf.charger_regimes(cfg, jour_sncf)
    except FileNotFoundError as e:
        log.warning("%s : km de voies non renseignés", e)
        regimes = None
    else:
        regimes = r["regimes"]
        sources["regimes"] = {"description": r["description"], "date_consultation": r["date_consultation"]}
    res = reseau(moteurs["sncf"].routeur.G, regimes, cfg)
    tkm, bilan = trains_km(d, ctx)
    annee = cfg["sources"]["score"]["annee"]

    total_km = sum(x["km"] for x in res.values())
    total_tkm = sum(tkm.values())
    # la description SNCF liste déjà le jeu des régimes (même manifeste) ; sa date peut différer
    source = " ; ".join(sources[k]["description"] for k in ("sncf", "score") if k in sources)
    date_c = f"SNCF {sources['sncf']['date_consultation']}"
    if "regimes" in sources and sources["regimes"]["date_consultation"] != sources["sncf"]["date_consultation"]:
        date_c += f", régimes d'exploitation {sources['regimes']['date_consultation']}"
    if "score" in sources:
        date_c += f", Extract_score {sources['score']['date_consultation']}"
    lignes = []
    for v in sorted(set(res) | set(tkm), key=lambda v: (v is None, -(v or 0))):
        x = res.get(v)
        lignes.append({"v_max_kmh": v if v is not None else "inconnue",
                       "nb_lignes": len(x["lignes"]) if x else 0,
                       "km": x["km"] if x else 0.0,
                       "part_reseau_pct": 100 * x["km"] / total_km if x and total_km else 0.0,
                       **{f"km_{c}": x[c] if x else 0.0 for c in CATEGORIES_VOIES},
                       "tkm": tkm.get(v, 0.0),
                       "part_tkm_pct": 100 * tkm.get(v, 0.0) / total_tkm if total_tkm else 0.0})
    lignes.append({"v_max_kmh": "total", "nb_lignes": len(set().union(*(x["lignes"] for x in res.values()))),
                   "km": total_km, "part_reseau_pct": 100.0 if total_km else 0.0,
                   **{f"km_{c}": sum(x[c] for x in res.values()) for c in CATEGORIES_VOIES},
                   "tkm": total_tkm, "part_tkm_pct": 100.0 if total_tkm else 0.0})
    for l in lignes:
        l.update(source=source, date_consultation=date_c)
    return {"lignes": lignes, "sources": sources, "annee_tkm": int(annee), "bilan_tkm": bilan}


def _arrondi(l: dict) -> dict:
    out = dict(l)
    for c in ("km", "km_voie_unique", "km_voie_double_ou_plus", "km_voie_non_renseignee"):
        out[c] = round(l[c], 1)
    out["tkm"] = round(l["tkm"])
    out["part_reseau_pct"] = round(l["part_reseau_pct"], 2)
    out["part_tkm_pct"] = round(l["part_tkm_pct"], 2)
    return out


def ecrire(resultat: dict, dossier: Path, suffixe: str | None = None) -> list[Path]:
    """kpi_vitesse_<date>.csv (`;`, UTF-8 avec BOM), .xlsx et .json (sources, trains-km non comptés)."""
    dossier.mkdir(parents=True, exist_ok=True)
    suffixe = suffixe or date.today().isoformat()
    cols = [c if c != "tkm" else f"tkm_{resultat['annee_tkm']}" for c in COLONNES]
    lignes = [{(f"tkm_{resultat['annee_tkm']}" if k == "tkm" else k): v for k, v in _arrondi(l).items()}
              for l in resultat["lignes"]]
    fichiers = [dossier / f"kpi_vitesse_{suffixe}.csv"]
    with open(fichiers[0], "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        w.writerows(lignes)
    b = resultat["bilan_tkm"]
    meta = {"sources": resultat["sources"], "annee_tkm": resultat["annee_tkm"],
            "circulations_entre_arrets": round(b["passages"], 1),
            "circulations_entre_arrets_non_comptees": round(b["passages_non_comptes"], 1),
            "motifs_non_comptes": {k: round(v, 1) for k, v in b["non_comptes"].most_common()},
            "trains_km_par_moteur": {k: round(v) for k, v in b["moteurs"].items()}}
    fichiers.append(dossier / f"kpi_vitesse_{suffixe}.json")
    fichiers[-1].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from openpyxl import Workbook
    except ImportError:
        return fichiers
    wb = Workbook()
    ws = wb.active
    ws.title = "kpi_vitesse"
    ws.append(cols)
    for l in lignes:
        ws.append([l[c] for c in cols])
    fichiers.append(dossier / f"kpi_vitesse_{suffixe}.xlsx")
    wb.save(fichiers[-1])
    return fichiers
