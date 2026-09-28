# Méthode de calcul

## Moteur « sncf » (tracés géographiques)

1. **Graphe.** Les tracés des lignes exploitées sont projetés en mètres (ETRS89-LAEA,
   EPSG:3035). Chaque tracé est coupé là où l'extrémité d'un autre tracé le touche à moins
   de 150 m (bifurcations en T) ; les extrémités à moins de 150 m sont fusionnées en un nœud.
   Avec 50 m, des tracés dessinés en décalage restaient déconnectés (ex. ligne 500000 à
   Saintes, 122 m : Nantes–Bordeaux passait par Tours).
2. **Longueurs.** La longueur de chaque arête est géodésique (ellipsoïde GRS80), calculée
   sur les coordonnées d'origine : la projection ne sert qu'à la topologie.
3. **Vitesses.** Tous les 100 m, on lit la vitesse maximale nominale du tronçon de
   vitesse le plus proche (à moins de 25 m, même code de ligne en priorité). Sans tronçon
   trouvé, la vitesse est « inconnue ».
4. **Gares.** Chaque gare est rattachée au point le plus proche de la voie (à moins de
   300 m), de préférence sur une des lignes où SNCF Réseau la situe. Au-delà de 100 m, une
   remarque le signale.

## Moteur « rinf » (graphe topologique)

Nœuds = points d'exploitation, arêtes = sections de ligne avec leur longueur officielle et
leur vitesse maximale. Une gare est associée par `uopid_rinf` (dans `gares.csv`) ou, à
défaut, au point d'exploitation le plus proche dans un rayon de 2 km ; l'association est
alors écrite en remarque pour être figée ensuite.

## Itinéraire

Plus court chemin (Dijkstra) selon le champ `itineraire` de la relation :
- `plus_court` : distance minimale ;
- `grande_vitesse` : les arêtes dont au moins la moitié est à v_max ≥ 250 km/h comptent
  pour 0,75 de leur longueur, ce qui reproduit le choix d'un TGV d'emprunter la LGV même
  un peu plus longue. La distance publiée reste la distance réelle.

Sont aussi calculés, pour information, le plus court chemin et le plus court chemin sans
LGV.

## Répartition par vitesse

`dont_km_<v>` = km du trajet dont la vitesse maximale nominale vaut v. Il y a une colonne
par valeur présente dans les sources, même à 0, pour que le format ne change pas quand on
ajoute des relations. Les arrondis sont répartis pour que la somme des colonnes
`dont_km_*` égale exactement `distance_km`. Il s'agit de la vitesse permise par
l'infrastructure, pas de la vitesse commerciale des trains.

`part_lgv_pct` et `type_ligne` utilisent le seuil de 250 km/h (définition UE des lignes
nouvelles à grande vitesse) : LGV au-delà de 95 %, classique sous 5 %, mixte entre les deux.

## Contrôles et statut

| statut | condition |
|---|---|
| `vérifié (RINF)` / `vérifié (SNCF)` | l'autre moteur donne la même distance à 1 % près |
| `vérifié (PK SNCF)` | ≥ 95 % du trajet sur une ligne, et l'écart des PK des deux gares sur cette ligne est à 1 % près |
| `à vérifier` | raccordement manuel emprunté, repli sur RINF (voir ci-dessous), écart avec le contrôle supérieur à `seuil_alerte_pct` (10 %, trou probable dans un des réseaux), ou statut forcé dans `relations.csv` |
| `estimé` | aucun contrôle concluant |
| `erreur : …` | gare inconnue, source absente ou pas d'itinéraire |

**Repli sur RINF.** Quand le moteur SNCF ne peut pas calculer une relation française (gare à
plus de `distance_max_rattachement_m` de toute voie SNCF, ou pas d'itinéraire), la distance est
calculée sur RINF, sans contrôle SNCF, et la relation est marquée `à vérifier`. Cas connus au
2026-09-28 : Marne-la-Vallée-Chessy (Interconnexion Est absente des tracés SNCF), Arcachon et
La Teste (branche Lamothe–Arcachon absente).

**Détour SNCF.** Quand la distance SNCF dépasse la distance RINF de plus de `seuil_alerte_pct`
(10 %), une ligne manque probablement aux tracés SNCF : la distance RINF est retenue, sans
contrôle, et la relation est `à vérifier` (ex. Douai–Valenciennes : SNCF 68,0 km par détour, RINF
35,4 km ; la ligne Douai–Somain–Valenciennes est absente des tracés au 2026-09-28). Quand c'est
RINF qui est plus long, la distance SNCF est gardée et la relation est `à vérifier`.

## Résultats de référence (POC du 2026-09-28)

Recalculées avec ce code sur les fichiers SNCF du 2026-09-28, les 20 relations du POC
donnent les mêmes distances à 0,6 km près, sauf Paris-Gare-de-Lyon–Montpellier-St-Roch
(738,7 km au lieu de 741,8) : le contournement Nîmes–Montpellier est limité à 220 km/h,
il n'est donc plus privilégié comme LGV et l'itinéraire reprend la ligne classique
après Nîmes. Écart aux PK SNCF : +0,09 % sur Bordeaux–Toulouse, −0,15 % sur Marseille–Nice.
