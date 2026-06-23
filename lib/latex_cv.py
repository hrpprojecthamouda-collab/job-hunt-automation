"""Stage 4 (LaTeX variant) — generate a tailored CV from the user's real
YAAC LaTeX template, per role.

Why this design: we do NOT have the LLM emit raw LaTeX (it could break the .cls
macros or invent content). Instead:
  1. The real CV content lives in cv/ (the user's template) — the source of truth.
  2. The LLM produces only a small STRUCTURED tailoring plan per role:
       - a re-emphasized headline/profile paragraph (French), drawn only from
         real experience,
       - the order to present the 5 experiences (by stable id),
       - a one-line skills-emphasis note for the reviewer.
  3. We render that plan by COPYING the user's cv/ folder verbatim and swapping
     only section_headline.tex and the experience order in
     section_experience_short.tex. Everything else (skills, projects,
     certifications, education, languages, the .cls, fonts, photo) is the user's
     real file, untouched.

This guarantees: valid LaTeX, no fabricated content, projects + certifications
always included (they're the user's own files), and faithful tailoring.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import anthropic

MODEL = "claude-opus-4-8"
MAX_TOKENS = 2000

# NOTE: Experiences are kept in the CV's original REVERSE-CHRONOLOGICAL order
# (most recent first) — the market/ATS expectation. We do NOT reorder by
# relevance (that reads as a gap or error). Tailoring is via the headline,
# tagline, and skills emphasis only.

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {
            "type": "string",
            "description": "Titre/tagline court sous le nom, en français, adapté "
            "au poste (ex. 'Ingénieur Intégration & Validation Système'). "
            "Basé uniquement sur l'expérience réelle.",
        },
        "headline_paragraph": {
            "type": "string",
            "description": "Le paragraphe d'accroche du CV, en français, "
            "ré-accentué pour CETTE offre. Mêmes faits que le profil réel, aucune "
            "invention. 2-4 phrases.",
        },
        "skills_emphasis": {
            "type": "string",
            "description": "1 phrase pour le relecteur : quelles compétences/"
            "expériences réelles mettre en avant pour ce poste (sans réordonner "
            "les expériences, qui restent anté-chronologiques).",
        },
        "reviewer_note": {
            "type": "string",
            "description": "1-2 phrases pour le relecteur humain : ce qui a été "
            "mis en avant pour ce poste et pourquoi. Texte simple.",
        },
    },
    "required": ["tagline", "headline_paragraph", "skills_emphasis", "reviewer_note"],
    "additionalProperties": False,
}


def build_system_prompt(profile: dict[str, Any]) -> str:
    return f"""Tu adaptes un CV réel à une offre précise, en FRANÇAIS. Tu ne \
produis PAS de LaTeX : tu produis seulement un petit plan structuré \
(tagline, paragraphe d'accroche, emphase compétences, note pour le relecteur).

# Profil réel du candidat (seule source autorisée)
{json.dumps(profile, ensure_ascii=False, indent=2)}

# RÈGLES
1. N'INVENTE RIEN. Le paragraphe d'accroche et le tagline doivent refléter \
uniquement l'expérience réelle du profil ci-dessus. Pas de nouvelle compétence, \
pas de chiffre inventé.
2. Respecte la distinction proven/developing : l'IA et Flutter sont en \
apprentissage, pas une expertise. Ne les présente pas comme une expertise.
3. NE RÉORDONNE PAS les expériences. Elles restent en ordre anté-chronologique \
(la plus récente d'abord) — c'est l'attendu du marché et des ATS. L'adaptation \
se fait uniquement via l'accroche, le tagline et l'emphase des compétences.
4. Le tagline et l'accroche s'adaptent au poste, mais restent crédibles et sobres."""


def plan_for_job(
    client: anthropic.Anthropic,
    system_prompt: str,
    job: dict[str, Any],
    company: dict[str, Any] | None,
) -> dict[str, Any]:
    company_block = "(employeur non enrichi)"
    if company:
        company_block = (f"{company.get('name')} — {company.get('size')}; "
                         f"{company.get('tech_stack')}")
    jd = (job.get("jd_text") or "").strip()
    if len(jd) > 6000:
        jd = jd[:6000] + " […tronqué]"
    user = f"""Adapte le CV à cette offre.

INTITULÉ : {job.get('title')}
EMPLOYEUR : {company_block}
LIEU : {job.get('location')}
DESCRIPTION :
{jd}

Produis le plan structuré (tagline, accroche, emphase compétences, note relecteur).
Rappel : NE réordonne PAS les expériences."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# --- LaTeX rendering (no LLM; pure string ops on the user's real files) -------

def _tex_escape(text: str) -> str:
    """Escape LaTeX specials in plain prose (headline/tagline come from the LLM)."""
    repl = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(repl.get(c, c) for c in text)


def render_cv_folder(
    cv_src: Path,
    out_dir: Path,
    plan: dict[str, Any],
    job: dict[str, Any],
) -> None:
    """Copy the real cv/ template to out_dir and apply the tailoring plan.

    Only section_headline.tex and the tagline in cv.tex are changed. Everything
    else — including the experience section (kept reverse-chronological), skills,
    projects, certifications — is the user's verbatim cv/ file.
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(cv_src, out_dir, ignore=shutil.ignore_patterns(
        "*.aux", "*.log", "*.out", "*.pdf"))

    # 1) Headline paragraph
    headline_path = out_dir / "section_headline.tex"
    headline_path.write_text(
        "% Tailored headline (re-emphasized from real profile — no invented content)\n"
        "\\par{\n" + _tex_escape(plan["headline_paragraph"]) + "\n}\n",
        encoding="utf-8",
    )

    # 2) Tagline in cv.tex
    cv_tex_path = out_dir / "cv.tex"
    cv_tex = cv_tex_path.read_text(encoding="utf-8")
    cv_tex = re.sub(r"\\tagline\{[^}]*\}",
                    r"\\tagline{" + _tex_escape(plan["tagline"]) + "}", cv_tex, count=1)
    cv_tex_path.write_text(cv_tex, encoding="utf-8")

    # 3) Experience section is left VERBATIM (reverse-chronological, as in cv/).
    #    No reordering — that's the market/ATS expectation.

    # 4) Drop a reviewer note (not compiled — for the human)
    (out_dir / "_TAILORING_NOTE.txt").write_text(
        f"Role: {job.get('title')} @ {job.get('company_name')} "
        f"(score {job.get('fit_score')})\n\n"
        f"Tagline: {plan['tagline']}\n\n"
        f"Skills to emphasize: {plan.get('skills_emphasis', '')}\n\n"
        f"Reviewer note: {plan['reviewer_note']}\n\n"
        "This CV uses your real cv/ template. Only the headline and tagline were "
        "tailored to the role. Experiences stay reverse-chronological (most "
        "recent first); skills, projects, certifications, education, and "
        "languages are your verbatim cv/ files. Compile cv.tex with LuaLaTeX.\n",
        encoding="utf-8",
    )
