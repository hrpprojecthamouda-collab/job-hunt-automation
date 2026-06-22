"""LLM scoring for Stage 2 — score one job against the master profile.

Design (grounded in the Claude API):
  * Model: claude-haiku-4-5. Ranking is a high-volume, well-structured
    classification task (score + short rationale per job); Haiku is the right
    tier and ~5x cheaper than Opus across 90+ jobs/run.
  * Structured outputs (output_config.format + json_schema) so every response
    is valid, parseable JSON — no scraping the model's prose.
  * Prompt caching on the profile/rubric system prefix — it's byte-identical
    across every job in a run, so we pay to build the cache once and read it
    cheaply thereafter.
  * Per-track rubric: a job's `track` ('core' | 'bridge') selects which bar it
    is scored against, so embedded roles and AI-adjacent stretch roles aren't
    judged the same way.

The profile's honesty split is load-bearing: the model is told it may only
credit PROVEN skills as expertise and must treat DEVELOPING skills as
learning-in-progress — so scores don't reward jobs that need expertise the
candidate doesn't yet have.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024

# Response schema — guarantees the three fields Stage 2 writes back.
SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {
            "type": "integer",
            "description": "0-100 fit against the rubric for this job's track.",
        },
        "rationale": {
            "type": "string",
            "description": "2-3 sentences: why this score. Name the strongest "
            "match and the biggest gap. Be concrete and honest.",
        },
        "track_assessment": {
            "type": "string",
            "enum": ["core", "bridge"],
            "description": "Which track this job truly belongs to on reflection "
            "(may correct the sourcing guess).",
        },
    },
    "required": ["fit_score", "rationale", "track_assessment"],
    "additionalProperties": False,
}


def build_system_prompt(profile: dict[str, Any], thresholds: dict[str, int]) -> str:
    """The cached rubric prefix — identical for every job in a run.

    Embeds the full profile and the two-track scoring instructions. Kept stable
    (no per-job content, no timestamps) so prompt caching actually hits.
    """
    return f"""You are a precise job-fit scorer for a real candidate's job hunt. \
Score each job 0-100 on how well it fits THIS candidate, using the rubric below. \
Return ONLY the structured fields requested.

# The candidate (master profile)
{json.dumps(profile, ensure_ascii=False, indent=2)}

# How to score — read carefully
- The profile splits skills into PROVEN (defensible in an interview today) and \
DEVELOPING (actively learning, NOT yet expertise). You MAY credit proven skills \
as real strengths. You MUST treat developing skills as learning-in-progress — a \
job that REQUIRES expertise the candidate only has at "developing" level is a \
weaker fit, not a strong one. Do not reward overclaiming.
- Each job has a TRACK:
  * 'core'  = roles the candidate can land today on proven experience \
(integration/validation, embedded, automotive testing). Score against fit with \
PROVEN experience. A strong core match scores high.
  * 'bridge' = AI-adjacent stretch roles. Score against realistic reachability \
given proven experience PLUS developing skills as evidence of direction. A \
junior/career-changer-friendly AI role the candidate could plausibly get scores \
higher than a senior AI role requiring years of ML in production (which should \
score low — it's not reachable yet).
- Honor must-haves and deal-breakers if present in the profile. (Currently both \
are empty — no hard filters; reflect location/fit softly in the score.)
- Score bands map to status downstream: >= {thresholds['pursue']} = pursue, \
{thresholds['maybe']}-{thresholds['pursue'] - 1} = maybe, < {thresholds['maybe']} = skip. \
Calibrate so a genuinely promising, realistic role lands in 'pursue' and a \
clearly-out-of-reach or off-target role lands in 'skip'.
- Be honest and concrete in the rationale. Name the single strongest match and \
the single biggest gap. No hedging, no flattery."""


def score_job(
    client: anthropic.Anthropic,
    system_prompt: str,
    job: dict[str, Any],
) -> dict[str, Any]:
    """Score one job. Returns {'fit_score', 'rationale', 'track_assessment'}.

    `system_prompt` must be the same object across calls in a run so the cache
    prefix is byte-identical.
    """
    job_text = _format_job(job)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},  # cache the rubric prefix
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
        messages=[{"role": "user", "content": job_text}],
    )
    # output_config.format guarantees the first text block is valid JSON.
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _format_job(job: dict[str, Any]) -> str:
    """Render the per-job content (the volatile, non-cached part of the prompt)."""
    jd = (job.get("jd_text") or "").strip()
    if len(jd) > 6000:  # keep token cost bounded; the head carries the signal
        jd = jd[:6000] + " […truncated]"
    return f"""Score this job.

TRACK (sourcing guess): {job.get('track', 'unknown')}
TITLE: {job.get('title', '')}
COMPANY: {job.get('company_name', '')}
LOCATION: {job.get('location', '')}
SALARY: {job.get('salary') or '(not stated)'}

JOB DESCRIPTION:
{jd or '(no description available)'}"""
