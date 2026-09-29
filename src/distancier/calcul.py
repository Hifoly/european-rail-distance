"""Orchestration : référentiel de gares, moteurs de calcul, tableau des relations."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import pickle
import re
from pathlib import Path

import networkx as nx
import yaml

from distancier import config
from distancier.reseau import Raccordement, VirageInterdit, construire_graphe, km_par_vitesse
from distancier.routage import Resultat, Routeur

log = logging.getLogger(__name__)


def lire_csv(chemin: Path) -> list[dict]:
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f, delimiter=";")]


def charger_corrections(cfg: dict) -> dict:
    p = config.chemin(cfg, "corrections")
    c = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {}
    racc = {"sncf": [], "rinf": []}
    valides = {}
    for r in c.get("raccordements") or []:
        racc[r.get("source", "sncf")].append(
            Raccordement(r["nom"], tuple(r["de"]), tuple(r["a"]), r.get("v_max"), r.get("note", "")))
        if r.get("valide"):  # raccord vérifié sur une carte : ne suffit plus à rendre la relation « à vérifier »
            valides[r["nom"]] = str(r["valide"])
    compl = {str(x["code"] if isinstance(x, dict) else x) for x in c.get("lignes_complementaires") or []}
    lignes_gares = {str(u): tuple(str(l) for l in (ls if isinstance(ls, list) else [ls]))
                    for u, ls in (c.get("lignes_gares") or {}).items()}
    virages = [VirageInterdit(v["nom"], tuple(v["point"]), str(v["de"]), str(v["vers"]), v.get("note", ""),
                              bool(v.get("separer")))
               for v in c.get("virages_interdits") or []]
    return {"raccordements": racc, "valides": valides, "virages_interdits": virages, "lignes_complementaires": compl, "lignes_gares": lignes_gares,
            "lignes_exclues": {str(x) for x in c.get("lignes_exclues") or []}}


# --- moteurs -----------------------------------------------------------------------

class MoteurSncf:
    nom = "sncf"

    def __init__(self, cfg: dict, jour: str | None, corrections: dict):
        from distancier.sources import sncf
        self.complementaires = corrections.get("lignes_complementaires", set())
        self.lignes_gares = corrections.get("lignes_gares", {})
        d = sncf.charger(cfg, jour, self.complementaires)
        self.gares = d["gares"]
        self.source = sncf.description_source(d["manifeste"])
        self.date = sncf.date_consultation(d["manifeste"])
        self.vitesses = sorted({v.v_max for v in d["vitesses"] if v.v_max is not None})
        troncons = [t for t in d["troncons"] if t.ligne not in corrections["lignes_exclues"]]
        params = dict(cfg["reseau"], crs=cfg["crs_metrique"])
        racc = corrections["raccordements"]["sncf"]
        virages = corrections.get("virages_interdits", [])
        cle = hashlib.sha1(json.dumps([params, [r.__dict__ for r in racc], sorted(corrections["lignes_exclues"]),
                                       sorted(self.complementaires)] + ([[v.__dict__ for v in virages]] if virages else []),
                                      sort_keys=True, default=str).encode()).hexdigest()[:10]
        cache = config.chemin(cfg, "intermediaire") / f"graphe_sncf_{d['jour']}_{cle}.pkl"
        if cache.exists():
            G = pickle.loads(cache.read_bytes())
        else:
            log.info("construction du graphe SNCF (quelques minutes)…")
            G = construire_graphe(troncons, d["vitesses"], raccordements=racc, virages_interdits=virages, **params)
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
                preferees = tuple(g.get("pks", {})) + self.lignes_gares.get(g["uic"], ())
                dist = self.routeur.rattacher(tag, g["lon"], g["lat"], preferees)
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

    def calculer(self, go: dict, gd: dict, mode: str, lignes=None) -> tuple[Resultat, list[str]]:
        o, n1 = self.point(go)
        d, n2 = self.point(gd)
        return self.routeur.chemin(o, d, mode, lignes), n1 + n2


class MoteurAdif:
    """Distances de la carte Adif (km entiers entre nœuds) : contrôle et repli en Espagne."""
    nom = "adif"

    def __init__(self, cfg: dict, jour: str | None, corrections: dict):
        from distancier.sources import adif
        self._geo = adif.geodesique_km
        d = adif.charger(cfg)
        src = cfg["sources"]["adif"]
        self.points, self.coords = d["points"], d["coords"]
        self.source, self.date = adif.SOURCE, adif.DATE
        self.vitesses = sorted({v for _, _, e in d["graphe"].edges(data=True) for v in e["profil"] if v is not None})
        # km entiers : chaque tronçon traversé peut être arrondi de 0,5 km
        self.marge_arete_km = float(src.get("marge_arrondi_km", 0.5))
        self.detour_max = float(src.get("interpolation_detour_max", 1.6))
        self._places = {}   # gare placée -> (tronçon, bout de référence, position 0-1)
        self.routeur = Routeur(d["graphe"], **cfg["routage"])

    def _placer(self, tag: str, g: dict) -> str:
        """Place une gare absente de la carte sur le tronçon dont elle s'écarte le moins."""
        if g.get("lon") is None:
            raise LookupError(f"{g['nom']} absente de la carte Adif (sans coordonnées)")
        p = (g["lon"], g["lat"])
        av = re.search(r"\bAV\b|Alta Velocidad", g["nom"]) is not None
        meilleur = None
        for u, v, k, d in self.routeur.G.edges(keys=True, data=True):
            if u not in self.coords or v not in self.coords or d["km"] <= 0 or d.get("temporaire"):
                continue
            du, dv = self._geo(self.coords[u], p), self._geo(p, self.coords[v])
            duv = self._geo(self.coords[u], self.coords[v])
            if duv <= 0 or duv > d["km"] * 1.02 + 2:   # coordonnées incohérentes avec les km de la carte
                continue
            # gare « AV » sur le calque AV, les autres sur les lignes classiques (LAV et ligne classique voisines)
            penalite = 0.1 if bool(d.get("lgv_presumee")) != av else 0.0
            rapport = (du + dv) / duv
            if rapport > self.detour_max:
                continue
            cle = (rapport + penalite, d["km"])
            if meilleur is None or cle < meilleur[0]:
                meilleur = (cle, u, v, d, du / (du + dv))
        if meilleur is None:
            raise LookupError(f"{g['nom']} absente de la carte Adif (aucun tronçon voisin)")
        _, u, v, d, t = meilleur
        self.routeur._ajouter(tag, u, d, 0.0, t)
        self.routeur._ajouter(tag, v, d, t, 1.0)
        # deux gares sur le même tronçon : relier directement
        for autre, (d2, u2, t2) in self._places.items():
            if d2 is d and autre in self.routeur.G:
                t2 = t2 if u2 == u else 1.0 - t2
                self.routeur._ajouter(tag, autre, d, min(t, t2), max(t, t2))
        self._places[tag] = (d, u, t)
        self.coords[tag] = p
        return f"{g['nom']} absente de la carte Adif : placée entre {u} et {v} (distance approchée)"

    def calculer(self, go: dict, gd: dict, mode: str, lignes=None) -> tuple[Resultat, list[str]]:
        noeuds, notes = [], []
        try:
            for tag, g in (("O", go), ("D", gd)):
                n = self.points.get(g.get("uopid_rinf") or "") or self.points.get(g["uic"])
                if n is None:
                    notes.append(self._placer(tag, g))
                    n = tag
                noeuds.append(n)
            res = self.routeur.chemin(noeuds[0], noeuds[1], mode)
            res.approche = bool(notes)
            return res, notes
        finally:
            self.routeur.detacher("O", "D")
            self._places.clear()
            self.coords.pop("O", None)
            self.coords.pop("D", None)


def vitesses_adif_sur_rinf(rinf: "MoteurRinf", adif: MoteurAdif, tolerance_pct: float = 10.0) -> float:
    """Reporte la vitesse de la carte 3 Adif sur les sections RINF de LAV sans vitesse (choix d'Aloïs
    le 2026-09-29). Pour chaque tronçon (ou enchaînement de tronçons) AV de la carte entre deux points
    RINF, le plus court chemin RINF entre ces points (longueur à `tolerance_pct` près de celle de la carte)
    donne les sections concernées ; seules celles sans vitesse et présumées LAV sont complétées, et
    marquées `vitesse_adif`. Renvoie les km RINF complétés."""
    G = rinf.routeur.G
    uopid = {}
    for cle, noeud in adif.points.items():
        if cle in G:
            uopid.setdefault(noeud, cle)
    A = adif.routeur.G
    # Tronçons AV à reporter : ceux dont les deux bouts sont des points RINF, et les enchaînements de
    # tronçons AV à travers une bifurcation sans point RINF (ex. Cuenca - Bif. Motilla - Requena), de
    # même vitesse, un tronçon non chiffré de moins de 15 km pouvant s'y glisser.
    troncons = []
    def etendre(chaine, noeuds):
        u, v = noeuds[0], noeuds[-1]
        vits = {d["profil"][0] for d in chaine} - {None}
        muets = sum(d["km"] for d in chaine if d["profil"][0] is None)
        if len(vits) > 1 or muets > 15:
            return
        if u in uopid and v in uopid:
            if vits:
                troncons.append((u, v, sum(d["km"] for d in chaine), vits.pop()))
            return
        if len(chaine) >= 4 or v in uopid:
            return
        for w, dd in A[v].items():
            for d in dd.values():
                if d.get("lgv_presumee") and not d.get("temporaire") and w not in noeuds:
                    etendre(chaine + [d], noeuds + [w])
    for u, v, d in A.edges(data=True):
        if d.get("lgv_presumee") and not d.get("temporaire"):
            for a, b in ((u, v), (v, u)):
                if a in uopid:
                    etendre([d], [a, b])
    total = 0.0
    for u, v, km_carte, vit in troncons:
        try:
            noeuds = nx.shortest_path(G, uopid[u], uopid[v], weight=lambda a, b, dd: min(x["km"] for x in dd.values()))
        except nx.NetworkXNoPath:
            continue
        aretes = [min(G[a][b].values(), key=lambda x: x["km"]) for a, b in zip(noeuds[:-1], noeuds[1:])]
        km = sum(a["km"] for a in aretes)
        if km_carte <= 0 or abs(km / km_carte - 1) > tolerance_pct / 100:
            continue
        for a in aretes:
            if a["profil"] == [None] and a.get("lgv_presumee") and not a.get("vitesse_adif"):
                a["profil"] = [vit]
                a["vitesses"] = km_par_vitesse(a)
                a["vitesse_adif"] = True
                total += a["km"]
    rinf.vitesses = sorted(set(rinf.vitesses) | set(adif.vitesses))
    return total


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
    if "adif" in (cfg.get("controles") or {}).values() and "adif" in cfg["sources"]:
        moteurs["adif"] = MoteurAdif(cfg, None, corrections)
        if cfg["sources"]["adif"].get("vitesses_lav_sur_rinf") and "rinf" in moteurs:
            km = vitesses_adif_sur_rinf(moteurs["rinf"], moteurs["adif"])
            log.info("vitesse Adif reportée sur %.0f km de LAV RINF sans vitesse", km)
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
        lignes.append(_relation(rel, gares, moteurs, cfg, rt, corrections["valides"]))
    vitesses = sorted({v for m in moteurs.values() for v in m.vitesses}, reverse=True)
    return {"relations": lignes, "vitesses": vitesses,
            "sources": {n: {"description": m.source, "date_consultation": m.date} for n, m in moteurs.items()}}


def _concorde(km: float, controle: float, ecart_pct: float, rt: dict, marge_km: float = 0.0) -> bool:
    """Deux distances concordent si l'écart est sous le seuil en % OU sous le seuil en km
    (trajets courts : la position de la gare sur la voie varie de quelques centaines de mètres).
    `marge_km` s'ajoute au seuil en km (arrondi des km entiers de la carte Adif)."""
    return abs(ecart_pct) <= rt["seuil_verification_pct"] \
        or abs(km - controle) <= rt.get("seuil_verification_km", 0) + marge_km


def _moteur_controle(cfg: dict, moteurs: dict, go: dict, gd: dict) -> str | None:
    """Moteur de contrôle propre au pays (section `controles`), si les deux gares y sont."""
    nom = (cfg.get("controles") or {}).get(go["pays"])
    if nom and nom == (cfg.get("controles") or {}).get(gd["pays"]) and nom in moteurs:
        return nom
    return None


def _relation(rel: dict, gares: dict, moteurs: dict, cfg: dict, rt: dict, valides: dict | None = None) -> dict:
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
    nom_ctl = None if nom_principal == "sncf" else _moteur_controle(cfg, moteurs, go, gd)
    echec_sncf = None
    try:
        res, notes = principal.calculer(go, gd, rel["itineraire"])
    except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e:
        if nom_ctl is not None:
            # Trou du RINF (ex. LAV espagnoles incomplètes) : distance du moteur de contrôle, sans contrôle.
            try:
                res, notes = moteurs[nom_ctl].calculer(go, gd, rel["itineraire"])
            except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e2:
                return {**ligne, "statut": f"erreur : RINF : {e} ; {nom_ctl.upper()} : {e2}"}
            notes.insert(0, f"calcul RINF impossible ({e}) : distance {nom_ctl.upper()}, sans contrôle RINF")
            nom_principal, principal, repli = nom_ctl, moteurs[nom_ctl], True
        elif nom_principal != "sncf" or "rinf" not in moteurs:
            return {**ligne, "statut": f"erreur : {e}"}
        else:
            echec_sncf = e
    if echec_sncf is not None:
        e = echec_sncf
        # Gare hors du réseau SNCF (ligne absente des tracés) : repli sur RINF, sans contrôle.
        nom_principal, principal, repli = "rinf", moteurs["rinf"], True
        try:
            res, notes = principal.calculer(go, gd, rel["itineraire"])
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e2:
            return {**ligne, "statut": f"erreur : SNCF : {e} ; RINF : {e2}"}
        notes.insert(0, f"calcul SNCF impossible ({e}) : distance RINF, sans contrôle SNCF")
    # Itinéraire « grande vitesse » trop long par rapport au plus court chemin (ex. demi-tour
    # après la LGV) : on garde le plus court chemin du même moteur.
    plafond = rt.get("plafond_detour_lgv_pct")
    if plafond is not None and rel["itineraire"] == "grande_vitesse":
        try:
            court = principal.calculer(go, gd, "plus_court")[0]
            if court.km > 0 and res.km > court.km * (1 + plafond / 100):
                notes.append(f"itinéraire LGV plus long de {100 * (res.km / court.km - 1):+.1f} % que le plus court "
                             f"chemin ({res.km:.1f} km) : plus court chemin retenu")
                res = court
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError):
            pass
    # Détour SNCF nettement plus long que RINF : ligne probablement absente des tracés SNCF.
    ctl_rinf = None
    if not repli and nom_principal == "sncf" and "rinf" in moteurs:
        try:
            ctl_rinf = moteurs["rinf"].calculer(go, gd, rel["itineraire"])[0]
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError):
            pass
        if ctl_rinf is not None and ctl_rinf.km > 0 \
                and res.km > ctl_rinf.km * (1 + rt.get("seuil_alerte_pct", math.inf) / 100):
            detour = 100 * (res.km / ctl_rinf.km - 1)
            notes = [f"détour SNCF de {detour:+.1f} % ({res.km:.1f} km, ligne probablement absente des tracés) : "
                     f"distance RINF, sans contrôle SNCF"] + moteurs["rinf"].calculer(go, gd, rel["itineraire"])[1]
            nom_principal, principal, repli, res = "rinf", moteurs["rinf"], True, ctl_rinf
    # Détour RINF nettement plus long que le contrôle du pays (carte Adif) : trou probable du RINF.
    ctl_pays, notes_ctl = None, []
    if not repli and nom_ctl is not None:
        try:
            ctl_pays, notes_ctl = moteurs[nom_ctl].calculer(go, gd, rel["itineraire"])
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError):
            pass
        # distance Adif approchée (gare placée sur la carte) : repli seulement au-delà d'un seuil plus large
        seuil = rt.get("seuil_alerte_approche_pct" if ctl_pays is not None and ctl_pays.approche
                       else "seuil_alerte_pct", math.inf)
        if ctl_pays is not None and ctl_pays.km > 0 and res.km > ctl_pays.km * (1 + seuil / 100):
            detour = 100 * (res.km / ctl_pays.km - 1)
            notes = [f"détour RINF de {detour:+.1f} % ({res.km:.1f} km, ligne probablement absente du RINF) : "
                     f"distance {nom_ctl.upper()}, sans contrôle RINF"] + notes_ctl
            nom_principal, principal, repli, res = nom_ctl, moteurs[nom_ctl], True, ctl_pays
    ligne["moteur"] = nom_principal
    ligne["remarques"] += notes
    if res.km_vitesse_adif > 0:
        ligne["remarques"].append(f"vitesse Adif (carte 3) sur {res.km_vitesse_adif:.1f} km de LAV sans vitesse RINF")
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

    verifie, alerte = [], False
    autre = None if repli else moteurs.get(nom_ctl or ("rinf" if nom_principal == "sncf" else "sncf"))
    if autre is not None and (autre.nom in ("rinf", nom_ctl) or tous_sncf):
        try:
            if autre.nom == nom_ctl and ctl_pays is not None:
                ctl = ctl_pays
            else:
                ctl = ctl_rinf if autre.nom == "rinf" and ctl_rinf is not None else autre.calculer(go, gd, rel["itineraire"])[0]
            if autre.nom == "rinf" and nom_principal == "sncf":
                # Second contrôle RINF sur le même itinéraire (lignes empruntées côté SNCF, même mode) :
                # les vitesses des deux sources diffèrent, chaque moteur peut choisir un autre itinéraire.
                # On garde celui des deux contrôles RINF le plus proche, et on dit lequel.
                try:
                    suivi = autre.calculer(go, gd, rel["itineraire"], [l for l, _ in res.lignes if l != "MANUEL"])[0]
                    if suivi.km > 0 and (ctl.km <= 0 or abs(res.km - suivi.km) < abs(res.km - ctl.km)):
                        ctl = suivi
                        ligne["remarques"].append("contrôle RINF sur les lignes de l'itinéraire SNCF")
                except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError):
                    pass
            if ctl.km <= 0:
                raise LookupError(f"distance nulle sur {autre.nom} (même point aux deux bouts)")
            ecart = 100 * (res.km / ctl.km - 1)
            ligne.update(distance_controle_km=round(ctl.km, 1), source_controle=autre.nom,
                         ecart_controle_pct=round(ecart, 2))
            marge = getattr(autre, "marge_arete_km", 0.0) * ctl.aretes
            if ctl.approche:
                # gare placée par interpolation sur la carte : sert à détecter un trou, pas à vérifier
                ligne["remarques"].append(f"contrôle {autre.nom.upper()} approché : "
                                          + " ; ".join(notes_ctl or autre.calculer(go, gd, rel["itineraire"])[1]))
                if abs(ecart) > rt.get("seuil_alerte_pct", math.inf):
                    alerte = True
                    ligne["remarques"].append(f"écart de {ecart:+.1f} % avec {autre.nom.upper()} : trou probable dans un des réseaux")
            elif _concorde(res.km, ctl.km, ecart, rt, marge):
                verifie.append(f"vérifié ({autre.nom.upper()})")
            elif abs(ecart) > rt.get("seuil_alerte_pct", math.inf):
                alerte = True
                ligne["remarques"].append(f"écart de {ecart:+.1f} % avec {autre.nom.upper()} : trou probable dans un des réseaux")
        except (nx.NetworkXNoPath, nx.NodeNotFound, LookupError) as e:
            ligne["remarques"].append(f"contrôle {autre.nom} impossible : {e}")
    if nom_principal == "sncf":
        pk = principal.controle_pk(go, gd, res, rt["part_ligne_controle_pk_pct"])
        if pk:
            ecart = 100 * (res.km / pk - 1)
            ligne.update(controle_pk_km=round(pk, 1), ecart_pk_pct=round(ecart, 2))
            if _concorde(res.km, pk, ecart, rt):
                verifie.append("vérifié (PK SNCF)")
                if alerte:  # les PK SNCF priment sur l'alerte RINF (choix d'Aloïs le 2026-09-29)
                    ligne["remarques"].append("distance confirmée par les PK SNCF, l'écart RINF n'est pas retenu")

    compl = sorted({lg for lg, _ in res.lignes} & getattr(principal, "complementaires", set()))
    if compl:
        ligne["remarques"].append("emprunte une ligne absente de formes-des-lignes-du-rfn, tracé repris du "
                                  "fichier des voies SNCF : " + ", ".join(compl))
    valides = valides or {}
    non_valides = [m for m in res.manuels if m not in valides]
    if non_valides:
        ligne["remarques"].append("emprunte un raccordement ajouté à la main : " + ", ".join(non_valides))
    for m in res.manuels:
        if m in valides:
            ligne["remarques"].append(f"emprunte un raccordement ajouté à la main, validé ({valides[m]}) : {m}")
    if rel.get("statut_force"):
        ligne["statut"] = rel["statut_force"]
    elif non_valides or repli or (alerte and "vérifié (PK SNCF)" not in verifie):
        ligne["statut"] = "à vérifier"
    elif verifie:
        ligne["statut"] = verifie[0]
    else:
        ligne["statut"] = "estimé"
    return ligne
