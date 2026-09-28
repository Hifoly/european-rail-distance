# Sources de données

Règle du projet : ne jamais mélanger les sources dans une même distance. Une relation est
calculée par un seul moteur ; l'autre source ne sert qu'au contrôle.

## SNCF Réseau open data

Portail : https://ressources.data.sncf.com (Opendatasoft). Accès public, sans compte.
Export d'un jeu complet : `GET {base_url}/catalog/datasets/{id}/exports/{format}` ;
métadonnées (date de mise à jour, licence) : `GET {base_url}/catalog/datasets/{id}`.

| Clé | Jeu | Champs utilisés |
|---|---|---|
| lignes | `formes-des-lignes-du-rfn` (GeoJSON) | code_ligne, mnemo (statut : on garde EXPLOITE), pk_debut_r, pk_fin_r, géométrie |
| vitesses | `vitesse-maximale-nominale-sur-ligne` (GeoJSON) | code_ligne, v_max (km/h), pkd, pkf, géométrie |
| gares | `liste-des-gares` (CSV `;`) | code_uic, libelle, code_ligne, pk, x_wgs84, y_wgs84 |

Constats sur les fichiers du 2026-09-28 :
- aucun jeu n'indique si une ligne est une LGV ; le classement se fait par la vitesse ;
- `liste-des-gares` a une ligne par couple gare × ligne ; Paris-Montparnasse et
  Marne-la-Vallée-Chessy en sont absentes ;
- les tracés n'ont ni la LGV Interconnexion Est (Roissy–Chessy) ni le raccordement de Pasilly ;
- PK incohérents sur certains tronçons (ligne 590000) ;
- le jeu des vitesses couvre 99 % des km exploités.

## RINF (Registre de l'infrastructure, ERA)

Le RINF est publié par l'Agence de l'Union européenne pour les chemins de fer sous forme
de graphe de connaissances interrogeable en SPARQL
(https://rinf.data.era.europa.eu/api/v1/sparql/rinf, vérifié le 2026-09-28).
Il couvre les 7 pays du projet de façon homogène (la Suisse y contribue).

- `queries/rinf_sections.rq` : sections de ligne (era:SectionOfLine) d'un pays, avec
  longueur (era:lengthOfSectionOfLine, en km), points d'exploitation de début et de fin
  (uopid), ligne (era:nationalLine/era:lineId) et vitesse maximale des voies
  (max de era:maximumPermittedSpeed sur les voies era:hasPart).
- `queries/rinf_points.rq` : points d'exploitation (era:OperationalPoint) avec nom,
  position (era:netReference, wgs84 lat/long) et type.
- Les objets du graphe sont versionnés (era:validity) : les requêtes ne gardent que la
  version valide le jour du téléchargement.
- Environ 1 500 sections françaises (souvent autour des faisceaux) ont une longueur déclarée
  plus courte que la ligne droite entre leurs points d'exploitation, parfois 0 km : le plus court
  chemin s'y engouffrait et RINF sortait 2 à 8 % trop court. Ces sections prennent la longueur de
  la ligne droite (constat du 2026-09-28 ; Bordeaux–Toulouse passe de 238,7 à 256,4 km, PK SNCF 256,4).

Les requêtes sont paginées (`taille_page` dans `settings.yaml`). La longueur est attendue
en mètres ; si la médiane des longueurs est inférieure à 100, le code considère qu'elles
sont en km et le signale.

**À confirmer au premier lancement** : l'adresse exacte du point d'accès SPARQL et les
noms de propriétés, qui dépendent de la version de l'ontologie ERA en service.

## Plus tard

Pour les autres pays, les open data des gestionnaires (DB InfraGO, RFI, Adif, Infrabel,
CFF, Infraestruturas de Portugal) pourront jouer le rôle de contrôle que joue SNCF
Réseau pour la France : un module `sources/<gestionnaire>.py` qui produit des
`Troncon` et `TronconVitesse` (voir `reseau.py`) suffit. OpenRailwayMap / OSM seulement
pour combler une géométrie manquante, jamais comme source de distance.

## Licences

Vérifier la licence de chaque jeu avant diffusion ; elle est enregistrée dans le
`manifest.json` de chaque téléchargement SNCF.
