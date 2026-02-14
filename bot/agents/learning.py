"""Learning Agent — post-deploy retrospective and knowledge accumulation.

After each successful deployment, this module:
- Reads all project artifacts (PRD, marketing, UX, QA report, project log, build plan)
- Generates a structured retrospective via LLM
- Saves LEARNINGS.md in the project directory
- Appends structured data to global_learnings.json for future projects
"""

import json
import logging
from datetime import datetime, timezone

from bot.llm.client import chat
from bot.memory import store

log = logging.getLogger(__name__)

RETROSPECTIVE_PROMPT = """\
You are a senior software engineering retrospective analyst. Analyze this completed project \
and generate a structured retrospective. Communicate in Argentine Spanish (vos, tuteo rioplatense).

PROJECT DOCUMENTS:

IDEA SUMMARY:
{idea_summary}

PRD:
{prd}

MARKETING BRIEF:
{marketing_brief}

UX/UI SPEC:
{ux_spec}

QA REPORT:
{qa_report}

PROJECT LOG:
{project_log}

BUILD PLAN:
{build_plan}

CONVERSATION HIGHLIGHTS (errors, decisions, retries):
{conversation_highlights}

Generate TWO outputs separated by the marker "---JSON---":

1. First: A LEARNINGS.md in markdown with:
   - What went well per agent phase (ideation, PRD, marketing, UX, development, QA, deployment)
   - What could improve per phase
   - Patterns worth reusing in future projects
   - Mistakes to avoid
   - Key technical decisions that worked (or didn't)

2. After the ---JSON--- marker: A JSON object with:
{{
  "agent_insights": {{
    "ideation": "one-line insight",
    "prd": "one-line insight",
    "marketing": "one-line insight",
    "ux": "one-line insight",
    "development": "one-line insight",
    "qa": "one-line insight",
    "deployment": "one-line insight"
  }},
  "patterns": ["reusable pattern 1", "..."],
  "mistakes": ["mistake to avoid 1", "..."],
  "reusable": ["decision that should be default 1", "..."]
}}

Keep the markdown under 800 words. Be specific and actionable, not generic.
"""


async def run_post_deploy_learning(slug: str, send_fn) -> None:
    """Run post-deploy learning: generate retrospective and save learnings."""
    await send_fn("Generando retrospectiva del proyecto...")

    # Load all project artifacts
    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md") or "(no disponible)"
    prd = store.load_document(slug, "PRD.md") or "(no disponible)"
    marketing_brief = store.load_document(slug, "MARKETING_BRIEF.md") or "(no disponible)"
    ux_spec = store.load_document(slug, "UX_SPEC.md") or "(no disponible)"
    qa_report = store.load_document(slug, "QA_REPORT.md") or "(no disponible)"
    project_log = store.load_document(slug, "PROJECT_LOG.md") or "(no disponible)"
    build_plan = store.load_document(slug, "BUILD_PLAN.json") or "(no disponible)"

    # Extract conversation highlights (errors, retries, decisions)
    history = store.load_history(slug)
    highlights = _extract_highlights(history)

    prompt = RETROSPECTIVE_PROMPT.format(
        idea_summary=idea_summary[:1500],
        prd=prd[:2000],
        marketing_brief=marketing_brief[:1000],
        ux_spec=ux_spec[:1000],
        qa_report=qa_report[:1500],
        project_log=project_log[:1000],
        build_plan=build_plan[:1000],
        conversation_highlights=highlights[:2000],
    )

    raw = await chat(
        "You are a retrospective analyst for software projects.",
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.4,
        max_tokens=3000,
        project_slug=slug,
    )

    # Split markdown and JSON
    if "---JSON---" in raw:
        md_part, json_part = raw.split("---JSON---", 1)
    else:
        md_part = raw
        json_part = ""

    # Save LEARNINGS.md
    md_part = md_part.strip()
    store.save_document(slug, "LEARNINGS.md", md_part)

    # Parse and save structured learning
    structured = _parse_structured(json_part, slug)
    if structured:
        store.save_learning(structured)

    store.append_message(slug, "assistant", "[LEARNINGS.md generado — retrospectiva guardada]")
    await send_fn("Retrospectiva generada (LEARNINGS.md). Aprendizajes guardados para futuros proyectos.")


def _extract_highlights(history: list[dict]) -> str:
    """Extract error messages, retries, and key decisions from conversation history."""
    highlights = []
    keywords = ["error", "falló", "failed", "fix", "corregí", "reintent", "retry",
                 "decidí", "decisión", "elegí", "aprobado", "bloqueado", "blocked"]
    for msg in history:
        content = msg.get("content", "")
        lower = content.lower()
        if any(kw in lower for kw in keywords):
            highlights.append(f"{msg.get('role', '?')}: {content[:200]}")
    return "\n".join(highlights[-30:])


def _parse_structured(json_text: str, slug: str) -> dict | None:
    """Parse the JSON part of the retrospective."""
    json_text = json_text.strip()
    # Strip markdown fences if present
    if json_text.startswith("```"):
        lines = json_text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        json_text = "\n".join(lines)

    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        log.warning("Could not parse structured learning JSON for %s", slug)
        return None

    return {
        "project": slug,
        "date": datetime.now(timezone.utc).isoformat(),
        "agent_insights": data.get("agent_insights", {}),
        "patterns": data.get("patterns", []),
        "mistakes": data.get("mistakes", []),
        "reusable": data.get("reusable", []),
    }


def learnings_context(max_projects: int = 3) -> str:
    """Format recent learnings as context text for injecting into agent prompts.

    Returns empty string if no learnings exist.
    """
    learnings = store.load_learnings()
    if not learnings:
        return ""

    recent = learnings[-max_projects:]
    parts = [
        "APRENDIZAJES DE PROYECTOS ANTERIORES:\n"
        "Usá estos aprendizajes activamente: aplicá los patrones que funcionaron, "
        "evitá los errores documentados, y seguí los defaults recomendados salvo "
        "que el usuario pida algo distinto."
    ]
    for entry in recent:
        project = entry.get("project", "?")
        parts.append(f"\n--- Proyecto: {project} ---")
        for pattern in entry.get("patterns", []):
            parts.append(f"  - Patrón: {pattern}")
        for mistake in entry.get("mistakes", []):
            parts.append(f"  - Evitar: {mistake}")
        for reusable in entry.get("reusable", []):
            parts.append(f"  - Default recomendado: {reusable}")

    return "\n".join(parts)
