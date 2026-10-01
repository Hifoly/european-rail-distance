"""Carte HTML des relations dont des montées-descentes gagnent du temps à 350 km/h.

Lit le résultat de calcul.calculer_sous_relations (avec les tracés des arêtes, Resultat.traces) et
écrit un fichier HTML autonome (Leaflet, fonds de carte en ligne) : les voies empruntées, en rouge
les sections à 300 km/h ou plus ; un clic sur une voie ou sur une relation de la liste affiche ses
montées-descentes et leurs temps.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from distancier.calcul import SEUIL_RELEVE


def impactee(ligne: dict) -> bool:
    """Montée-descente qui gagne du temps quand les sections à 300 ou 320 km/h passent à 350 km/h."""
    t, t350 = ligne.get("temps_theorique"), ligne.get("temps_theorique_350")
    return t is not None and t350 is not None and t350 < t


def _nombre(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def donnees(resultat: dict) -> dict:
    """Segments de voie (dédoublonnés) et relations avec leurs montées-descentes impactées."""
    segments, index = [], {}
    relations: dict[str, dict] = {}
    for ligne in resultat["relations"]:
        res = ligne.get("resultat")
        if res is None or not impactee(ligne):
            continue
        ids = []
        for coords, v in res.traces:
            cle = (coords[0], coords[-1], len(coords))
            if cle not in index:
                index[cle] = len(segments)
                segments.append({"c": [[lat, lon] for lon, lat in coords], "v": v})
            ids.append(index[cle])
        km_relevees = sum(k for v, k in res.vitesses.items() if v and v >= SEUIL_RELEVE)
        od = {
            "sous_relation": ligne.get("sous_relation", ""),
            "montee": ligne.get("montee_iata", ""), "descente": ligne.get("descente_iata", ""),
            "gare_origine": ligne.get("gare_origine", ""), "gare_destination": ligne.get("gare_destination", ""),
            "desserte": ligne.get("desserte_score", ""),
            "km": round(res.km, 1), "km_300": round(km_relevees, 1),
            "statut": ligne.get("statut", ""),
            "segments": sorted(set(ids)),
        }
        for c in ("temps_theorique", "temps_theorique_350", "temps_score_min", "temps_score", "temps_score_max",
                  "temps_score_350", "nb_arret_inter_min", "nb_arret_inter", "nb_arret_inter_max",
                  "distance_km_min", "distance_km_max"):
            od[c] = _nombre(ligne.get(c))
        nom = ligne.get("relation") or "(sans relation)"
        relations.setdefault(nom, {"nom": nom, "od": []})["od"].append(od)
    liste = []
    for r in relations.values():
        r["od"].sort(key=lambda o: (o["sous_relation"], o["gare_origine"], o["gare_destination"]))
        r["segments"] = sorted({s for o in r["od"] for s in o["segments"]})
        r["gain_max"] = max((o["temps_theorique"] - o["temps_theorique_350"]) for o in r["od"])
        liste.append(r)
    liste.sort(key=lambda r: r["nom"])
    return {"segments": segments, "relations": liste, "seuil": SEUIL_RELEVE}


def ecrire(resultat: dict, dossier: Path, suffixe: str | None = None) -> Path:
    dossier.mkdir(parents=True, exist_ok=True)
    suffixe = suffixe or date.today().isoformat()
    d = donnees(resultat)
    sources = resultat.get("sources", {})
    d["sources"] = " ; ".join(f"{s['description']} (consulté le {s['date_consultation']})"
                              for s in sources.values() if isinstance(s, dict) and "description" in s)
    d["date"] = suffixe
    chemin = dossier / f"carte_350_{suffixe}.html"
    chemin.write_text(_HTML.replace("__DONNEES__", json.dumps(d, ensure_ascii=False, separators=(",", ":"))),
                      encoding="utf-8")
    return chemin


_HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gains à 350 km/h</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root { --fond: #ffffff; --texte: #1d2433; --doux: #5b6475; --trait: #e3e6ec; --accent: #c8102e; --bleu: #2b5c9e; }
  @media (prefers-color-scheme: dark) {
    :root { --fond: #171b22; --texte: #e8ebf0; --doux: #9aa3b2; --trait: #2c333e; --accent: #ff5a6e; --bleu: #7aa7e6; }
  }
  html, body { margin: 0; height: 100%; background: var(--fond); color: var(--texte);
               font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
  #app { display: flex; height: 100%; }
  #panneau { flex: 0 0 420px; max-width: 45vw; min-width: 0; display: flex; flex-direction: column; border-right: 1px solid var(--trait); }
  #carte { flex: 1 1 auto; min-width: 0; }
  header { padding: 12px 16px; border-bottom: 1px solid var(--trait); }
  h1 { font-size: 16px; margin: 0 0 4px; }
  .doux { color: var(--doux); font-size: 12px; }
  #recherche { width: 100%; box-sizing: border-box; margin-top: 8px; padding: 6px 8px; border: 1px solid var(--trait);
               border-radius: 6px; background: var(--fond); color: var(--texte); }
  #contenu { overflow: auto; flex: 1; }
  .rel { padding: 8px 16px; border-bottom: 1px solid var(--trait); cursor: pointer; }
  .rel:hover, .rel.actif { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .rel b { display: block; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { padding: 4px 6px; border-bottom: 1px solid var(--trait); text-align: right; white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; white-space: normal; }
  tbody tr { cursor: pointer; }
  tbody tr:hover, tbody tr.actif { background: color-mix(in srgb, var(--bleu) 14%, transparent); }
  .retour { margin: 8px 16px; cursor: pointer; color: var(--bleu); }
  .legende { background: var(--fond); color: var(--texte); padding: 6px 8px; border-radius: 6px; font-size: 12px;
             box-shadow: 0 1px 4px rgba(0,0,0,.3); }
  .legende i { display: inline-block; width: 18px; height: 4px; margin-right: 6px; vertical-align: middle; }
  @media (max-width: 700px) { #app { flex-direction: column-reverse; } #panneau { flex: 0 0 45%; max-width: none; border-right: 0; } }
</style>
</head>
<body>
<div id="app">
  <div id="panneau">
    <header>
      <h1>Relations qui gagnent du temps à 350 km/h</h1>
      <div class="doux" id="resume"></div>
      <input id="recherche" type="search" placeholder="Filtrer : relation, sous-relation, gare…">
    </header>
    <div id="contenu"></div>
  </div>
  <div id="carte"></div>
</div>
<script>
const D = __DONNEES__;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const ROUGE = css('--accent'), BLEU = css('--bleu');
const carte = L.map('carte', { preferCanvas: true }).setView([46.6, 2.4], 6);
// fonds sans clé, utilisables depuis un fichier ouvert en local (CARTO exige une clé dans ce cas)
const fonds = {
  'Plan gris': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 16, attribution: 'Fond © Esri, HERE, Garmin, © OpenStreetMap' }),
  'OpenStreetMap France': L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
    { maxZoom: 19, attribution: '© OpenStreetMap France, © contributeurs OpenStreetMap' }),
  'Topographique': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19, attribution: 'Fond © Esri' }),
};
fonds['Plan gris'].addTo(carte);
const surcouches = {
  'OpenRailwayMap (vitesses)': L.tileLayer('https://{s}.tiles.openrailwaymap.org/maxspeed/{z}/{x}/{y}.png',
    { maxZoom: 19, opacity: 0.7, attribution: '© OpenRailwayMap' }),
};
L.control.layers(fonds, surcouches).addTo(carte);
const legende = L.control({ position: 'bottomright' });
legende.onAdd = () => { const d = L.DomUtil.create('div', 'legende');
  d.innerHTML = `<div><i style="background:${ROUGE}"></i>section à ${D.seuil} km/h ou plus (relevée à 350)</div>` +
                `<div><i style="background:${BLEU}"></i>autre section empruntée</div>`; return d; };
legende.addTo(carte);

// relations qui passent par chaque segment
const parSegment = D.segments.map(() => []);
D.relations.forEach((r, i) => r.segments.forEach(s => parSegment[s].push(i)));
const couleur = s => (D.segments[s].v || 0) >= D.seuil ? ROUGE : BLEU;
const traits = D.segments.map((s, i) => L.polyline(s.c, { color: couleur(i), weight: 3, opacity: 0.75 })
  .on('click', e => choisirSegment(i, e.latlng)).addTo(carte));

let selection = new Set();
function surligner(segments) {
  selection.forEach(i => traits[i].setStyle({ weight: 3, opacity: 0.75 }));
  selection = new Set(segments);
  const actif = selection.size > 0;
  traits.forEach((t, i) => t.setStyle(actif && !selection.has(i) ? { opacity: 0.15, weight: 2 } : { opacity: 0.75, weight: 3 }));
  selection.forEach(i => { traits[i].setStyle({ weight: 6, opacity: 1 }); traits[i].bringToFront(); });
  if (actif) carte.fitBounds(L.featureGroup([...selection].map(i => traits[i])).getBounds(), { padding: [30, 30], maxZoom: 11 });
}

const f = x => x == null ? '' : (Math.round(x * 10) / 10).toLocaleString('fr-FR');
const gain = (a, b) => a == null || b == null ? '' : f(a - b);
const contenu = document.getElementById('contenu'), recherche = document.getElementById('recherche');
const nbOd = D.relations.reduce((n, r) => n + r.od.length, 0);
document.getElementById('resume').textContent =
  `${D.relations.length} relations, ${nbOd} montées-descentes impactées. Calcul du ${D.date}. Sources : ${D.sources}`;

function liste() {
  const q = recherche.value.trim().toLowerCase();
  contenu.innerHTML = '';
  D.relations.forEach((r, i) => {
    const texte = (r.nom + ' ' + r.od.map(o => o.sous_relation + ' ' + o.gare_origine + ' ' + o.gare_destination).join(' ')).toLowerCase();
    if (q && !texte.includes(q)) return;
    const div = document.createElement('div');
    div.className = 'rel';
    div.innerHTML = `<b></b><span class="doux">${r.od.length} montées-descentes · gain théorique jusqu'à ${f(r.gain_max)} min</span>`;
    div.querySelector('b').textContent = r.nom;
    div.onclick = () => detail(i);
    contenu.appendChild(div);
  });
}

function detail(i) {
  const r = D.relations[i];
  surligner(r.segments);
  contenu.innerHTML = '';
  const retour = document.createElement('div');
  retour.className = 'retour'; retour.textContent = '← toutes les relations';
  retour.onclick = () => { surligner([]); carte.closePopup(); liste(); };
  contenu.appendChild(retour);
  const h = document.createElement('div');
  h.className = 'rel'; h.style.cursor = 'default';
  h.innerHTML = `<b></b><span class="doux">Temps en minutes. Théo. : temps théorique (vitesse max partout) ; score : plan de transport 2025 (min / médian / max) ; 350 : sections à 300 et 320 km/h relevées à 350 km/h. Cliquer une ligne pour voir son trajet.</span>`;
  h.querySelector('b').textContent = r.nom;
  contenu.appendChild(h);
  const t = document.createElement('table');
  t.innerHTML = `<thead><tr><th>Sous-relation / trajet</th><th>km</th><th>km ≥${D.seuil}</th><th>Théo.</th><th>Théo. 350</th>
    <th>Gain</th><th>Score min</th><th>Score</th><th>Score max</th><th>Score 350</th><th>Arrêts</th></tr></thead><tbody></tbody>`;
  const corps = t.querySelector('tbody');
  r.od.forEach(o => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><span class="doux"></span><br></td><td>${f(o.km)}</td><td>${f(o.km_300)}</td>
      <td>${f(o.temps_theorique)}</td><td>${f(o.temps_theorique_350)}</td><td>${gain(o.temps_theorique, o.temps_theorique_350)}</td>
      <td>${f(o.temps_score_min)}</td><td>${f(o.temps_score)}</td><td>${f(o.temps_score_max)}</td><td>${f(o.temps_score_350)}</td>
      <td>${[o.nb_arret_inter_min, o.nb_arret_inter, o.nb_arret_inter_max].map(x => x ?? '').join(' / ')}</td>`;
    tr.querySelector('.doux').textContent = o.sous_relation;
    tr.cells[0].append(`${o.gare_origine} → ${o.gare_destination}`);
    tr.title = (o.desserte || '') + (o.distance_km_min != null ? `\nkm train le plus rapide : ${f(o.distance_km_min)}` : '') +
               (o.distance_km_max != null ? `\nkm train le plus lent : ${f(o.distance_km_max)}` : '') + `\nstatut : ${o.statut}`;
    tr.onclick = () => { corps.querySelectorAll('tr').forEach(x => x.classList.remove('actif')); tr.classList.add('actif'); surligner(o.segments); };
    corps.appendChild(tr);
  });
  contenu.appendChild(t);
}

function choisirSegment(s, latlng) {
  const rels = parSegment[s];
  const div = document.createElement('div');
  div.innerHTML = `<b>${rels.length} relation${rels.length > 1 ? 's' : ''} sur cette voie</b>` +
    `<div class="doux">v max : ${D.segments[s].v ?? 'inconnue'} km/h</div>`;
  rels.forEach(i => { const a = document.createElement('a'); a.href = '#'; a.style.display = 'block';
    a.textContent = D.relations[i].nom; a.onclick = ev => { ev.preventDefault(); detail(i); }; div.appendChild(a); });
  L.popup({ maxHeight: 260 }).setLatLng(latlng).setContent(div).openOn(carte);
}

recherche.oninput = liste;
liste();
</script>
</body>
</html>
"""
