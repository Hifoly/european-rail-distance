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
| voies | `fichier-de-formes-des-voies-du-reseau-ferre-national` (GeoJSON) | code_ligne, nom_voie, pk_debut_r, pk_fin_r, géométrie ; seulement pour les `lignes_complementaires` de corrections.yaml |

Constats sur les fichiers du 2026-09-28 :
- aucun jeu n'indique si une ligne est une LGV ; le classement se fait par la vitesse ;
- `liste-des-gares` a une ligne par couple gare × ligne ; Paris-Montparnasse et
  Marne-la-Vallée-Chessy en sont absentes ;
- les tracés n'ont ni la LGV Interconnexion Est (Roissy–Chessy) ni le raccordement de Pasilly ;
  plus généralement 66 codes de ligne du fichier des voies (tracés voie par voie, modifié en 2020)
  manquent aux tracés de lignes. Les 44 qui existent aussi dans RINF (donc exploités) sont repris
  du fichier des voies, voie la plus longue de chaque ligne (`lignes_complementaires`), dont
  226310 Interconnexion Est, 262000 Douai–Valenciennes, 657000 Lamothe–Arcachon, 811000 à Sète.
  Sans vitesse dans le jeu des vitesses, leurs km vont en `dont_km_vitesse_inconnue` ;
- le raccordement 226305 (Chaulnes, LGV Nord vers la ligne 259000, voie de service à 50 km/h)
  est écarté (`lignes_exclues`) : il faisait passer les trajets Est–Nord par Laon et Chaulnes ;
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

**Espagne (constats du 2026-09-29).** 2 520 sections, 15 537 km : 11 227 km à 1 668 mm
(ibérique), 2 783 km à 1 435 mm (normal), 1 201 km à 1 000 mm (métrique), 326 km mixtes
(voies d'écartements différents ou troisième rail). L'écartement vient de era:wheelSetGauge
(libellé en mm) ; les codes de ligne RINF contiennent le numéro de ligne Adif (ESL**050**210000
= LAV Madrid–Barcelone–frontière, ligne 050). Défauts connus :
- Adif ne publie pas la vitesse des LAV (era:maximumPermittedSpeed « notYetAvailable ») : 24 % des
  km sans vitesse, presque toute la grande vitesse. Pour le choix d'itinéraire, les sections
  espagnoles à 1 435 mm sans vitesse comptent comme LGV (`lgv_presumee_ecartement`) ;
- trous dans les LAV : ligne 050 coupée entre Alcover-AV et Camp de Tarragona, et entre
  Barcelona-Sants et Riells ; ligne 080 (Madrid–Valladolid–Burgos) en trois morceaux ; LAV de
  Galice sans Sanabria AV ni A Gudiña ; Xàtiva, Cádiz et Huelva isolés du reste du réseau.
  Six trous sont comblés par des raccords en ligne droite dans `corrections.yaml` (2026-09-29,
  existence de la ligne vérifiée sur la carte 1 Adif) : Alcover-AV–Camp de Tarragona (11,1 km),
  Cenicero–Fuenmayor (Miranda–Logroño, 5,0 km), Benifaió–Silla (Xàtiva, 8,7 km), Bif. La Chana–
  Albolote (Granada–Moreda, 4,2 km), Cortadura–Río Arillo (Cádiz, 5,6 km), Puerta de Atocha–
  Atocha Cercanías (0,1 km). Restent sans raccord : Xàtiva–La Encina (Moixent sans section, trou de
  47 km), Huelva, Venta de Baños–Valladolid et Reus–Plana (détours RINF), traités par la carte Adif ;
- gares Renfe sans point RINF à leur code Adif : Medina del Campo AV, Sanabria AV,
  A Gudiña-Porta de Galicia, Puertollano, A Coruña-Turístico.

Les requêtes sont paginées (`taille_page` dans `settings.yaml`). La longueur est attendue
en mètres ; si la médiane des longueurs est inférieure à 100, le code considère qu'elles
sont en km et le signale.

**À confirmer au premier lancement** : l'adresse exacte du point d'accès SPARQL et les
noms de propriétés, qui dépendent de la version de l'ontologie ERA en service.

## Renfe (périmètre espagnol)

Horaires GTFS « alta velocidad, larga y media distancia » (data.renfe.com, jeu
`horarios-de-alta-velocidad-larga-distancia-y-media-distancia`, fichier sur ssl.renfe.com,
CC BY 4.0). Sert seulement à définir le périmètre (`distancier perimetre`), jamais à une distance :
une relation par couple de gares desservies par un même train des produits retenus
(`produits` dans `settings.yaml`). Les identifiants d'arrêt sont les codes de gare Adif :
point RINF = « ES » + code, UIC = « 71 » + code. Au 2026-09-29 : 162 gares, 1 460 relations
domestiques (AVE, AVLO, AVANT, ALVIA, Intercity, EUROMED, et la partie espagnole des AVE
internationaux).

## Adif (contrôle espagnol)

Adif ne publie pas de fichier de PK. Sa Declaración sobre la Red 2026 (www.adif.es, PDF) contient
le catalogue des lignes (annexe F : origine, destination, écartement, électrification, sans PK)
et une carte (fichier « 20260227_03_DR_Adif_2026_Mapas.pdf », page 5, carte 1, calques
« distancias AV » et « distancias Adif ») des distances en km entiers entre les principales
gares et bifurcations. Consultée le 2026-09-29.

Transcription (choix d'Aloïs le 2026-09-29 : graphe Adif en contrôle de toutes les relations et
en repli quand le RINF n'a pas de chemin) :

- `config/adif/carte1_2026.csv` : un tronçon par ligne (`de ; a ; km ; calque ; note`), 284
  tronçons ; `calque` = AV (Adif Alta Velocidad) ou Adif (réseau conventionnel). Texte et tracés
  extraits du PDF (pymupdf, calques OCG), relus à l'image et appariés à la main dans les nœuds.
- `note` signale ce qui n'est pas lu tel quel sur la carte : « lecture incertaine » (chiffre
  ambigu ou attribution douteuse), « non chiffré, estimé » (jonction dessinée sans chiffre), et
  quelques km estimés d'après le RINF quand la carte ne chiffre pas un lien (tunnel de Recoletos
  Chamartín–Atocha 8 km, València Nord–Joaquín Sorolla 2 km, coupure à Sagunt de Valencia–Castelló).
- `config/adif/noeuds.csv` : nœud de la carte -> point RINF (`uopid_rinf`) ou code UIC (gares
  absentes du RINF : Sanabria AV, Medina del Campo AV, Puertollano…), et coordonnées du point
  RINF (servent seulement à placer les gares absentes de la carte).
- Relectures après comparaison au RINF : jonctions de Pontevedra/Redondela/Vigo, Utrera,
  Sagunt, Tarragona–Sant Vicenç, Oviedo–Gijón ; Guillarei–Tui et Tarragona–Torredembarra retirés.

- `config/adif/carte3_2026.csv` : carte 3 du même PDF (page 7, « velocidad máxima » et type de
  voie, calques « Velocidades Adif / Velocidad AV » et « Vias Adif / Vias AV »), relue à l'image
  tronçon par tronçon le 2026-09-29 : vitesse maximale (246 tronçons sur 284) et écartement
  d'après la légende des voies (279 sur 284 ; `mixte` = 3e rail ou voies des deux écartements).
  Une lecture automatique préalable (étiquettes et couleurs rapprochées des tronçons) concordait
  avec le RINF à 91 % pour l'écartement et 77 % pour la vitesse : insuffisant, d'où la relecture.
  À noter : les LAV d'Estrémadure, Ourense–Santiago et l'axe atlantique sont à 1 668 mm.

Limites connues : la carte ne chiffre que les grands nœuds (622 relations sur 1 460 ont une gare
placée par approximation) ; l'Asturies (branches Villabona et Avilés) et Avilés ne sont pas
chiffrés ; La Isla–Mérida (6 km) et Los Rosales–Sevilla (27 km) sont plus courts que le vol
d'oiseau entre les points RINF (lecture ou attribution à revoir).

## Plus tard

Pour les autres pays, les open data des gestionnaires (DB InfraGO, RFI, Adif, Infrabel,
CFF, Infraestruturas de Portugal) pourront jouer le rôle de contrôle que joue SNCF
Réseau pour la France : un module `sources/<gestionnaire>.py` qui produit des
`Troncon` et `TronconVitesse` (voir `reseau.py`) suffit. OpenRailwayMap / OSM seulement
pour combler une géométrie manquante, jamais comme source de distance.

## Licences

Vérifier la licence de chaque jeu avant diffusion ; elle est enregistrée dans le
`manifest.json` de chaque téléchargement SNCF.
