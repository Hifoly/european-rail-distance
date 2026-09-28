"""Téléchargements HTTP avec reprise et manifeste de provenance."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from distancier import __version__

log = logging.getLogger(__name__)


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = f"distancier-fer/{__version__}"
    return s


def avec_reprise(fonction, essais: int = 4):
    """Appelle fonction() en réessayant après 2, 4, 8 s sur erreur réseau."""
    for i in range(essais):
        try:
            return fonction()
        except requests.RequestException as e:
            if i == essais - 1:
                raise
            attente = 2 ** (i + 1)
            log.warning("échec (%s), nouvel essai dans %s s", e, attente)
            time.sleep(attente)


def telecharger(url: str, dest: Path, params: dict | None = None, s: requests.Session | None = None,
                timeout: int = 600) -> Path:
    s = s or session()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    def _get():
        with s.get(url, params=params, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for bloc in r.iter_content(1 << 20):
                    f.write(bloc)

    log.info("téléchargement %s", url)
    avec_reprise(_get)
    tmp.replace(dest)
    return dest


def maintenant() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ecrire_manifeste(dossier: Path, contenu: dict) -> None:
    (dossier / "manifest.json").write_text(json.dumps(contenu, ensure_ascii=False, indent=2), encoding="utf-8")


def lire_manifeste(dossier: Path) -> dict:
    return json.loads((dossier / "manifest.json").read_text(encoding="utf-8"))
