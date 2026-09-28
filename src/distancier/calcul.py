"""Orchestration : référentiel de gares, moteurs de calcul, tableau des relations."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import pickle
from pathlib import Path

import networkx as nx
import yaml

from distancier import config
from distancier.reseau import Raccordement, construire_graphe
from distancier.routage import Resultat, Routeur

log = logging.getLogger(__name__)


def lire_csv(chemin: Path) -> list[dict]:
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f, delimiter=";")]


def charger_corrections(cfg: dict) -> dict:
    p = config.chemin(cfg, "corrections")
    c = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {}
    racc = {"sncf": [], "rinf": []}
    for r in c.get("raccordements") or []:
        racc[r.get("source", "sncf")].append(
            Raccordement(r["nom"], tuple(r["de"]), tuple(r["a"]), r.get("v_max"), r.get("note", "")))
    return {"raccordements": racc, "lignes_exclues": {str(x) for x in c.get("lignes_exclues") or []}}


# --- moteurs -----------------------------------------------------------------------

class MoteurSncf:
    nom = "sncf"

    def __init__(self, cfg: dict, jour: str | None, corrections: dict):
        from distancier.sources import sncf
        d = sncf.charger(cfg, jour)
        self.gares = d["gares"]
        self.source = sncf.description_source(d["manifeste"])
        self.date = sncf.date_consultation(d["manifeste"])
        self.vitesses = sorted({v.v_max for v in d["vitesses"] if v.v_max is not None})
        troncons = [t for t in d["troncons"] if t.ligne not in corrections["lignes_exclues"]]
        params = dict(cfg["reseau"], crs=cfg["crs_metrique"])
        racc = corrections["raccordements"]["sncf"]
        cle = hashlib.sha1(json.dumps([params, [r.__dict__ for r in racc], sorted(corrections["lignes_exclues"])],
                                      sort_keys=True, default=str).encode()).hexdigest()[:10]
        cache = config.chemin(cfg, "intermediaire") / f"graphe_sncf_{d['jour']}_{cle}.pkl"
        if cache.exists():
            G = pickle.loads(cache.read_bytes())
        else:
            log.info("construction du graphe SNCF (quelques minutes)…")
            G = construire_graphe(troncons, d["vitesses"], raccordements=racc, **params)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(pickle.dumps(G))
        self.routeur = Routeur(G, **cfg["routage"], **cfg["reseau"])

    def completer_gare(self, g: dict) -> None:
        ref = self.gares.get(g["uic"])
        if ref:
            g.setdefault("pks", ref["pks"])
            if not g.get("lon") and ref["lon"] is not None:
                g["lon"], g["lat"] = ref["lon"], ref["lat"]

    def calculer(self, go: dict, gd: dict, mode: str) -> tuple[Resultat, list[str]]:
        notes = []
        for g in (go, gd):
            if not g.get("lon"):
                raise LookupError(f"{g['nom']} : absente du jeu SNCF des gares, saisir lat/lon dans config/gares.csv")
        try:
            for tag, g in (("O", go), ("D", gd)):
                dist = self.routeur.rattacher(tag, g["lon"], g["lat"], tuple(g.get("pks", {})))
                if dist > 100:
                    notes.append(f"{g['nom']} rattachée à la voie à {dist:.0f} m")
            return self.routeur.chemin("O", "D", mode), notes
        finally:
            self.routeur.detacher("O", "D")

    def controle_pk(self, go: dict, gd: dict, res: Resultat, part_min_pct: float):
        ligne, km = res.ligne_dominante()
        pko, pkd = go.get("pks", {}).get(ligne), gd.get("pks", {}).get(ligne)
        if pko is None or pkd is None or km < res.km * part_min_pct / 100:
            return None
        return abs(pkd - pko)


class MoteurRinf:
    nom = "rinf"

    def __init__(self, cfg: dict, jour: str | None, corrections: dict):
        from distancier.sources import rinf
        self._rinf = rinf
        d = rinf.charger(cfg, jour, corrections["raccordements"]["rinf"])
        self.source = rinf.description_source(d["manifeste"])
        self.date = rinf.date_consultation(d["manifeste"])
        self.vitesses = d["vitesses"]
        self.rayon = cfg["sources"]["rinf"]["rayon_recherche_gare_m"]
        self.routeur = Routeur(d["graphe"], **cfg["routage"])

    def point(self, g: dict) -> tuple[str, list[str]]:
        G = self.routeur.G
        if g.get("uopid_rinf"):
            if g["uopid_rinf"] not in G:
                raise LookupError(f"uopid {g['uopid_rinf']} absent du RINF téléchargé")
            return g["uopid_rinf"], []
        if not g.get("lon"):
            raise LookupError(f"{g['nom']} : ni uopid_rinf ni coordonnées dans config/gares.csv")
        n, dist = self._rinf.point_le_plus_proche(G, g["lon"], g["lat"], self.rayon, g["nom"])
        return n, [f"{g['nom']} associée au point RINF {n} ({G.nodes[n].get('nom')}, à {dist:.0f} m) : "
                   f"renseigner uopid_rinf pour figer"]

    def calculer(self, go: dict, gd: dict, mode: str) -> tuple[Resultat, list[str]]:
        o, n1 = self.point(go)
        d, n2 = self.point(gd)
        return self.routeur.chemin(o, d, mode), n1 + n2


# --- calcul ------------------------------------------------------------------------

def referentiel_gares(cfg: dict) -> dict[str, dict]:
    gares = {}
    for r in lire_csv(config.chemin(cfg, "gares")):
        g = {"uic": r["uic"], "nom": r["nom"], "pays": r["pays"], "uopid_rinf": r.get("uopid_rinf", ""),
             "note": r.get("note", "")}
        if r.get("lat") and r.get("lon"):
            g["lat"], g["lon"] = float(r["lat"]), float(r["lon"])
        gares[r["uic"]] = g
    return gares


def calculer(cfg: dict, jour_sncf: str | None = None, jour_rinf: str | None = None) -> dict:
    corrections = charger_corrections(cfg)
    moteurs = {}
    for nom, classe, jour in (("sncf", MoteurSncf, jour_sncf), ("rinf", MoteurRinf, jour_rinf)):
        if nom not in cfg["moteurs"].values():
            continue
        try:
            moteurs[nom] = classe(cfg, jour, corrections)
        except FileNotFoundError as e:
            log.warning("moteur %s indisponible : %s", nom, e)
    if not moteurs:
        raise RuntimeError("aucune donnée source : lancer d'abord « distancier telecharger »")

    gares = referentiel_gares(cfg)
    if "sncf" in moteurs:
        for g in gares.values():
            if g["pays"] == "FR":
                moteurs["sncf"].completer_gare(g)

    rt = cfg["routage"]
    lignes = []
    for rel in lire_csv(config.chemin(cfg, "relations")):
        lignes.append(_relation(rel, gares, moteurs, cfg, rt))
    vitesses = sorted({v for m in moteurs.values() for v in m.vitesses}, reverse=True)
    return {"relations": lignes, "vitesses": vitesses,
            "sources": {n: {"description": m.source, "date_consultation": m.date} for n, m in moteurs.items()}}


def _relation(rel: dict, gares: dict, moteurs: dict, cfg: dict, rt: dict) -> dict:
    go, gd = gares.get(rel["uic_origine"]), gares.get(rel["uic_destination"])
    ligne = {"id": rel["id"], "itineraire_retenu": rel["itineraire"], "remarques": []}
    if rel.get("remarque"):
        ligne["remarques"].append(rel["remarque"])
    if not go or not gd:
        manquant = rel["uic_origine"] if not go else rel["uic_destination"]
        return {**ligne, "statut": f"erreur : UIC {manquant} absent de config/gares.csv"}
    ligne.update(gare_origine=go["nom"], code_uic_origine=go["uic"], pays_origine=go["pays"],
                 gare_destination=gd["nom"], code_uic_destination=gd["uic"], pays_destination=gd["pays"])
    for g in (go, gd):
        if g.get("note"):
            ligne["remarques"].append(f"{g['nom']} : {g['note']}")

    tous_sncf = cfg["moteurs"].get(go["pays"]) == "sncf" and cfg["moteurs"].get(gd["pays"]) == "sncf"
    nom_principal = "sncf" if tous_sncf else "rinf"
    principal = moteurs.get(nom_principal)
    if principal is None:
        return {**ligne, "statut": f"erreur : données {nom_principal} non téléchargées"}
    repli = False
    try:
        res, notes = principal.calculer(go, gd, rel["itineraire"])
    except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e:
        # Gare hors du réseau SNCF (ligne absente des tracés) : repli sur RINF, sans contrôle.
        if nom_principal != "sncf" or "rinf" not in moteurs:
            return {**ligne, "statut": f"erreur : {e}"}
        nom_principal, principal, repli = "rinf", moteurs["rinf"], True
        try:
            res, notes = principal.calculer(go, gd, rel["itineraire"])
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e2:
            return {**ligne, "statut": f"erreur : SNCF : {e} ; RINF : {e2}"}
        notes.insert(0, f"calcul SNCF impossible ({e}) : distance RINF, sans contrôle SNCF")
    ligne["moteur"] = nom_principal
    ligne["remarques"] += notes
    ligne["resultat"] = res
    ligne["source"] = principal.source
    ligne["date_consultation"] = principal.date
    for mode, col in (("plus_court", "distance_plus_courte_km"), ("sans_lgv", "distance_sans_lgv_km")):
        try:
            ligne[col] = round(principal.calculer(go, gd, mode)[0].km, 1)
        except (nx.NetworkXNoPath, LookupError):
            ligne[col] = ""
    if rel["itineraire"] == "grande_vitesse" and ligne["distance_plus_courte_km"] != "" \
            and ligne["distance_plus_courte_km"] < res.km - 0.5:
        ligne["remarques"].append(f"itinéraire LGV privilégié ; le plus court chemin fait {ligne['distance_plus_courte_km']} km")

    verifie = []
    autre = None if repli else moteurs.get("rinf" if nom_principal == "sncf" else "sncf")
    if autre is not None and (autre.nom == "rinf" or tous_sncf):
        try:
            ctl, _ = autre.calculer(go, gd, rel["itineraire"])
            ecart = 100 * (res.km / ctl.km - 1)
            ligne.update(distance_controle_km=round(ctl.km, 1), source_controle=autre.nom,
                         ecart_controle_pct=round(ecart, 2))
            if abs(ecart) <= rt["seuil_verification_pct"]:
                verifie.append(f"vérifié ({autre.nom.upper()})")
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e:
            ligne["remarques"].append(f"contrôle {autre.nom} impossible : {e}")
    if nom_principal == "sncf":
        pk = principal.controle_pk(go, gd, res, rt["part_ligne_controle_pk_pct"])
        if pk:
            ecart = 100 * (res.km / pk - 1)
            ligne.update(controle_pk_km=round(pk, 1), ecart_pk_pct=round(ecart, 2))
            if abs(ecart) <= rt["seuil_verification_pct"]:
                verifie.append("vérifié (PK SNCF)")

    if res.manuels:
        ligne["remarques"].append("emprunte un raccordement ajouté à la main : " + ", ".join(res.manuels))
    if rel.get("statut_force"):
        ligne["statut"] = rel["statut_force"]
    elif res.manuels or repli:
        ligne["statut"] = "à vérifier"
    elif verifie:
        ligne["statut"] = verifie[0]
    else:
        ligne["statut"] = "estimé"
    return ligne
