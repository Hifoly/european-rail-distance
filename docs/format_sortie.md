# Format du tableau de sortie

`data/output/distances_<date>.csv` : séparateur `;`, UTF-8 avec BOM (s'ouvre directement
dans Excel), une ligne par relation. Même contenu dans l'onglet « distances » du `.xlsx`.

| Colonne | Contenu |
|---|---|
| id | identifiant de la relation (`config/relations.csv`) |
| gare_origine, code_uic_origine, pays_origine | gare de départ (nom officiel, UIC, code pays ISO) |
| gare_destination, code_uic_destination, pays_destination | gare d'arrivée |
| itineraire_retenu | `tgv_commercial` (distance du TGV direct), sinon celui de `config/relations.csv` (`grande_vitesse` ou `plus_court`) |
| distance_km | distance retenue, km, 1 décimale : celle du TGV direct le plus fréquent s'il y en a un (`distance_tgv_commercial_km`), sinon `distance_au_plus_court_km`. Les colonnes `dont_km_*`, `part_lgv_pct`, `type_ligne`, `lignes_empruntees`, les contrôles et le statut portent sur cette distance |
| dont_km_320 … dont_km_10 | km à chaque vitesse maximale nominale (une colonne par valeur des sources) |
| dont_km_vitesse_inconnue | km sans vitesse connue |
| part_lgv_pct | part du trajet à v_max ≥ 250 km/h |
| type_ligne | LGV, mixte ou classique |
| lignes_empruntees | codes de ligne et km, dans l'ordre du trajet |
| distance_au_plus_court_km | distance par le rail sans tenir compte des dessertes : itinéraire de `relations.csv` (LGV privilégiée pour `grande_vitesse`) |
| distance_tgv_commercial_km | km parcourus par le TGV direct le plus fréquent : itinéraire grande vitesse d'arrêt en arrêt (vide sans TGV direct ou sans horaires téléchargés) |
| desserte_tgv | arrêts de ce TGV et nombre de trains qui la font, ou « aucun TGV direct » |
| temps_theorique | minutes, 1 décimale : temps pour parcourir `distance_km` en roulant partout à la vitesse maximale de chaque section (somme des `dont_km_<v>` / v). Les km sans vitesse connue comptent à la vitesse médiane du trajet (pondérée par les km) (remarque au-delà de 0,5 km) |
| temps_theorique_350 | comme `temps_theorique`, mais les sections à 300 ou 320 km/h comptées à 350 km/h |
| temps_pratique | minutes : durée médiane de tous les TGV directs entre les deux gares, quelle que soit leur desserte (horaires SNCF, départ de la première gare à l'arrivée à la seconde). Vide sans TGV direct |
| temps_score | minutes : temps médian de l'année 2025 dans le plan de transport TGV théorique (`data/Extract_score.xlsx`, fichier local), pondéré par le nombre de jours de chaque jour type et par le compteur (0,5 par tranche d'un train couplé), les deux sens réunis. Vide si le fichier est absent ou si l'OD n'y figure pas |
| temps_score_350 | `temps_score` moins le gain d'un relèvement à 350 km/h des sections à 300 ou 320 km/h, calculé tronçon par tronçon entre les arrêts du TGV, avec accélération (0,15 m/s²) et freinage (0,5 m/s²) : le gain réel est plus petit que le gain théorique, nul sur les LGV très courtes |
| distance_plus_courte_km | plus court chemin strict, sans préférence pour les LGV |
| distance_sans_lgv_km | plus court chemin sans LGV |
| moteur | `sncf` ou `rinf` |
| distance_controle_km, source_controle, ecart_controle_pct | calcul par l'autre source |
| controle_pk_km, ecart_pk_pct | contrôle par les points kilométriques SNCF |
| statut | vérifié (…), estimé, à vérifier, erreur. Distance TGV : « vérifié (RINF) » ou « vérifié (PK SNCF) » si le contrôle de tout le trajet concorde, sinon « vérifié (PK SNCF et RINF) »… si chaque tronçon est vérifié, sinon estimé ; un seul tronçon « à vérifier » suffit pour tout le trajet |
| source | jeux de données et API utilisés |
| date_consultation | date du téléchargement des données |
| remarques | limites connues, rattachements, corrections |

Fichiers associés :
- `distances_par_vitesse_<date>.csv` (onglet « par_vitesse ») : format long, une ligne par
  relation et par vitesse (id, gares, v_max_kmh, km, part_pct, source, date_consultation),
  pratique pour les tableaux croisés et SQL ;
- `calcul_<date>.json` : sources, dates et paramètres utilisés pour ce calcul.
