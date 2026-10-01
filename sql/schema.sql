-- Schéma PostgreSQL / PostGIS du distancier.
-- Chaque exécution de « distancier charger-bdd » crée un calcul ; les relations et
-- leur répartition par vitesse y sont rattachées (historique conservé).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS distancier;

CREATE TABLE IF NOT EXISTS distancier.gare (
    uic         text PRIMARY KEY,
    nom         text NOT NULL,
    pays        char(2) NOT NULL,
    uopid_rinf  text,
    geom        geometry(Point, 4326),
    note        text
);

CREATE TABLE IF NOT EXISTS distancier.calcul (
    id          serial PRIMARY KEY,
    cree_le     timestamptz NOT NULL DEFAULT now(),
    fichier     text NOT NULL,
    meta        jsonb           -- sources, dates de consultation, paramètres
);

CREATE TABLE IF NOT EXISTS distancier.relation (
    calcul_id               int NOT NULL REFERENCES distancier.calcul(id) ON DELETE CASCADE,
    id                      int NOT NULL,
    uic_origine             text NOT NULL,
    uic_destination         text NOT NULL,
    itineraire_retenu       text,
    distance_km             numeric(8,1),
    part_lgv_pct            numeric(5,1),
    type_ligne              text,
    lignes_empruntees       text,
    distance_plus_courte_km numeric(8,1),
    distance_sans_lgv_km    numeric(8,1),
    distance_tgv_commercial_km numeric(8,1),
    desserte_tgv            text,
    moteur                  text,
    distance_controle_km    numeric(8,1),
    source_controle         text,
    ecart_controle_pct      numeric(6,2),
    controle_pk_km          numeric(8,1),
    ecart_pk_pct            numeric(6,2),
    statut                  text,
    source                  text,
    date_consultation       date,
    remarques               text,
    PRIMARY KEY (calcul_id, id)
);
-- bases créées avant l'itinéraire « TGV commercial »
ALTER TABLE distancier.relation ADD COLUMN IF NOT EXISTS distance_tgv_commercial_km numeric(8,1);
ALTER TABLE distancier.relation ADD COLUMN IF NOT EXISTS desserte_tgv text;

-- Format long : une ligne par relation et par vitesse maximale (NULL = inconnue).
CREATE TABLE IF NOT EXISTS distancier.relation_vitesse (
    calcul_id   int NOT NULL,
    id          int NOT NULL,
    v_max_kmh   int,
    km          numeric(8,1) NOT NULL,
    FOREIGN KEY (calcul_id, id) REFERENCES distancier.relation(calcul_id, id) ON DELETE CASCADE
);

-- Dernier calcul, prêt pour les KPI.
CREATE OR REPLACE VIEW distancier.relation_courante AS
SELECT r.*, o.nom AS gare_origine, o.pays AS pays_origine, d.nom AS gare_destination, d.pays AS pays_destination
FROM distancier.relation r
JOIN distancier.gare o ON o.uic = r.uic_origine
JOIN distancier.gare d ON d.uic = r.uic_destination
WHERE r.calcul_id = (SELECT max(id) FROM distancier.calcul);
