"""Orchestration : référentiel de gares, moteurs de calcul, tableau des relations."""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import logging
import math
import pickle
import statistics
from pathlib import Path

import networkx as nx
import yaml

from distancier import config
from distancier.reseau import Raccordement, VirageInterdit, construire_graphe
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


class TgvCommercial:
    """Itinéraire « TGV commercial » : la relation suit les arrêts du TGV direct le plus fréquent
    des horaires SNCF. Chaque tronçon d'arrêt à arrêt est une relation à part entière (itinéraire
    grande vitesse, mêmes contrôles et même statut), calculée une fois puis gardée en cache."""

    def __init__(self, dessertes, source: str, date: str, gares: dict, gares_sncf: dict):
        self.dessertes, self.source, self.date = dessertes, source, date
        self.gares, self.gares_sncf = gares, gares_sncf
        self._troncons: dict[tuple, dict | str] = {}

    def gare(self, uic: str) -> dict | None:
        g = self.gares.get(uic)
        if g and g.get("lon"):
            return g
        ref = self.gares_sncf.get(uic)
        if ref and ref["lon"] is not None:
            return {"uic": uic, "nom": (g or ref)["nom"], "pays": "FR", "uopid_rinf": (g or {}).get("uopid_rinf", ""),
                    "note": "", "lon": ref["lon"], "lat": ref["lat"], "pks": ref["pks"]}
        return None

    def troncon(self, a: str, b: str, calcul, itineraire: str = "grande_vitesse") -> dict | str:
        """Ligne de résultat du tronçon a -> b, ou le motif de l'échec. `calcul(rel, gares)` calcule une relation."""
        cle = (a, b, itineraire)
        if cle not in self._troncons:
            ga, gb = self.gare(a), self.gare(b)
            if ga is None or gb is None:
                self._troncons[cle] = f"arrêt UIC {a if ga is None else b} absent du jeu SNCF des gares"
            else:
                rel = {"id": "", "uic_origine": a, "uic_destination": b, "itineraire": itineraire}
                t = calcul(rel, {a: dict(ga), b: dict(gb)})
                self._troncons[cle] = t if "resultat" in t else f"{ga['nom']} - {gb['nom']} : {t['statut']}"
        return self._troncons[cle]

    def nom(self, uic: str) -> str:
        g = self.gares.get(uic) or self.gares_sncf.get(uic)
        return g["nom"] if g else uic


# remarques de tronçon reprises sur la relation (les autres sont des informations de routine)
_REMARQUES_ROUTINE = ("itinéraire LGV privilégié", "contrôle RINF sur les lignes")


def _assembler(troncons: list[dict]) -> Resultat:
    res = Resultat(km=0.0)
    vit = collections.Counter()
    for t in troncons:
        r = t["resultat"]
        res.km += r.km
        vit.update(r.vitesses)
        for ligne, km in r.lignes:
            if res.lignes and res.lignes[-1][0] == ligne:
                res.lignes[-1] = (ligne, res.lignes[-1][1] + km)
            else:
                res.lignes.append((ligne, km))
        res.manuels += [m for m in r.manuels if m not in res.manuels]
    res.vitesses = dict(vit)
    return res


def _statut_troncons(troncons: list[dict]) -> str:
    statuts = [t["statut"] for t in troncons]
    if "à vérifier" in statuts:
        return "à vérifier"
    if all(s.startswith("vérifié (") for s in statuts):
        moyens = sorted({s[len("vérifié ("):-1] for s in statuts})
        return f"vérifié ({' et '.join(moyens)})"
    return "estimé"


def _tgv_commercial(ligne: dict, rel: dict, tgv: TgvCommercial, calcul, rt: dict) -> None:
    """Remplace la distance de `ligne` par celle du TGV direct le plus fréquent, si elle existe."""
    go, gd = ligne["code_uic_origine"], ligne["code_uic_destination"]
    if go == gd:
        return
    trouve = tgv.dessertes.desserte(go, gd)
    if trouve is None:
        ligne["desserte_tgv"] = "aucun TGV direct"
        return
    arrets, n, duree = trouve
    if duree is not None:
        ligne["temps_pratique"] = round(duree)
    troncons = []
    for a, b in zip(arrets[:-1], arrets[1:]):
        if len(arrets) == 2 and rel["itineraire"] == "grande_vitesse":
            t = ligne                              # même calcul que la relation elle-même
        else:
            t = tgv.troncon(a, b, calcul)
        if isinstance(t, str):
            ligne["remarques"].append(f"distance TGV commerciale impossible : {t}")
            return
        troncons.append(t)
    moteurs = {t["moteur"] for t in troncons}
    if len(moteurs) > 1:   # un moteur par relation : pas de distance assemblée de deux sources
        ligne["remarques"].append("distance TGV commerciale non retenue : tronçons calculés sur des moteurs "
                                  "différents (" + ", ".join(sorted(moteurs)) + ")")
        return
    desserte = " > ".join(tgv.nom(u) for u in arrets) + f" ({n} train{'s' if n > 1 else ''})"
    ligne["distance_tgv_commercial_km"] = round(_assembler(troncons).km, 1)
    ligne["desserte_tgv"] = desserte
    _appliquer_troncons(ligne, rel, troncons, f"distance du TGV direct le plus fréquent ({desserte})",
                        f"desserte TGV : {tgv.source}, consulté le {tgv.date}", "tgv_commercial", rt)


def _appliquer_troncons(ligne: dict, rel: dict, troncons: list[dict], motif: str, source_desserte: str,
                        itineraire: str, rt: dict) -> None:
    """distance_km devient la somme des tronçons d'arrêt en arrêt : vitesses, lignes, contrôles et
    statut suivent les tronçons. Les tronçons doivent venir d'un seul moteur."""
    if troncons == [ligne]:
        ligne["source"] += f" ; {source_desserte}"
        return
    res = _assembler(troncons)
    moteurs = {t["moteur"] for t in troncons}
    au_plus_court = ligne["resultat"].km
    remarques = [r for r in ligne["remarques"] if not r.startswith(_REMARQUES_ROUTINE)]
    remarques.append(f"{motif} ; au plus court : {au_plus_court:.1f} km")
    if au_plus_court > 0 and res.km > au_plus_court * 1.01:
        remarques.append(f"le TGV fait un détour de {100 * (res.km / au_plus_court - 1):+.1f} % par ses arrêts")
    for t in troncons:
        nom = f"{t['gare_origine']} - {t['gare_destination']}"
        remarques += [f"{nom} : {r}" for r in t["remarques"]
                      if not r.startswith(_REMARQUES_ROUTINE) and f"{nom} : {r}" not in remarques]
    for cle in ("distance_controle_km", "source_controle", "ecart_controle_pct", "controle_pk_km", "ecart_pk_pct"):
        ligne.pop(cle, None)
    sources_ctl = {t.get("source_controle") for t in troncons}
    if len(sources_ctl) == 1 and None not in sources_ctl:
        ctl = sum(t["distance_controle_km"] for t in troncons)
        ligne.update(distance_controle_km=round(ctl, 1), source_controle=sources_ctl.pop(),
                     ecart_controle_pct=round(100 * (res.km / ctl - 1), 2) if ctl else "")
    pk = sum(t.get("controle_pk_km") or 0 for t in troncons)
    if pk > 0 and all(t.get("controle_pk_km") for t in troncons):
        ligne.update(controle_pk_km=round(pk, 1), ecart_pk_pct=round(100 * (res.km / pk - 1), 2))
    ligne.update(itineraire_retenu=itineraire, resultat=res, remarques=remarques, moteur=moteurs.pop(),
                 date_consultation=troncons[0]["date_consultation"],
                 source=troncons[0]["source"] + f" ; {source_desserte}")
    # Statut : comme pour une relation, le contrôle de tout le trajet suffit à « vérifier » ;
    # sinon chaque tronçon doit l'être. Un tronçon « à vérifier » le reste pour tout le trajet.
    statut = _statut_troncons(troncons)
    if statut == "estimé":
        globaux = [n for n, (km, ctl) in (("RINF", (res.km, ligne.get("distance_controle_km"))),
                                          ("PK SNCF", (res.km, ligne.get("controle_pk_km"))))
                   if ctl and _concorde(km, ctl, 100 * (km / ctl - 1), rt)]
        if globaux:
            statut = f"vérifié ({globaux[0]})"
    ligne["statut"] = rel["statut_force"] if rel.get("statut_force") else statut


def _preparer(cfg: dict, jour_sncf: str | None, jour_rinf: str | None) -> tuple[dict, dict, dict, dict]:
    """Moteurs, référentiel de gares, sources et corrections."""
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

    sources = {n: {"description": m.source, "date_consultation": m.date} for n, m in moteurs.items()}
    return moteurs, gares, sources, corrections


def calculer(cfg: dict, jour_sncf: str | None = None, jour_rinf: str | None = None,
             jour_gtfs: str | None = None) -> dict:
    moteurs, gares, sources, corrections = _preparer(cfg, jour_sncf, jour_rinf)
    tgv = None
    if "sncf" in moteurs and cfg["sources"].get("gtfs_sncf"):
        from distancier.sources import gtfs
        try:
            d = gtfs.charger(cfg, jour_gtfs)
        except FileNotFoundError as e:
            log.warning("pas de distance TGV commerciale : %s", e)
        else:
            tgv = TgvCommercial(gtfs.Dessertes(d["trajets"], d["horaires"]), gtfs.description_source(d["manifeste"]),
                                gtfs.date_consultation(d["manifeste"]), gares, moteurs["sncf"].gares)
            sources["gtfs_sncf"] = {"description": tgv.source, "date_consultation": tgv.date}

    rt = cfg["routage"]
    lignes = []
    for rel in lire_csv(config.chemin(cfg, "relations")):
        lignes.append(_relation(rel, gares, moteurs, cfg, rt, corrections["valides"], tgv))
    if cfg["sources"].get("score"):
        _temps_score(cfg, lignes, sources)
    vitesses = sorted({v for m in moteurs.values() for v in m.vitesses}, reverse=True)
    return {"relations": lignes, "vitesses": vitesses, "sources": sources}


def _temps_score(cfg: dict, lignes: list[dict], sources: dict) -> None:
    """temps_score : temps médian de l'année dans le plan de transport (Extract_score), deux sens réunis."""
    from distancier.sources import score
    try:
        d = score.charger(cfg)
    except FileNotFoundError as e:
        log.warning("%s", e)
        return
    sources["score"] = {"description": d["description"], "date_consultation": d["date_consultation"]}
    for ligne in lignes:
        o, a = d["iata"].get(ligne.get("code_uic_origine", "")), d["iata"].get(ligne.get("code_uic_destination", ""))
        t = d["temps"].get(frozenset((o, a))) if o and a else None
        if t is not None:
            ligne["temps_score"] = round(t, 1)
            if ligne.get("temps_theorique") is not None and ligne.get("temps_theorique_350") is not None:
                # gain théorique d'un relèvement à 350 km/h (choix d'Aloïs le 2026-10-01)
                ligne["temps_score_350"] = round(t - (ligne["temps_theorique"] - ligne["temps_theorique_350"]), 1)


def _concorde(km: float, controle: float, ecart_pct: float, rt: dict) -> bool:
    """Deux distances concordent si l'écart est sous le seuil en % OU sous le seuil en km
    (trajets courts : la position de la gare sur la voie varie de quelques centaines de mètres)."""
    return abs(ecart_pct) <= rt["seuil_verification_pct"] or abs(km - controle) <= rt.get("seuil_verification_km", 0)


def _relation(rel: dict, gares: dict, moteurs: dict, cfg: dict, rt: dict, valides: dict | None = None,
              tgv: TgvCommercial | None = None) -> dict:
    """Une relation : itinéraire sur le réseau (distance au plus court), puis, si un TGV direct la
    dessert, distance_km = distance de ce TGV d'arrêt en arrêt."""
    ligne = _relation_reseau(rel, gares, moteurs, cfg, rt, valides)
    if "resultat" not in ligne:
        return ligne
    ligne["distance_au_plus_court_km"] = round(ligne["resultat"].km, 1)
    if tgv is not None and ligne["moteur"] == "sncf":
        _tgv_commercial(ligne, rel, tgv,
                        lambda r, g: _relation_reseau(r, g, moteurs, cfg, rt, valides), rt)
    _temps_theorique(ligne)
    return ligne


V_RELEVEE, SEUIL_RELEVE = 350, 300   # temps_theorique_350 : v_max >= 300 km/h portée à 350 km/h


def _vitesse_mediane(km_par_vitesse: dict) -> int:
    """Vitesse en dessous de laquelle se trouve la moitié des km."""
    total, cumul = sum(km_par_vitesse.values()), 0.0
    for v in sorted(km_par_vitesse):
        cumul += km_par_vitesse[v]
        if cumul >= total / 2:
            return v
    return max(km_par_vitesse)


def _minutes_theoriques(res: Resultat) -> tuple[float, float, float, int] | None:
    """(minutes, minutes à 350 km/h, km sans vitesse, vitesse médiane) pour parcourir `res` en roulant
    partout à la vitesse maximale ; None sans aucune vitesse connue. Les km sans vitesse connue
    comptent à la vitesse médiane du trajet, pondérée par les km (choix d'Aloïs le 2026-10-01).
    Variante 350 : sections à 300 ou 320 km/h relevées à 350 km/h (demande d'Aloïs le 2026-10-01)."""
    connues = {v: km for v, km in res.vitesses.items() if v}
    if not connues:
        return None
    inconnu = res.vitesses.get(None, 0.0)
    v_med = _vitesse_mediane(connues)
    minutes_inconnu = 60 * inconnu / v_med if inconnu > 0 else 0.0
    return (minutes_inconnu + sum(60 * km / v for v, km in connues.items()),
            minutes_inconnu + sum(60 * km / (V_RELEVEE if v >= SEUIL_RELEVE else v) for v, km in connues.items()),
            inconnu, v_med)


def _temps_theorique(ligne: dict) -> None:
    """temps_theorique et temps_theorique_350 : minutes pour parcourir distance_km (voir _minutes_theoriques)."""
    res = ligne["resultat"]
    t = _minutes_theoriques(res)
    if t is None:
        if res.km > 0:
            ligne["remarques"].append("temps théorique non calculé : aucune vitesse connue sur le trajet")
        return
    minutes, minutes_350, inconnu, v_med = t
    if inconnu > 0.5:
        ligne["remarques"].append(f"temps théorique : {inconnu:.1f} km sans vitesse connue comptés à "
                                  f"{v_med} km/h (vitesse médiane du trajet)")
    ligne["temps_theorique"] = round(minutes, 1)
    ligne["temps_theorique_350"] = round(minutes_350, 1)


def _relation_reseau(rel: dict, gares: dict, moteurs: dict, cfg: dict, rt: dict, valides: dict | None = None) -> dict:
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

    verifie, alerte = [], False
    autre = None if repli else moteurs.get("rinf" if nom_principal == "sncf" else "sncf")
    if autre is not None and (autre.nom == "rinf" or tous_sncf):
        try:
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
            if _concorde(res.km, ctl.km, ecart, rt):
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


# --- par relation et sous-relation du plan de transport (branche france_rela_srela) ------------

def calculer_sous_relations(cfg: dict, jour_sncf: str | None = None, jour_rinf: str | None = None) -> dict:
    """Une ligne par relation, sous-relation, montée et descente du plan de transport TGV
    (Extract_score), dans le sens du train : distance de la desserte la plus fréquente, d'arrêt en
    arrêt. Sur chaque tronçon, LGV ou ligne classique selon le temps de parcours des trains."""
    from distancier.sources import score
    moteurs, gares, sources, corrections = _preparer(cfg, jour_sncf, jour_rinf)
    if "sncf" not in moteurs:
        raise RuntimeError("données SNCF Réseau non téléchargées : lancer « distancier telecharger --source sncf »")
    d = score.charger_sous_relations(cfg)
    sources["score"] = {"description": d["description"], "date_consultation": d["date_consultation"]}
    uic: dict[str, str] = {}
    for u, i in sorted(d["iata"].items()):
        uic.setdefault(i, u)
    rt = cfg["routage"]
    arrets = TgvCommercial(None, d["description"], d["date_consultation"], gares, moteurs["sncf"].gares)
    contexte = {"calcul": lambda r, g: _relation_reseau(r, g, moteurs, cfg, rt, corrections["valides"]),
                "arrets": arrets, "uic": uic, "troncons": d["troncons"], "directs": {}, "rt": rt,
                "frontieres": {k: str(v) for k, v in (cfg["sources"]["score"].get("frontieres") or {}).items()},
                "source": f"desserte : {d['description']}, fichier du {d['date_consultation']}",
                "couples": Path(cfg["sources"]["score"]["couples"]).name}
    contexte["k"] = _coefficient_temps(contexte)
    sources["score"]["coefficient_temps"] = round(contexte["k"], 3)
    log.info("temps des trains / temps théorique sur les tronçons sans alternative : %.2f", contexte["k"])
    lignes = [_sous_relation(i, cle, od, contexte) for i, (cle, od) in enumerate(sorted(d["od"].items()), 1)]
    vitesses = sorted({v for m in moteurs.values() for v in m.vitesses}, reverse=True)
    return {"relations": lignes, "vitesses": vitesses, "sources": sources}


def _deux_itineraires(a: str, b: str, ctx: dict) -> tuple[dict | str, dict | None, float | None, float | None]:
    """Tronçon a -> b par la LGV (itinéraire grande vitesse) et par la ligne classique (sans LGV), avec
    leurs temps théoriques. La ligne classique est None si elle n'existe pas, vient d'un autre moteur
    ou n'est pas plus lente de 2 % (rien à départager)."""
    gv = ctx["arrets"].troncon(a, b, ctx["calcul"])
    if isinstance(gv, str):
        return gv, None, None, None
    tg = _minutes_theoriques(gv["resultat"])
    tg = tg[0] if tg else None
    cl = ctx["arrets"].troncon(a, b, ctx["calcul"], "sans_lgv")
    if isinstance(cl, str) or cl["moteur"] != gv["moteur"] or tg is None:
        return gv, None, tg, None
    tc = _minutes_theoriques(cl["resultat"])
    if tc is None or tc[0] <= tg * 1.02:
        return gv, None, tg, None
    return gv, cl, tg, tc[0]


def _coefficient_temps(ctx: dict) -> float:
    """Rapport médian entre le temps de parcours des trains et le temps théorique, sur les tronçons
    où il n'y a pas à choisir entre LGV et ligne classique : marges, ralentissements, accélérations."""
    ratios = []
    for (_, _, ia, ib), minutes in ctx["troncons"].items():
        a, b = ctx["uic"].get(ia), ctx["uic"].get(ib)
        if not a or not b or a == b:
            continue
        gv, cl, tg, _ = _deux_itineraires(a, b, ctx)
        if cl is None and tg:
            ratios.append(minutes / tg)
    return statistics.median(ratios) if ratios else 1.0


def _troncon_selon_temps(a: str, b: str, minutes: float | None, ctx: dict) -> tuple[dict | str, dict | None]:
    """Tronçon a -> b par la LGV ou par la ligne classique : celui dont le temps théorique, multiplié
    par le coefficient observé (_coefficient_temps), est le plus proche du temps de parcours des trains,
    au sens du rapport (seuil à la moyenne géométrique des deux temps). Renvoie le tronçon et, si la
    ligne classique est retenue, les temps comparés."""
    gv, cl, tg, tc = _deux_itineraires(a, b, ctx)
    if cl is None or minutes is None:
        return gv, None
    if minutes / ctx["k"] >= math.sqrt(tg * tc):
        return cl, {"trains": minutes, "lgv": tg * ctx["k"], "classique": tc * ctx["k"]}
    return gv, None


def _sous_relation(i: int, cle: tuple, od: dict, ctx: dict) -> dict:
    relation, sous_relation, montee, descente = cle
    base = {"id": i, "relation": relation, "sous_relation": sous_relation, "montee_iata": montee,
            "descente_iata": descente}
    desserte = list(od["desserte"])
    uics = [ctx["uic"].get(x) for x in desserte]
    # gare étrangère en bout de trajet : distance arrêtée au point frontière, comme dans le tableau principal
    remarques_frontiere, frontiere = [], set()
    for k in (0, -1):
        if uics[k] is None and desserte[k] in ctx["frontieres"]:
            uics[k] = ctx["frontieres"][desserte[k]]
            frontiere.add(k % len(uics))
            remarques_frontiere.append(f"{desserte[k]} : distance arrêtée au point frontière "
                                       f"{ctx['arrets'].nom(uics[k])} ; temps_score jusqu'à la gare étrangère")
    manquants = sorted({x for x, u in zip(desserte, uics) if u is None})
    if manquants:
        etrangers = [x for x in manquants if not x.startswith("FR")]
        motif = (f"hors périmètre : gare étrangère sans point frontière (IATA {', '.join(etrangers)}, "
                 f"à ajouter dans sources.score.frontieres)" if etrangers == manquants
                 else f"erreur : IATA {', '.join(manquants)} absent de {ctx['couples']}")
        return {**base, "remarques": [], "statut": motif}
    # deux arrêts étrangers de suite ramenés au même point frontière
    garde = [k for k in range(len(uics)) if k == 0 or uics[k] != uics[k - 1]]
    frontiere = {garde.index(k) if k in garde else len(garde) - 1 for k in frontiere}
    desserte, uics = [desserte[k] for k in garde], [uics[k] for k in garde]
    if len(uics) < 2:
        return {**base, "remarques": remarques_frontiere, "statut": "hors périmètre : trajet hors de France"}
    go, gd = uics[0], uics[-1]
    # relation directe entre la montée et la descente : distance au plus court, une fois par couple de gares
    if (go, gd) not in ctx["directs"]:
        g = {u: dict(x) for u in (go, gd) if (x := ctx["arrets"].gare(u))}
        if len(g) < 2:
            manque = go if go not in g else gd
            ctx["directs"][(go, gd)] = {"remarques": [], "statut": f"erreur : UIC {manque} absent du jeu SNCF des gares"}
        else:
            ctx["directs"][(go, gd)] = ctx["calcul"]({"id": "", "uic_origine": go, "uic_destination": gd,
                                                      "itineraire": "grande_vitesse"}, g)
    direct = ctx["directs"][(go, gd)]
    ligne = {**direct, **base, "remarques": list(direct["remarques"]) + remarques_frontiere}
    if "resultat" not in ligne:
        return ligne
    ligne["distance_au_plus_court_km"] = round(ligne["resultat"].km, 1)
    noms = [ctx["arrets"].nom(u) for u in uics]
    texte = " > ".join(noms)
    if od.get("part_desserte_pct") is not None:
        texte += f" ({od['part_desserte_pct']:.0f} % des circulations)"
    ligne.update(desserte_score=texte, part_desserte_pct=od.get("part_desserte_pct"),
                 circulations_annee=od.get("circulations"))

    troncons, classiques = [], []
    for k, (a, b) in enumerate(zip(uics, uics[1:])):
        minutes = None if {k, k + 1} & frontiere else \
            ctx["troncons"].get((relation, sous_relation, desserte[k], desserte[k + 1]))
        t, cmp = _troncon_selon_temps(a, b, minutes, ctx)
        if isinstance(t, str):
            ligne["remarques"].append(f"distance de la desserte impossible : {t}")
            troncons = None
            break
        if cmp:
            classiques.append(f"{noms[k]} - {noms[k + 1]}")
            ligne["remarques"].append(
                f"{noms[k]} - {noms[k + 1]} : ligne classique retenue (trains en {cmp['trains']:.0f} min, "
                f"théorique {cmp['lgv']:.0f} min par la LGV et {cmp['classique']:.0f} min sans)")
        troncons.append(t)
    if troncons:
        moteurs = {t["moteur"] for t in troncons}
        if len(moteurs) > 1:   # un moteur par relation : pas de distance assemblée de deux sources
            ligne["remarques"].append("distance de la desserte non retenue : tronçons calculés sur des moteurs "
                                      "différents (" + ", ".join(sorted(moteurs)) + ")")
        else:
            if len(troncons) == 1 and not classiques:
                troncons = [ligne]                  # même calcul que la relation directe
            ligne["troncons_sans_lgv"] = " ; ".join(classiques)
            _appliquer_troncons(ligne, {"itineraire": "grande_vitesse"}, troncons,
                                f"distance de la desserte la plus fréquente ({texte})", ctx["source"],
                                "desserte_score", ctx["rt"])
            ligne["itineraire_retenu"] = "desserte_score"
    _temps_theorique(ligne)
    if od.get("temps") is not None:
        ligne["temps_score"] = round(od["temps"], 1)
        if ligne.get("temps_theorique") is not None:
            ligne["temps_score_350"] = round(od["temps"] - (ligne["temps_theorique"] - ligne["temps_theorique_350"]), 1)
    return ligne
