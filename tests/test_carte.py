import csv
import json
import re

from distancier import carte, config

COLS = ["relation", "sous_relation", "gare_origine", "code_uic_origine", "gare_destination", "code_uic_destination",
        "distance_km", "distance_km_min", "distance_km_max", "part_lgv_pct", "desserte_score", "desserte_score_min",
        "desserte_score_max", "temps_theorique", "temps_theorique_350", "temps_score_min", "temps_score",
        "temps_score_max", "temps_score_350", "nb_arret_inter", "statut"]


def _ligne(o, uo, d, ud, km, th, th350, s, s350, desserte, sr="P - Q"):
    return dict(zip(COLS, ["R", sr, o, uo, d, ud, km, km, km, "80", desserte, desserte, desserte,
                           th, th350, s, s, s, s350, "0", "vérifié (RINF)"]))


def test_carte_350(cfg):
    sorties = config.chemin(cfg, "sorties")
    sorties.mkdir(parents=True)
    tableau = sorties / "distances_srela_2026-01-01.csv"
    lignes = [
        _ligne("Pville", "87000001", "Qville", "87000002", "110", "25", "21.4", "40", "36.4", "Pville > Qville (100 %)"),
        # aucune vitesse >= 300 : pas de gain, sous-relation absente de la carte
        _ligne("Pville", "87000001", "Sville", "87000003", "60", "30", "30", "45", "45", "Pville > Sville (100 %)", sr="P - S"),
    ]
    with open(tableau, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, delimiter=";")
        w.writeheader()
        w.writerows(lignes)

    sortie = carte.ecrire(cfg)
    assert sortie.name == "carte_350_2026-01-01.html"
    html = sortie.read_text(encoding="utf-8")
    data = json.loads(re.search(r"const DATA = (\[.*?\]);\n", html).group(1))
    assert [s["sr"] for s in data] == ["P - Q"]
    s = data[0]
    assert s["gmax"] == 3.6 and s["n"] == 1
    assert s["od"][0]["g"] == 3.6 and s["od"][0]["gp"] == 9.0
    assert len(s["segs"]) == 1 and s["segs"][0]["g"] == 3.6
    assert s["segs"][0]["p"] == [[48.0, 2.0], [48.0, 3.0]]
