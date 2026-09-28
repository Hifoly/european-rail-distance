"""Lecture de config/settings.yaml et résolution des chemins."""
from __future__ import annotations

import os
from pathlib import Path

import yaml


def charger(chemin: str | Path = "config/settings.yaml") -> dict:
    chemin = Path(chemin).resolve()
    cfg = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    cfg["_racine"] = chemin.parent.parent
    endpoint = os.environ.get("RINF_SPARQL_ENDPOINT")
    if endpoint:
        cfg["sources"]["rinf"]["endpoint"] = endpoint
    return cfg


def chemin(cfg: dict, cle: str) -> Path:
    p = Path(cfg["chemins"][cle])
    return p if p.is_absolute() else cfg["_racine"] / p


def dossier_source(cfg: dict, source: str, jour: str | None = None) -> Path:
    """Dossier data/raw/<source>/<jour>. Sans jour : le plus récent téléchargé."""
    base = chemin(cfg, "donnees_brutes") / source
    if jour:
        return base / jour
    jours = sorted(p for p in base.glob("*") if (p / "manifest.json").exists()) if base.exists() else []
    if not jours:
        raise FileNotFoundError(
            f"Aucune donnée {source} dans {base}. Lancer d'abord : distancier telecharger --source {source}"
        )
    return jours[-1]
