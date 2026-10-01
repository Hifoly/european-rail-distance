"""Carte HTML des gains de temps à 350 km/h, à partir du tableau par sous-relation.

Lecture seule : la carte se construit sur data/output/distances_srela_<date>.csv déjà calculé
(`distancier calculer --par-sous-relation`) et ne modifie aucun calcul. Une sous-relation est
retenue si au moins une de ses montées-descentes gagne du temps (temps_score_350 < temps_score).
Les tracés relient les arrêts en ligne droite ; chaque tronçon est coloré par son gain théorique.
"""
from __future__ import annotations

import collections
import csv
import json
import re
from pathlib import Path

from distancier import config

_DESSERTE = re.compile(r" \(\d.*$")


def _nombre(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _arrets(desserte: str) -> list[str]:
    return _DESSERTE.sub("", desserte).split(" > ") if desserte else []


def dernier_tableau(cfg: dict) -> Path:
    fichiers = sorted(config.chemin(cfg, "sorties").glob("distances_srela_*.csv"))
    if not fichiers:
        raise FileNotFoundError("aucun tableau par sous-relation : lancer d'abord "
                                "« distancier calculer --par-sous-relation »")
    return fichiers[-1]


def positions(cfg: dict) -> tuple[dict, dict]:
    """Coordonnées (lat, lon) par code UIC et par nom de gare : jeu SNCF des gares, puis config/gares.csv."""
    from distancier.sources import sncf
    par_uic, par_nom = {}, {}
    for uic, g in sncf.dernieres_gares(cfg).items():
        if g["lat"] is not None:
            par_uic[uic] = par_nom.setdefault(g["nom"], (round(g["lat"], 5), round(g["lon"], 5)))
    with open(config.chemin(cfg, "gares"), encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r.get("lat") and r.get("lon"):
                par_uic[r["uic"]] = (round(float(r["lat"]), 5), round(float(r["lon"]), 5))
    return par_uic, par_nom


def donnees(lignes: list[dict], par_uic: dict, par_nom: dict) -> list[dict]:
    """Une entrée par sous-relation impactée : tronçons à tracer et montées-descentes qui gagnent du temps."""
    nom_uic = {}
    for r in lignes:
        for nom, uic in ((r["gare_origine"], r["code_uic_origine"]), (r["gare_destination"], r["code_uic_destination"])):
            if nom and uic:
                nom_uic[nom] = uic

    def pos(nom):
        return par_uic.get(nom_uic.get(nom, "")) or par_nom.get(nom) or par_nom.get(nom + " (IE)")

    gain_troncon = {}
    for r in lignes:
        t, t350 = _nombre(r.get("temps_theorique")), _nombre(r.get("temps_theorique_350"))
        if t is not None and t350 is not None and r["gare_origine"]:
            gain_troncon[frozenset((r["gare_origine"], r["gare_destination"]))] = round(t - t350, 1)

    sous_relations = collections.OrderedDict()
    for r in lignes:
        s = sous_relations.setdefault((r["relation"], r["sous_relation"]),
                                      {"r": r["relation"], "sr": r["sous_relation"], "troncons": {}, "od": [], "gmax": 0.0})
        for d in (r.get("desserte_score"), r.get("desserte_score_min"), r.get("desserte_score_max")):
            a = _arrets(d or "")
            for x, y in zip(a, a[1:]):
                px, py = pos(x), pos(y)
                if px and py:
                    s["troncons"][frozenset((x, y))] = (x, y, px, py)
        ts, ts350 = _nombre(r.get("temps_score")), _nombre(r.get("temps_score_350"))
        if ts is None or ts350 is None or ts350 >= ts - 0.05:
            continue
        gain = round(ts - ts350, 1)
        s["gmax"] = max(s["gmax"], gain)
        s["od"].append({
            "o": r["gare_origine"], "d": r["gare_destination"], "km": _nombre(r.get("distance_km")),
            "kmin": _nombre(r.get("distance_km_min")), "kmax": _nombre(r.get("distance_km_max")),
            "lgv": _nombre(r.get("part_lgv_pct")), "th": _nombre(r.get("temps_theorique")),
            "th350": _nombre(r.get("temps_theorique_350")), "smin": _nombre(r.get("temps_score_min")), "s": ts,
            "smax": _nombre(r.get("temps_score_max")), "s350": ts350, "g": gain, "gp": round(100 * gain / ts, 1),
            "na": r.get("nb_arret_inter", ""), "des": r.get("desserte_score", ""), "st": r.get("statut", ""),
        })

    sortie = []
    for s in sous_relations.values():
        if not s["od"]:
            continue
        s["od"].sort(key=lambda o: -o["g"])
        segs = [{"a": a, "b": b, "p": [pa, pb], "g": gain_troncon.get(frozenset((a, b)))}
                for a, b, pa, pb in s["troncons"].values()]
        sortie.append({"r": s["r"], "sr": s["sr"], "gmax": s["gmax"], "n": len(s["od"]), "segs": segs, "od": s["od"]})
    return sorted(sortie, key=lambda s: -s["gmax"])


def ecrire(cfg: dict, fichier: Path | None = None) -> Path:
    fichier = fichier or dernier_tableau(cfg)
    with open(fichier, encoding="utf-8-sig", newline="") as f:
        lignes = list(csv.DictReader(f, delimiter=";"))
    par_uic, par_nom = positions(cfg)
    d = donnees(lignes, par_uic, par_nom)
    date = re.search(r"(\d{4}-\d{2}-\d{2})", fichier.name)
    sortie = fichier.parent / f"carte_350_{date.group(1) if date else 'srela'}.html"
    sortie.write_text(_HTML.replace("__SOURCE__", fichier.name)
                      .replace("__DATA__", json.dumps(d, ensure_ascii=False, separators=(",", ":"))), encoding="utf-8")
    return sortie


_HTML = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gains à 350 km/h</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root{--bg:#fff;--fg:#1d2433;--muted:#5b6475;--line:#e3e6ec;--accent:#c2410c;--panel:#f7f8fa}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e8eaf0;--muted:#9aa3b5;--line:#2a2f3a;--accent:#fb923c;--panel:#1b1f27}}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
#app{display:flex;height:100%}#side{width:420px;max-width:45vw;display:flex;flex-direction:column;border-right:1px solid var(--line);background:var(--panel)}
#map{flex:1}header{padding:12px 14px;border-bottom:1px solid var(--line)}h1{font-size:16px;margin:0 0 4px}
.sub{color:var(--muted);font-size:12px}#q{width:100%;margin-top:8px;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
#list{overflow:auto;flex:1}.item{padding:8px 14px;border-bottom:1px solid var(--line);cursor:pointer}.item:hover,.item.on{background:var(--bg)}
.item b{display:block}.item span{color:var(--muted);font-size:12px}.badge{float:right;font-weight:600;color:var(--accent)}
#detail{max-height:55%;overflow:auto;border-top:2px solid var(--accent);display:none;background:var(--bg)}#detail h2{font-size:14px;margin:10px 14px 2px}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:4px 6px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}th{position:sticky;top:0;background:var(--panel);cursor:pointer}
.legend{background:var(--bg);color:var(--fg);padding:6px 8px;border-radius:6px;font-size:12px;line-height:1.6}.sw{display:inline-block;width:18px;height:4px;margin-right:6px;vertical-align:middle}
.close{float:right;margin:8px 12px;cursor:pointer;color:var(--muted)}
@media (max-width:760px){#app{flex-direction:column}#side{width:100%;max-width:none;height:50%}#map{height:50%}}
</style></head><body><div id="app"><div id="side"><header><h1>Gains à 350 km/h</h1>
<div class="sub">Sous-relations dont au moins une montée-descente gagne du temps si 300 et 320 km/h passent à 350. Tableau __SOURCE__ (Extract_score 2025). Gain = temps_score − temps_score_350, en minutes. Tracés en ligne droite d'arrêt en arrêt.</div>
<input id="q" placeholder="Filtrer (relation, sous-relation, gare)…"></header><div id="list"></div>
<div id="detail"></div></div><div id="map"></div></div>
<script>
const DATA = __DATA__;
const map = L.map('map',{preferCanvas:true}).setView([46.6,2.4],6);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© OpenStreetMap'}).addTo(map);
const orm = L.tileLayer('https://{s}.tiles.openrailwaymap.org/maxspeed/{z}/{x}/{y}.png',{maxZoom:19,opacity:.55,attribution:'© OpenRailwayMap'});
L.control.layers(null,{'Vitesses (OpenRailwayMap)':orm}).addTo(map);
const col = g => g==null?'#94a3b8':g<=0.05?'#94a3b8':g<2?'#fdba74':g<5?'#f97316':g<10?'#dc2626':'#7f1d1d';
const lg = L.control({position:'bottomright'});
lg.onAdd=()=>{const d=L.DomUtil.create('div','legend');d.innerHTML='<b>Gain du tronçon (théorique)</b><br>'+
 [['#94a3b8','aucun'],['#fdba74','&lt; 2 min'],['#f97316','2 – 5 min'],['#dc2626','5 – 10 min'],['#7f1d1d','≥ 10 min']].map(x=>`<span class="sw" style="background:${x[0]}"></span>${x[1]}`).join('<br>');return d};
lg.addTo(map);
const layers=[];
DATA.forEach((s,i)=>{const grp=L.featureGroup();
 s.segs.forEach(g=>L.polyline(g.p,{color:col(g.g),weight:3,opacity:.75}).bindTooltip(`${g.a} – ${g.b}<br>gain théorique : ${g.g==null?'?':g.g+' min'}`,{sticky:true}).addTo(grp));
 grp.on('click',()=>show(i)); grp.addTo(map); layers.push(grp);});
const fmt=v=>v==null?'–':(Math.round(v*10)/10).toLocaleString('fr-FR');
function show(i){const s=DATA[i];
 layers.forEach((l,j)=>l.setStyle({opacity:j===i?1:.12,weight:j===i?6:3})); layers[i].bringToFront();
 if(layers[i].getLayers().length) map.fitBounds(layers[i].getBounds(),{padding:[30,30]});
 document.querySelectorAll('.item').forEach(e=>e.classList.toggle('on',+e.dataset.i===i));
 const d=document.getElementById('detail'); d.style.display='block';
 d.innerHTML=`<span class="close" onclick="reset()">✕ fermer</span><h2>${s.r} / ${s.sr}</h2><div class="sub" style="margin:0 14px 8px">${s.n} montées-descentes impactées, gain max ${fmt(s.gmax)} min. Cliquer un en-tête pour trier.</div>
 <table><thead><tr><th data-k="o">Montée › descente</th><th data-k="km">km</th><th data-k="lgv">LGV %</th><th data-k="th">Théo</th><th data-k="th350">Théo 350</th><th data-k="smin">Score min</th><th data-k="s">Score</th><th data-k="smax">Score max</th><th data-k="s350">Score 350</th><th data-k="g">Gain</th><th data-k="gp">Gain %</th><th data-k="na">Arrêts</th></tr></thead><tbody></tbody></table>`;
 let key='g',dir=-1; const body=()=>{const od=[...s.od].sort((a,b)=>((a[key]??-1e9)>(b[key]??-1e9)?1:-1)*dir);
  d.querySelector('tbody').innerHTML=od.map(o=>`<tr title="${o.des} — ${o.st}"><td>${o.o} › ${o.d}</td><td>${fmt(o.km)}${o.kmin!==o.km||o.kmax!==o.km?` <span class="sub">(${fmt(o.kmin)}–${fmt(o.kmax)})</span>`:''}</td><td>${fmt(o.lgv)}</td><td>${fmt(o.th)}</td><td>${fmt(o.th350)}</td><td>${fmt(o.smin)}</td><td>${fmt(o.s)}</td><td>${fmt(o.smax)}</td><td>${fmt(o.s350)}</td><td><b>${fmt(o.g)}</b></td><td>${fmt(o.gp)}</td><td>${o.na}</td></tr>`).join('')};
 d.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k; dir=key===k?-dir:-1; key=k; body()}); body();}
function reset(){layers.forEach(l=>l.setStyle({opacity:.75,weight:3}));document.getElementById('detail').style.display='none';document.querySelectorAll('.item').forEach(e=>e.classList.remove('on'))}
const hay=s=>(s.r+' '+s.sr+' '+s.od.map(o=>o.o+' '+o.d).join(' ')).toLowerCase();
function list(q){q=(q||'').toLowerCase();document.getElementById('list').innerHTML=DATA.map((s,i)=>hay(s).includes(q)?
 `<div class="item" data-i="${i}"><span class="badge">${fmt(s.gmax)} min</span><b>${s.sr}</b><span>${s.r} · ${s.n} montées-descentes</span></div>`:'').join('');
 document.querySelectorAll('.item').forEach(e=>e.onclick=()=>show(+e.dataset.i));
 layers.forEach((l,i)=>{if(hay(DATA[i]).includes(q))l.addTo(map);else map.removeLayer(l)});}
document.getElementById('q').oninput=e=>list(e.target.value); list('');
</script></body></html>"""
