# Consignes pour Claude

- Répondre en français. Noms de gares officiels, distances en km.
- Ne jamais mélanger les sources dans une même distance : un moteur par relation, l'autre en contrôle.
- Toujours indiquer la source et la date de consultation ; distinguer vérifié et estimé.
- distance_km = distance du TGV direct le plus fréquent (horaires SNCF) s'il existe, sinon
  distance_au_plus_court_km (choix d'Aloïs le 2026-10-01).
- Format de sortie figé : distance_km puis une colonne dont_km_<v_max> par vitesse des sources,
  puis dont_km_vitesse_inconnue (voir docs/format_sortie.md). Ne pas réintroduire de colonnes
  km_lgv / km_classique.
- Tests : `pytest -q` (données simulées, sans réseau).
