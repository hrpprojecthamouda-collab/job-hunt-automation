"""LLM draft generation for Stage 4 — tailored CV + cover letter per role.

GATED stage: everything produced here is a CANDIDATE for human review, never a
finished action. The orchestrator writes drafts with status='needs_review'.

The cardinal rule (enforced in the prompt): the model may ONLY draw on the
candidate's real `cv_source_material` and proven skills. It SELECTS and
RE-EMPHASIZES to fit each job — it must never invent experience, metrics,
skills, or claims. Fabrication here is the worst failure mode (it gets the
candidate caught in interviews), so the instruction is explicit and repeated.

Design:
  * Model: claude-opus-4-8 — drafting is generative, low-volume (3 roles), and
    quality-critical (it carries the candidate's identity). Worth the top model.
  * Output: French CV variant + French cover letter, both Markdown.
  * Tailoring mode: RE-EMPHASIZE ONLY — same facts, reordered/reweighted to
    foreground what each JD values. No rewriting of facts, no new claims.
  * Structured output so CV and letter come back as separate, cleanly-stored fields.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic

MODEL = "claude-opus-4-8"
MAX_TOKENS = 8000

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "cv_markdown": {
            "type": "string",
            "description": "A tailored CV variant in FRENCH, Markdown. Same facts "
            "as the source material, re-emphasized for this role. No invented content.",
        },
        "cover_letter_markdown": {
            "type": "string",
            "description": "A cover letter in FRENCH, Markdown, addressed to this "
            "employer, grounded only in real experience.",
        },
        "tailoring_notes": {
            "type": "string",
            "description": "1-2 sentences for the human reviewer: what was "
            "emphasized for this role and why. Plain text.",
        },
    },
    "required": ["cv_markdown", "cover_letter_markdown", "tailoring_notes"],
    "additionalProperties": False,
}


def build_system_prompt(profile: dict[str, Any]) -> str:
    """Cached prefix: the candidate's real material + the hard no-invention rules."""
    return f"""Tu es un assistant qui rédige des candidatures en FRANÇAIS pour un \
candidat réel. Tu produis une variante de CV et une lettre de motivation, \
adaptées à une offre précise.

# Matériau source du candidat (la SEULE source autorisée)
{json.dumps(profile, ensure_ascii=False, indent=2)}

# RÈGLES ABSOLUES (ne jamais enfreindre)
1. N'INVENTE RIEN. Tu peux uniquement SÉLECTIONNER et RÉORGANISER le contenu du \
matériau source ci-dessus. Interdit : inventer une expérience, un chiffre, une \
compétence, un diplôme, un résultat, ou une mission qui n'y figure pas.
2. Respecte la distinction proven / developing du profil. Les compétences \
"proven" peuvent être présentées comme une expertise réelle. Les compétences \
"developing" (IA, Flutter, Python comme cœur de métier) doivent être présentées \
honnêtement comme en cours d'acquisition / en apprentissage — JAMAIS comme une \
expertise établie. Ne sur-vends pas.
3. Mode d'adaptation = RÉ-ACCENTUATION SEULEMENT. Mêmes faits que le CV réel, \
mais réordonnés/repondérés pour mettre en avant ce que cette offre valorise. Ne \
reformule pas les faits eux-mêmes au point de les déformer.
4. Là où le matériau source contient des marqueurs "TODO — add metrics", \
n'invente pas de chiffre : laisse un placeholder clair entre crochets \
(ex. « [à compléter : nombre d'ECU] ») que le candidat remplira.
5. Ton : direct, concret, sans jargon creux ni flatterie. Le candidat relira et \
validera tout — ton rôle est de produire un BROUILLON solide et honnête.

Le résultat est un CANDIDAT pour relecture humaine, pas un envoi final."""


def draft_for_job(
    client: anthropic.Anthropic,
    system_prompt: str,
    job: dict[str, Any],
    company: dict[str, Any] | None,
) -> dict[str, Any]:
    """Generate CV + cover-letter drafts for one job. Returns the parsed schema dict."""
    user_content = _format_job_and_company(job, company)
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ],
        output_config={"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
        messages=[{"role": "user", "content": user_content}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _format_job_and_company(job: dict[str, Any], company: dict[str, Any] | None) -> str:
    jd = (job.get("jd_text") or "").strip()
    if len(jd) > 8000:
        jd = jd[:8000] + " […tronqué]"
    company_block = "(employeur non enrichi)"
    if company:
        company_block = (
            f"Nom : {company.get('name')}\n"
            f"Taille : {company.get('size')}\n"
            f"Secteur / contexte : {company.get('tech_stack')}\n"
            f"Actualité : {company.get('recent_news')}\n"
            f"Notes : {company.get('notes')}"
        )
    return f"""Rédige une variante de CV et une lettre de motivation en français \
pour cette offre.

# Offre
INTITULÉ : {job.get('title', '')}
LIEU : {job.get('location', '')}
SCORE DE PERTINENCE (interne) : {job.get('fit_score')} / 100
JUSTIFICATION (interne) : {job.get('rationale', '')}

DESCRIPTION DU POSTE :
{jd or '(pas de description)'}

# Employeur (enrichissement Stage 3)
{company_block}

Adapte la mise en avant des compétences et expériences réelles du candidat à \
CETTE offre et CET employeur. Rappel : ré-accentuation seulement, aucune invention."""
