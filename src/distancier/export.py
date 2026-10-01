"""Mise en forme du tableau des relations (CSV `;` UTF-8 avec BOM, Excel)."""
from __future__ import annotations

import csv
import json
import logging
import math
from datetime import date
from pathlib import Path

from distancier import __version__

log = logging.getLogger(__name__)

COLONNES_DEBUT = ["id", "gare_origine", "code_uic_origine", "pays_origine", "gare_destination",
                  "code_uic_destination", "pays_destination", "itineraire_retenu", "distance_km"]
COLONNES_FIN = ["dont_km_vitesse_inconnue", "part_lgv_pct", "type_ligne", "lignes_empruntees",
                "distance_au_plus_court_km", "distance_tgv_commercial_km", "desserte_tgv",
                "temps_theorique", "temps_theorique_350", "temps_pratique", "temps_score", "temps_score_350",
                "distance_plus_courte_km", "distance_sans_lgv_km", "moteur", "distance_controle_km",
                "source_controle", "ecart_controle_pct", "controle_pk_km", "ecart_pk_pct",
                "statut", "source", "date_consultation", "remarques"]

# tableau par relation, sous-relation, montée et descente du plan de transport (distancier calculer --par-sous-relation)
COLONNES_SRELA_DEBUT = ["id", "relation", "sous_relation", "montee_iata", "descente_iata", "gare_origine",
                        "code_uic_origine", "gare_destination", "code_uic_destination", "itineraire_retenu", "distance_km"]
COLONNES_SRELA_FIN = ["dont_km_vitesse_inconnue", "part_lgv_pct", "type_ligne", "lignes_empruntees",
                      "distance_au_plus_court_km", "desserte_score", "part_desserte_pct", "circulations_annee",
                      "troncons_sans_lgv", "temps_theorique", "temps_theorique_350", "temps_score_min", "temps_score_max",
                      "temps_score", "temps_score_350", "nb_arret_inter_min", "nb_arret_inter_max", "nb_arret_inter",
                      "distance_plus_courte_km", "distance_sans_lgv_km", "moteur", "distance_controle_km",
                      "source_controle", "ecart_controle_pct", "controle_pk_km", "ecart_pk_pct",
                      "statut", "source", "date_consultation", "remarques"]


def colonnes(vitesses: list[int], debut: list[str] = COLONNES_DEBUT, fin: list[str] = COLONNES_FIN) -> list[str]:
    return debut + [f"dont_km_{v}" for v in vitesses] + fin


def arrondir_somme(valeurs: dict, total: float, decimales: int = 1) -> dict:
    """Arrondit chaque valeur de sorte que la somme des arrondis égale total arrondi."""
    f = 10 ** decimales
    cible = round(total * f)
    bas = {k: math.floor(v * f) for k, v in valeurs.items()}
    reste = cible - sum(bas.values())
    for k in sorted(valeurs, key=lambda k: valeurs[k] * f - bas[k], reverse=True)[:max(0, reste)]:
        bas[k] += 1
    return {k: v / f for k, v in bas.items()}


def resume_lignes(lignes: list, km_min: float = 1.0) -> str:
    """« 830000:9.3 > 752000:600.7 » ; les passages de moins de km_min sont omis."""
    garde = []
    for ligne, km in lignes:
        if km < km_min:
            continue
        if garde and garde[-1][0] == ligne:
            garde[-1] = (ligne, garde[-1][1] + km)
        else:
            garde.append((ligne, km))
    return " > ".join(f"{l or '?'}:{k:.1f}" for l, k in garde)


def mettre_en_forme(ligne: dict, vitesses: list[int], rt: dict) -> dict:
    out = {k: v for k, v in ligne.items() if k not in ("resultat", "remarques")}
    out["remarques"] = " ; ".join(ligne.get("remarques", []))
    res = ligne.get("resultat")
    if res is None:
        return out
    km = round(res.km, 1)
    par_v = arrondir_somme(res.vitesses, res.km)
    out["distance_km"] = km
    for v in vitesses:
        out[f"dont_km_{v}"] = par_v.get(v, 0.0)
    out["dont_km_vitesse_inconnue"] = par_v.get(None, 0.0)
    part = 100 * res.km_lgv(rt["seuil_lgv_kmh"]) / res.km if res.km else 0.0
    out["part_lgv_pct"] = round(part, 1)
    seuil = rt["part_lgv_type_lgv_pct"]
    out["type_ligne"] = "LGV" if part >= seuil else ("classique" if part < 100 - seuil else "mixte")
    out["lignes_empruntees"] = resume_lignes(res.lignes)
    return out


def ecrire(resultat: dict, cfg: dict, dossier: Path, suffixe: str | None = None, par_sous_relation: bool = False) -> list[Path]:
    dossier.mkdir(parents=True, exist_ok=True)
    suffixe = suffixe or date.today().isoformat()
    if par_sous_relation:
        suffixe = f"srela_{suffixe}"
    vitesses = resultat["vitesses"]
    cols = colonnes(vitesses, *((COLONNES_SRELA_DEBUT, COLONNES_SRELA_FIN) if par_sous_relation else ()))
    lignes = [mettre_en_forme(l, vitesses, cfg["routage"]) for l in resultat["relations"]]
    fichiers = []

    principal = dossier / f"distances_{suffixe}.csv"
    with open(principal, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(lignes)
    fichiers.append(principal)

    long_cols = ["id", "gare_origine", "gare_destination", "v_max_kmh", "km", "part_pct", "source", "date_consultation"]
    long_lignes = []
    for l, brut in zip(lignes, resultat["relations"]):
        res = brut.get("resultat")
        if res is None:
            continue
        for v, km in sorted(arrondir_somme(res.vitesses, res.km).items(), key=lambda t: -(t[0] or 0)):
            if km > 0:
                long_lignes.append({"id": l["id"], "gare_origine": l["gare_origine"], "gare_destination": l["gare_destination"],
                                    "v_max_kmh": v if v is not None else "inconnue", "km": km,
                                    "part_pct": round(100 * km / res.km, 1), "source": l["source"],
                                    "date_consultation": l["date_consultation"]})
    par_vitesse = dossier / f"distances_par_vitesse_{suffixe}.csv"
    with open(par_vitesse, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=long_cols, delimiter=";")
        w.writeheader()
        w.writerows(long_lignes)
    fichiers.append(par_vitesse)

    try:
        from openpyxl import Workbook
        wb = Workbook()
        for ws, entetes, donnees in ((wb.active, cols, lignes), (wb.create_sheet(), long_cols, long_lignes)):
            ws.append(entetes)
            for d in donnees:
                ws.append([d.get(c, "") for c in entetes])
            ws.freeze_panes = "B2"
            ws.auto_filter.ref = ws.dimensions
        wb.worksheets[0].title, wb.worksheets[1].title = "distances", "par_vitesse"
        xlsx = dossier / f"distances_{suffixe}.xlsx"
        wb.save(xlsx)
        fichiers.append(xlsx)
    except ImportError:
        log.info("openpyxl absent : pas d'export Excel")

    meta = dossier / f"calcul_{suffixe}.json"
    meta.write_text(json.dumps({"version": __version__, "sources": resultat["sources"],
                                "parametres": {"reseau": cfg["reseau"], "routage": cfg["routage"],
                                               "moteurs": cfg["moteurs"], "crs_metrique": cfg["crs_metrique"]}},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    fichiers.append(meta)
    return fichiers
