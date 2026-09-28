"""Collecte par API, avec un faux serveur (aucun accès réseau)."""
import json

from distancier import config
from distancier.sources import http, rinf, sncf


class Reponse:
    def __init__(self, donnees=None, contenu=b""):
        self.donnees, self.contenu = donnees, contenu

    def raise_for_status(self):
        pass

    def json(self):
        return self.donnees

    def iter_content(self, _):
        yield self.contenu

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FausseSession:
    def __init__(self):
        self.appels = []
        self.headers = {}

    def get(self, url, params=None, stream=False, timeout=None):
        self.appels.append(url)
        if "/exports/" in url:
            return Reponse(contenu=b"{}")
        return Reponse({"metas": {"default": {"modified": "2026-09-01T00:00:00Z", "license": "ODbL"}}})

    def post(self, url, data=None, timeout=None, headers=None):
        q = data["query"]
        offset = int(q.rsplit("OFFSET", 1)[1])
        n = 2 if offset == 0 else 1          # taille de page 2 : deux pages
        if "SectionOfLine" in q:
            rows = [{"sol": {"value": f"s{offset + i}"}, "longueur": {"value": "1000"}} for i in range(n)]
        else:
            rows = [{"uopid": {"value": f"FR{offset + i}"}} for i in range(n)]
        return Reponse({"results": {"bindings": rows}})


def test_telechargement_sncf(cfg, monkeypatch):
    fausse = FausseSession()
    monkeypatch.setattr(http, "session", lambda: fausse)
    dossier = sncf.telecharger(cfg, jour="2026-09-28")
    man = http.lire_manifeste(dossier)
    assert set(man["jeux"]) == {"lignes", "vitesses", "gares"}
    assert man["jeux"]["gares"]["url"].endswith("/catalog/datasets/liste-des-gares/exports/csv")
    assert man["jeux"]["lignes"]["modifie_a_la_source"] == "2026-09-01T00:00:00Z"
    assert config.dossier_source(cfg, "sncf") == dossier   # le plus récent


def test_telechargement_rinf_pagine(cfg, monkeypatch):
    fausse = FausseSession()
    monkeypatch.setattr(http, "session", lambda: fausse)
    cfg["sources"]["rinf"]["taille_page"] = 2
    dossier = rinf.telecharger(cfg, ["BE"], jour="2026-09-28")
    man = json.loads((dossier / "manifest.json").read_text())
    assert man["pays"]["BE"]["sections"] == 3 and man["pays"]["BE"]["points"] == 3
    assert (dossier / "sections_BE.csv").read_text().count("\n") == 4
