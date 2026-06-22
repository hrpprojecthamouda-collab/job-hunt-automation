"""France Travail "Offres d'emploi v2" source adapter.

Reliable, public-sector job API with broad FR/Paris coverage — the brief's
recommended first "reliable" source.

API shape (verified against francetravail.io docs + community wrappers, 2026-06):
  * Auth:   OAuth2 client_credentials grant.
            token URL: https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire
            scope:     "api_offresdemploiv2 o2dsoffre"
  * Base:   https://api.francetravail.io
  * Search: GET /partenaire/offresdemploi/v2/offres/search
            params: motsCles, commune | departement, range="start-end" (max 150/page, ~3150 cap)
            200 => full page, 206 => partial (last page), 204 => no results.
  * Limit:  ~10 calls/sec.

Credentials live in env vars (loaded from .env by the orchestrator):
  FRANCE_TRAVAIL_CLIENT_ID, FRANCE_TRAVAIL_CLIENT_SECRET

If credentials are absent the adapter raises MissingCredentials, which the
orchestrator logs as a skipped source — the rest of the run proceeds. This file
imports cleanly with no creds, so the logic is unit-testable offline.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SCOPE = "api_offresdemploiv2 o2dsoffre"
BASE_URL = "https://api.francetravail.io"
SEARCH_PATH = "/partenaire/offresdemploi/v2/offres/search"

PAGE_SIZE = 150          # API max per page
HARD_CAP = 1000          # safety cap on total fetched per run (tunable)
REQUEST_PAUSE = 0.15     # seconds between calls; stays well under 10 req/s


class MissingCredentials(RuntimeError):
    """Raised when FRANCE_TRAVAIL_CLIENT_ID/SECRET are not set."""


def _get_token() -> str:
    """OAuth2 client_credentials -> bearer token."""
    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID")
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise MissingCredentials(
            "Set FRANCE_TRAVAIL_CLIENT_ID and FRANCE_TRAVAIL_CLIENT_SECRET "
            "(register a free app at https://francetravail.io/inscription)."
        )
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": SCOPE,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _build_queries(search_cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """Turn the config's track query sets into (track, motsCles) pairs.

    One query per title keeps each request focused; motsCles does an OR-ish
    keyword match, so we issue the title as the primary search term per track.
    """
    queries: list[tuple[str, str]] = []
    for track, qset in search_cfg.get("tracks", {}).items():
        for title in qset.get("titles", []):
            queries.append((track, title))
    return queries


def _normalize_offer(offer: dict[str, Any], track: str) -> dict[str, Any]:
    """Map a raw France Travail offer to the adapter contract dict."""
    lieu = offer.get("lieuTravail") or {}
    entreprise = offer.get("entreprise") or {}
    salaire = offer.get("salaire") or {}
    return {
        "source": "france_travail",
        "native_id": str(offer.get("id", "")),
        "title": offer.get("intitule") or "",
        "company_name": entreprise.get("nom") or "(non précisé)",
        "location": lieu.get("libelle"),
        "url": (offer.get("origineOffre") or {}).get("urlOrigine"),
        "jd_text": offer.get("description"),
        "salary": salaire.get("libelle"),
        "date_posted": offer.get("dateCreation"),
        "track": track,
    }


def fetch(search_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch + normalize offers for all configured track queries.

    Raises MissingCredentials if not configured (orchestrator handles it);
    other request errors propagate and are caught per-source by the orchestrator.
    """
    token = _get_token()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    locations = search_cfg.get("locations", [])
    # France Travail filters by commune INSEE code or departement; for a first
    # cut we pass the location string as a keyword and rely on motsCles. (A later
    # refinement can map "Paris" -> departement 75, etc.)
    queries = _build_queries(search_cfg)

    results: list[dict[str, Any]] = []
    seen_native_ids: set[str] = set()  # in-run guard; DB enforces across runs

    for track, mots in queries:
        start = 0
        while start < HARD_CAP:
            end = start + PAGE_SIZE - 1
            params = {"motsCles": mots, "range": f"{start}-{end}"}
            if locations:
                params["motsCles"] = f"{mots} {locations[0]}"
            resp = session.get(
                BASE_URL + SEARCH_PATH, params=params, timeout=30
            )
            if resp.status_code == 204:  # no results for this query
                break
            resp.raise_for_status()
            offers = resp.json().get("resultats", []) or []
            for offer in offers:
                nid = str(offer.get("id", ""))
                if nid and nid not in seen_native_ids:
                    seen_native_ids.add(nid)
                    results.append(_normalize_offer(offer, track))
            # 206 = partial content = more pages may exist; 200 on a short page
            # also means we've reached the end.
            if resp.status_code != 206 or len(offers) < PAGE_SIZE:
                break
            start += PAGE_SIZE
            time.sleep(REQUEST_PAUSE)

    return results
