"""PRD Agent — generates development-ready PRDs from approved idea summaries.

Takes the IDEA_SUMMARY.md and generates a comprehensive PRD that includes:
- Project overview and goals
- User personas and use cases
- Feature list with priority levels
- Technical architecture recommendations (asks user for choices)
- API design
- Deployment requirements
- V1 scope definition
- Placeholder strategy
"""

import logging
from bot.llm.client import chat
from bot.memory import store
from bot.config import MAX_CONVERSATION_CONTEXT

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the PRD Agent for FactoryBot. You generate detailed, development-ready PRDs \
from finalized idea summaries. You communicate in Argentine Spanish (vos, tuteo rioplatense).

Your personality: thorough but practical. You focus on what's needed to build V1, not a corporate 50-page doc.

IMPORTANT CONTEXT - This runs on a Raspberry Pi 5 (8GB RAM, ARM). Default tech choices:
- Frontend: HTML/CSS/JS with Tailwind (lightweight, no build step)
- Backend: Node.js with Express or Python with FastAPI
- Database: Supabase (PostgreSQL) self-hosted local — REST API en http://192.168.1.14:54321/rest/v1/, \
direct PostgreSQL en 192.168.1.14:5432. Usar @supabase/supabase-js para Node.js o REST directo. \
Para proyectos simples sin DB, archivos JSON está bien.
- Deployment: Docker directo (docker compose)
- Web server: Caddy (automatic HTTPS) — available via Coolify/Traefik
- Email: Resend (free tier)
- Placeholder images: https://placehold.co/ (ej: https://placehold.co/600x400, \
https://placehold.co/600x400/EEE/31343C?text=Hero+Image)
- Placeholder text: Contextually appropriate (NOT Lorem Ipsum)

When generating the PRD:
1. First, ask the user 3-5 key technical/design questions (one message, numbered).
   Propose a recommended default for each: "Recomiendo X por Y. ¿Te parece?"
2. Wait for answers before generating the full PRD.
3. Generate the PRD in markdown format.

PRD Structure:
```
# PRD: [Project Name]

## 1. Overview
[What, why, for whom]

## 2. User Personas
[Who uses this and how]

## 3. Features
### Must-Have (V1)
- [Feature]: [Description]
### Nice-to-Have (V1 if time)
- ...
### Future (Phase 2+)
- ...

## 4. Technical Architecture
### Stack
- Frontend: ...
- Backend: ...
- Database: ...
### Data Models
[Tables/collections with fields]
### API Endpoints
[Method, path, description]

## 5. UI/UX
### Pages/Screens
[List of pages with description]
### Color Palette
[Colors with hex codes]
### Design Direction
[Brief description]

## 6. Deployment
- Domain/URL
- Environment variables needed
- External services/APIs

## 7. V1 Scope
### What Ships
[Explicit list]
### What Doesn't
[Explicit list with reasons]

## 8. Placeholder Strategy
[What gets placeholder content and what type]

## 9. Security
[Auth, validation, rate limiting, CORS]

## 10. Success Criteria
[How to know V1 is done]
```

Rules:
- Be specific enough that a developer can build from this without extra context
- Include actual endpoint paths, data model fields, color hex codes
- Keep it practical — this is V1, not enterprise architecture
- Never exceed what the Pi can handle
- Under 2000 words total
"""

QUESTIONS_PROMPT = """\
Based on this IDEA_SUMMARY, generate 3-5 key technical and design questions to ask the user \
before writing the PRD. For each question, provide a recommended default.

Format your response as a numbered list in Argentine Spanish. Be conversational.
Example:
1. Frontend: ¿HTML/CSS vanilla con Tailwind o React? Recomiendo HTML+Tailwind porque es más liviano y no necesita build step.
2. ...

IDEA_SUMMARY:
{idea_summary}
"""

GENERATE_PRD_PROMPT = """\
Generate a complete, development-ready PRD based on the idea summary and the user's answers \
to the technical questions. Follow the PRD structure defined in your instructions.

IDEA_SUMMARY:
{idea_summary}

CONVERSATION (includes technical decisions):
{conversation}
"""


async def start_prd_generation(slug: str, send_fn) -> None:
    """Begin PRD generation by asking technical questions."""
    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md")
    if not idea_summary:
        await send_fn("Error: no encontré el IDEA_SUMMARY.md. ¿Algo salió mal en la fase de ideación?")
        return

    # Ask technical questions
    prompt = QUESTIONS_PROMPT.format(idea_summary=idea_summary)
    response = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.5,
        project_slug=slug,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)
    await send_fn("Respondeme estas preguntas y genero el PRD completo.")


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle messages during PRD generation/review phase."""
    state = store.load_state(slug)
    if not state:
        return

    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md") or ""
    context = store.get_context_messages(slug, limit=MAX_CONVERSATION_CONTEXT)

    current_state = state["state"]

    if current_state == "prd_generation":
        # User answered questions — generate the full PRD
        await _generate_prd(slug, idea_summary, context, send_fn)

    elif current_state == "prd_review":
        # User is reviewing/requesting changes
        existing_prd = store.load_document(slug, "PRD.md") or ""
        await _revise_prd(slug, existing_prd, user_message, context, send_fn)


async def _generate_prd(slug: str, idea_summary: str, context: list[dict], send_fn) -> None:
    """Generate the full PRD from idea summary and user answers."""
    await send_fn("Generando el PRD...")

    conversation = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in context
    )
    # Inject skills and learnings
    from bot.skills import get_agent_skills
    from bot.agents.learning import learnings_context
    system = SYSTEM_PROMPT
    skills_ctx = get_agent_skills("prd")
    if skills_ctx:
        system += f"\n\n{skills_ctx}"
    past_learnings = learnings_context()
    if past_learnings:
        system += f"\n\n{past_learnings}"

    prompt = GENERATE_PRD_PROMPT.format(
        idea_summary=idea_summary,
        conversation=conversation,
    )

    prd = await chat(
        system,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.4,
        max_tokens=4096,
        project_slug=slug,
    )

    store.save_document(slug, "PRD.md", prd)
    store.transition(slug, "prd_review")
    store.append_message(slug, "assistant", f"[PRD.md generado]\n\n{prd[:500]}...")

    # Send in chunks if needed
    if len(prd) > 3500:
        chunks = [prd[i:i+3500] for i in range(0, len(prd), 3500)]
        for i, chunk in enumerate(chunks):
            await send_fn(f"```\n{chunk}\n```" if i > 0 else chunk)
    else:
        await send_fn(prd)

    await send_fn(
        "Ese es el PRD. Revisalo y decime:\n"
        "- /approve si está bien y arrancamos a construir\n"
        "- O decime qué cambiarías"
    )


async def _revise_prd(slug: str, existing_prd: str, feedback: str, context: list[dict], send_fn) -> None:
    """Revise the PRD based on user feedback."""
    await send_fn("Revisando el PRD con tus cambios...")

    revision_prompt = (
        f"The user wants changes to the PRD. Apply their feedback and regenerate.\n\n"
        f"CURRENT PRD:\n{existing_prd}\n\n"
        f"USER FEEDBACK:\n{feedback}\n\n"
        f"Generate the updated PRD. Keep the same structure."
    )

    prd = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": revision_prompt}],
        heavy=True,
        temperature=0.4,
        max_tokens=4096,
        project_slug=slug,
    )

    store.save_document(slug, "PRD.md", prd)
    store.append_message(slug, "assistant", f"[PRD.md actualizado]\n\n{prd[:500]}...")

    if len(prd) > 3500:
        chunks = [prd[i:i+3500] for i in range(0, len(prd), 3500)]
        for i, chunk in enumerate(chunks):
            await send_fn(f"```\n{chunk}\n```" if i > 0 else chunk)
    else:
        await send_fn(prd)

    await send_fn("PRD actualizado. ¿/approve o más cambios?")
