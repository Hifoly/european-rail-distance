# Distancier FER européen

Base de distances ferroviaires réelles (par le rail, pas à vol d'oiseau) entre gares
de France, Allemagne, Italie, Espagne, Belgique, Suisse et Portugal, avec pour chaque
relation la répartition des km par vitesse maximale nominale de la ligne. Usage prévu :
calcul de KPI selon la distance du parcours.

Les données sont récupérées directement par API auprès des producteurs, sans dépôt
manuel de fichiers :

| Source | Accès | Rôle |
|---|---|---|
| SNCF Réseau open data | API Opendatasoft Explore v2.1 | moteur principal pour les relations France–France |
| RINF de l'ERA | SPARQL (ERA Knowledge Graph) | moteur principal pour les autres pays et le transfrontalier, contrôle pour la France |

Détails : [docs/sources.md](docs/sources.md), [docs/methodologie.md](docs/methodologie.md),
[docs/format_sortie.md](docs/format_sortie.md).

## Installation (Linux, WSL ou macOS)

```bash
git clone <url-du-depot> distancier-fer && cd distancier-fer
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # ajouter ,bdd pour PostgreSQL : pip install -e ".[dev,bdd]"
pytest -q                        # tests sur données simulées, sans réseau
```

Python 3.10 ou plus récent.

## Utilisation

```bash
distancier telecharger                  # SNCF Réseau + RINF des 7 pays -> data/raw/<source>/<date>/
distancier telecharger --source sncf    # une seule source
distancier telecharger --source rinf --pays FR BE
distancier calculer                     # -> data/output/distances_<date>.csv / .xlsx
distancier charger-bdd data/output/distances_2026-09-28.csv   # facultatif, PostGIS
```

Chaque téléchargement est rangé dans un dossier daté avec un `manifest.json` (URL,
date de consultation, date de mise à jour côté producteur, licence). `calculer` utilise
le téléchargement le plus récent, ou celui passé par `--jour-sncf` / `--jour-rinf`.
Le premier calcul construit le graphe SNCF (environ 30 s) puis le garde en cache dans
`data/interim/`.

Si une source n'a pas encore été téléchargée, les relations qui en dépendent sortent
avec un statut `erreur : …` et les autres sont calculées normalement.

## Ce qu'on modifie au quotidien

| Fichier | Contenu |
|---|---|
| `config/relations.csv` | les relations à calculer (UIC origine, UIC destination, itinéraire `grande_vitesse` ou `plus_court`) |
| `config/gares.csv` | référentiel des gares : UIC, nom officiel, pays, coordonnées (facultatives en France), `uopid_rinf` |
| `config/corrections.yaml` | raccordements absents des sources, lignes à exclure (dernier recours, toujours signalé) |
| `config/settings.yaml` | seuils (LGV = 250 km/h, écart de vérification 1 %…), moteur par pays, adresses des API |

Pour ajouter une relation : ajouter les deux gares dans `gares.csv` si elles n'y sont
pas, puis une ligne dans `relations.csv`, puis `distancier calculer`.

## Base PostgreSQL / PostGIS (facultatif)

Sous WSL (Ubuntu) :

```bash
sudo apt install postgresql postgis
sudo -u postgres createuser -P distancier
sudo -u postgres createdb -O distancier distancier
sudo -u postgres psql -d distancier -c "CREATE EXTENSION postgis"
cp .env.example .env    # renseigner DATABASE_URL
set -a; source .env; set +a
distancier charger-bdd data/output/distances_<date>.csv
```

Le schéma est dans [sql/schema.sql](sql/schema.sql) : une table `relation` (une ligne par
relation et par calcul), une table `relation_vitesse` (format long, une ligne par
vitesse) et une vue `relation_courante` sur le dernier calcul.

## Arborescence

```
config/        paramètres, référentiel de gares, relations, corrections
queries/       requêtes SPARQL RINF (paginées automatiquement)
sql/           schéma PostGIS
src/distancier/
  sources/     collecte par API (sncf.py, rinf.py, http.py)
  reseau.py    construction du graphe à partir des tracés
  routage.py   itinéraires, répartition des km par vitesse
  calcul.py    orchestration, contrôles croisés, statut vérifié / estimé
  export.py    CSV (;) et Excel
  db.py        chargement PostgreSQL
tests/         tests sur un petit réseau simulé
data/          téléchargements et résultats (non versionnés)
```

## Points ouverts

- **Adresse SPARQL du RINF à confirmer** : `sources.rinf.endpoint` dans
  `config/settings.yaml` (ou variable `RINF_SPARQL_ENDPOINT`). Les requêtes de `queries/`
  suivent l'ontologie ERA mais n'ont pas encore été exécutées contre le vrai service.
- Les tracés SNCF n'ont ni la LGV Interconnexion Est ni le raccordement de Pasilly
  (constaté le 2026-09-28) : Lille-Europe–Marseille et Paris–Dijon sont forcés à « à vérifier »
  dans `relations.csv`.
- Paris-Montparnasse est absente de la liste des gares SNCF : coordonnées saisies à la main
  dans `gares.csv`, code UIC à vérifier.
- Allemagne, Italie, Espagne, Belgique, Suisse, Portugal : RINF seul pour l'instant. Les
  open data des autres gestionnaires (DB InfraGO, RFI, Adif, Infrabel, CFF, IP) pourront
  servir de contrôle, sur le modèle de `sources/sncf.py`.
