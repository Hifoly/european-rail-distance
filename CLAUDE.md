# Consignes pour Claude

- Répondre en français. Noms de gares officiels, distances en km.
- Ne jamais mélanger les sources dans une même distance : un moteur par relation, l'autre en contrôle.
- Toujours indiquer la source et la date de consultation ; distinguer vérifié et estimé.
- Format de sortie figé : distance_km puis une colonne dont_km_<v_max> par vitesse des sources,
  puis dont_km_vitesse_inconnue, puis les km_ecartement_* (voir docs/format_sortie.md). Ne pas réintroduire de colonnes
  km_lgv / km_classique.
- Tests : `pytest -q` (données simulées, sans réseau).
