"""Stable-key logic for Stage 1 deduplication.

Two keys, two jobs (see the brief: "the dedup key is the one detail to get
right early"):

  * dedup_key (HARD, UNIQUE) — 'source:native_id'. Identifies an offer uniquely
    *within* a source. Stops the exact same posting being re-inserted on every
    run. This is the one the DB enforces.

  * fingerprint (SOFT, non-unique) — a normalized 'company|title|location'.
    Identifies what is probably the *same role* even when it's reposted on a
    different source with a different id/url. Stored alongside, not enforced, so
    a later stage can surface likely cross-source duplicates without us ever
    blocking a legitimately distinct role.

Normalization is deliberately conservative: lowercase, strip accents, collapse
whitespace, drop common noise tokens. The goal is to catch obvious reposts, not
to be clever — over-aggressive normalization collapses genuinely different roles.
"""

from __future__ import annotations

import re
import unicodedata


def _strip_accents(text: str) -> str:
    """Remove accents so 'Systme' and 'Système' fingerprint the same."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str | None) -> str:
    """Lowercase, de-accent, collapse whitespace/punctuation to single spaces."""
    if not text:
        return ""
    text = _strip_accents(text).lower()
    # Replace any run of non-alphanumeric chars with a single space.
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def dedup_key(source: str, native_id: str) -> str:
    """HARD unique key for a posting within a source.

    `native_id` must be the source's own stable identifier for the offer
    (France Travail offer id, etc.) — never a URL with tracking params.
    """
    return f"{normalize(source)}:{str(native_id).strip()}"


def fingerprint(company: str | None, title: str | None, location: str | None) -> str:
    """SOFT key: normalized company|title|location for cross-source repost detection.

    Location is reduced to its first token after normalization (city), so
    'Paris 15e' and 'Paris' don't split a repost into two — but we keep company
    and title intact so two different roles at the same company stay distinct.
    """
    loc_norm = normalize(location)
    loc_city = loc_norm.split(" ")[0] if loc_norm else ""
    return "|".join([normalize(company), normalize(title), loc_city])


if __name__ == "__main__":
    # Quick self-check.
    assert dedup_key("FranceTravail", " 188XYZ ") == "francetravail:188XYZ"
    assert fingerprint("RENAULT", "Ingénieur Validation", "Paris 15e") == \
        fingerprint("renault", "ingenieur  validation", "paris")
    assert fingerprint("Acme", "Backend Eng", "Lyon") != \
        fingerprint("Acme", "Frontend Eng", "Lyon")
    print("dedup self-check OK")
