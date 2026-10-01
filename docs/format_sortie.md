# Format du tableau de sortie

`data/output/distances_<date>.csv` : séparateur `;`, UTF-8 avec BOM (s'ouvre directement
dans Excel), une ligne par relation. Même contenu dans l'onglet « distances » du `.xlsx`.

| Colonne | Contenu |
|---|---|
| id | identifiant de la relation (`config/relations.csv`) |
| gare_origine, code_uic_origine, pays_origine | gare de départ (nom officiel, UIC, code pays ISO) |
| gare_destination, code_uic_destination, pays_destination | gare d'arrivée |
| itineraire_retenu | `grande_vitesse` ou `plus_court` |
| distance_km | distance réelle par le rail, km, 1 décimale |
| dont_km_320 … dont_km_10 | km à chaque vitesse maximale nominale (une colonne par valeur des sources) |
| dont_km_vitesse_inconnue | km sans vitesse connue |
| part_lgv_pct | part du trajet à v_max ≥ 250 km/h |
| type_ligne | LGV, mixte ou classique |
| lignes_empruntees | codes de ligne et km, dans l'ordre du trajet |
| distance_plus_courte_km | plus court chemin |
| distance_sans_lgv_km | plus court chemin sans LGV |
| distance_tgv_commercial_km | km parcourus par le TGV direct le plus fréquent : itinéraire grande vitesse d'arrêt en arrêt (vide sans TGV direct ou sans horaires téléchargés) |
| desserte_tgv | arrêts de ce TGV et nombre de trains qui la font, ou « aucun TGV direct » |
| moteur | `sncf` ou `rinf` |
| distance_controle_km, source_controle, ecart_controle_pct | calcul par l'autre source |
| controle_pk_km, ecart_pk_pct | contrôle par les points kilométriques SNCF |
| statut | vérifié (…), estimé, à vérifier, erreur |
| source | jeux de données et API utilisés |
| date_consultation | date du téléchargement des données |
| remarques | limites connues, rattachements, corrections |

Fichiers associés :
- `distances_par_vitesse_<date>.csv` (onglet « par_vitesse ») : format long, une ligne par
  relation et par vitesse (id, gares, v_max_kmh, km, part_pct, source, date_consultation),
  pratique pour les tableaux croisés et SQL ;
- `calcul_<date>.json` : sources, dates et paramètres utilisés pour ce calcul.
