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

# The 5 experience blocks in the real CV, keyed by a stable id. The generator
# matches these against section_experience_short.tex to reorder them.
EXPERIENCE_IDS = {
    "bertrandt": "Architecte Électrique-Électronique Mulet",   # RENAULT prototypage (Bertrandt)
    "akkodis_connectivity": "Architecte Électrique-Électronique Système",  # RENAULT connectivité
    "akkodis_vehicle": "Co-Architecte Électrique-Électronique Véhicule",   # RENAULT projets véhicule
    "serma": "Ingénieur test et validation",                   # RENAULT validation (Serma)
    "iliade": "Consultant Fonctionnel SAP",                    # ENI / SAP
}

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
        "experience_order": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(EXPERIENCE_IDS.keys()),
            },
            "description": "Les 5 ids d'expérience dans l'ordre de présentation "
            "souhaité (le plus pertinent en premier). Doit contenir les 5 ids, "
            "sans doublon.",
        },
        "reviewer_note": {
            "type": "string",
            "description": "1-2 phrases pour le relecteur humain : ce qui a été "
            "mis en avant pour ce poste et pourquoi. Texte simple.",
        },
    },
    "required": ["tagline", "headline_paragraph", "experience_order", "reviewer_note"],
    "additionalProperties": False,
}


def build_system_prompt(profile: dict[str, Any]) -> str:
    return f"""Tu adaptes un CV réel à une offre précise, en FRANÇAIS. Tu ne \
produis PAS de LaTeX : tu produis seulement un petit plan structuré \
(tagline, paragraphe d'accroche, ordre des expériences, note pour le relecteur).

# Profil réel du candidat (seule source autorisée)
{json.dumps(profile, ensure_ascii=False, indent=2)}

# Les 5 expériences réelles (ids stables — tu réordonnes, tu ne réécris pas) :
- bertrandt : Architecte E/E Mulet, RENAULT prototypage (Mars 2025–présent)
- akkodis_connectivity : Architecte E/E Système, RENAULT connectivité (2023–2025)
- akkodis_vehicle : Co-Architecte E/E Véhicule, RENAULT projets (2022–2023)
- serma : Ingénieur test et validation, RENAULT validation (2020–2022)
- iliade : Consultant Fonctionnel SAP, ENI (2019–2020)

# RÈGLES
1. N'INVENTE RIEN. Le paragraphe d'accroche et le tagline doivent refléter \
uniquement l'expérience réelle ci-dessus. Pas de nouvelle compétence, pas de \
chiffre inventé.
2. Respecte la distinction proven/developing : l'IA et Flutter sont en \
apprentissage, pas une expertise. Ne les présente pas comme une expertise.
3. experience_order doit contenir les 5 ids, l'ordre reflétant la pertinence \
pour CETTE offre (le plus pertinent d'abord). Garde un ordre globalement \
chronologique si la pertinence est équivalente.
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

Produis le plan structuré (tagline, accroche, ordre des 5 expériences, note relecteur)."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    plan = json.loads(text)
    # Defensive: ensure all 5 ids present exactly once (LLM could slip).
    order = [i for i in plan["experience_order"] if i in EXPERIENCE_IDS]
    for i in EXPERIENCE_IDS:
        if i not in order:
            order.append(i)
    plan["experience_order"] = order[:5]
    return plan


# --- LaTeX rendering (no LLM; pure string ops on the user's real files) -------

def _tex_escape(text: str) -> str:
    """Escape LaTeX specials in plain prose (headline/tagline come from the LLM)."""
    repl = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(repl.get(c, c) for c in text)


def _split_experience_blocks(experience_tex: str) -> tuple[str, dict[str, str], str]:
    """Split section_experience_short.tex into (header, {id: block}, footer).

    Blocks are separated by \\emptySeparator. We match each block to an id by
    looking for its title string (from EXPERIENCE_IDS).
    """
    begin = experience_tex.index(r"\begin{experiences}") + len(r"\begin{experiences}")
    end = experience_tex.index(r"\end{experiences}")
    header = experience_tex[:begin]
    footer = experience_tex[end:]
    body = experience_tex[begin:end]

    raw_blocks = [b for b in body.split(r"\emptySeparator")]
    id_to_block: dict[str, str] = {}
    for blk in raw_blocks:
        for eid, title in EXPERIENCE_IDS.items():
            if title in blk:
                id_to_block[eid] = blk.strip("\n")
                break
    return header, id_to_block, footer


def render_cv_folder(
    cv_src: Path,
    out_dir: Path,
    plan: dict[str, Any],
    job: dict[str, Any],
) -> None:
    """Copy the real cv/ template to out_dir and apply the tailoring plan.

    Only section_headline.tex, the tagline in cv.tex, and the experience order
    in section_experience_short.tex are changed. Everything else is verbatim.
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

    # 3) Reorder experiences
    exp_path = out_dir / "section_experience_short.tex"
    exp_tex = exp_path.read_text(encoding="utf-8")
    header, id_to_block, footer = _split_experience_blocks(exp_tex)
    ordered = [id_to_block[i] for i in plan["experience_order"] if i in id_to_block]
    # keep any block that somehow wasn't matched, appended in original order
    for eid, blk in id_to_block.items():
        if blk not in ordered:
            ordered.append(blk)
    new_body = ("\n  \\emptySeparator\n").join(ordered)
    exp_path.write_text(header + "\n  " + new_body + "\n" + footer, encoding="utf-8")

    # 4) Drop a reviewer note (not compiled — for the human)
    (out_dir / "_TAILORING_NOTE.txt").write_text(
        f"Role: {job.get('title')} @ {job.get('company_name')} "
        f"(score {job.get('fit_score')})\n\n"
        f"Tagline: {plan['tagline']}\n\n"
        f"Experience order: {' > '.join(plan['experience_order'])}\n\n"
        f"Reviewer note: {plan['reviewer_note']}\n\n"
        "This CV uses your real cv/ template. Only the headline, tagline, and "
        "experience ORDER were tailored. Skills, projects, certifications, "
        "education, and languages are your verbatim files. Compile cv.tex with "
        "LuaLaTeX (as the template requires).\n",
        encoding="utf-8",
    )
