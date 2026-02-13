"""Ideation Agent — conversational brainstorming for new project ideas.

This agent acts as a sharp, honest creative partner. It:
- Restates the idea to confirm understanding
- Asks for a project email address
- Asks 2-3 targeted clarifying questions at a time
- Challenges weak spots
- Proposes features the user may not have considered
- Flags scope creep early
- Generates a structured IDEA_SUMMARY.md when the user is ready
"""

import json
import logging
from bot.llm.client import chat
from bot.memory import store
from bot.config import MAX_CONVERSATION_CONTEXT

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Ideation Agent for FactoryBot, a software development factory. \
You brainstorm project ideas with the user conversationally in Argentine Spanish (vos, tuteo rioplatense).

Your personality: sharp, honest, direct creative partner. Not corporate. Like chatting with a smart colleague.

Your workflow:
1. When a new idea comes in, acknowledge it and restate it to confirm understanding.
2. Ask for the project email address (needed for the project).
3. Ask 2-3 targeted clarifying questions — NOT a dump of 20 questions.
4. Challenge weak spots honestly: "¿Qué pasa si un invitado confirma dos veces?"
5. Propose features the user may not have considered: "¿Debería poder agregar un +1 con restricciones alimentarias?"
6. Flag scope creep immediately: "Eso suena a Phase 2. Mantengamos V1 enfocado."
7. Keep track of what's been decided vs. what's still open.

When the user says the idea is finalized (e.g., "dale, avancemos", "listo", "aprobado", "vamos con el PRD"), \
generate a structured summary.

IMPORTANT:
- Ask questions conversationally, 2-3 at a time max
- Be direct and concise
- Always advocate for a focused V1
- Never use emojis unless the user does first
- Keep messages under 500 words
"""

SUMMARY_PROMPT = """\
Based on the conversation history below, generate a structured IDEA_SUMMARY.md for this project.

Format:
```markdown
# [Project Name]

## Project Email
[email]

## One-Line Description
[Single sentence describing the project]

## Problem Statement
[What problem does this solve? Why does it matter?]

## Target Users
[Who will use this?]

## V1 Core Features
- [Feature 1]
- [Feature 2]
- ...

## Key Decisions Made
- [Decision 1: choice and why]
- ...

## Deferred to Phase 2+
- [Feature deferred and why]
- ...

## Open Questions
- [Any remaining questions]

## Technical Notes
- [Any technical preferences or constraints mentioned]
```

Be comprehensive but concise. Only include what was actually discussed.
"""

EMAIL_CHECK_PROMPT = """\
Analyze the conversation so far. Has the user provided a project email address?
If yes, extract it. Respond in JSON: {"has_email": true/false, "email": "..." or null}
"""


async def start_ideation(idea_text: str, send_fn) -> None:
    """Start a new project from a raw idea."""
    # Create project with a temporary name derived from the idea
    temp_name = idea_text[:50].strip().rstrip(".")
    project = store.create_project(temp_name)
    slug = project["slug"]

    store.append_message(slug, "user", idea_text)

    # Get the agent's first response
    messages = [{"role": "user", "content": idea_text}]
    response = await chat(
        SYSTEM_PROMPT,
        messages,
        heavy=False,
        temperature=0.7,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle a message during ideation phase."""
    context = store.get_context_messages(slug, limit=MAX_CONVERSATION_CONTEXT)

    # Check if user wants to finalize
    if _sounds_like_approval(user_message):
        await _finalize_idea(slug, context, send_fn)
        return

    # Check if we have an email yet
    email_status = await _check_email(context)
    state = store.load_state(slug)

    if email_status.get("has_email") and email_status.get("email"):
        if not state.get("email"):
            store.update_state(slug, email=email_status["email"])

    # Continue conversation
    response = await chat(
        SYSTEM_PROMPT,
        context,
        heavy=False,
        temperature=0.7,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)


async def _finalize_idea(slug: str, context: list[dict], send_fn) -> None:
    """Generate the IDEA_SUMMARY.md and save it."""
    await send_fn("Generando el resumen de la idea...")

    # Check email first
    email_status = await _check_email(context)
    state = store.load_state(slug)

    if not state.get("email") and not email_status.get("has_email"):
        await send_fn(
            "Antes de cerrar, necesito el email del proyecto. "
            "¿Cuál va a ser el email asociado a este proyecto?"
        )
        return

    if email_status.get("email"):
        store.update_state(slug, email=email_status["email"])

    # Generate summary
    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in context
    )
    summary_messages = [
        {"role": "user", "content": f"Conversation:\n\n{conversation_text}"}
    ]
    summary = await chat(
        SUMMARY_PROMPT,
        summary_messages,
        heavy=True,
        temperature=0.3,
        max_tokens=2000,
    )

    # Extract a better project name from the summary
    first_line = summary.split("\n")[0].strip("# ").strip()
    if first_line:
        store.update_state(slug, name=first_line)

    store.save_document(slug, "IDEA_SUMMARY.md", summary)
    store.append_message(slug, "assistant", f"[IDEA_SUMMARY.md generado]\n\n{summary}")

    await send_fn(f"Acá está el resumen de la idea:\n\n{summary}")
    await send_fn(
        "Si estás conforme, usá /approve para pasar a la generación del PRD. "
        "Si querés cambiar algo, decime."
    )


async def _check_email(context: list[dict]) -> dict:
    """Check if the conversation includes a project email."""
    conversation_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in context[-10:]
    )
    raw = await chat(
        EMAIL_CHECK_PROMPT,
        [{"role": "user", "content": conversation_text}],
        heavy=False,
        temperature=0.0,
        max_tokens=100,
        json_mode=True,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"has_email": False, "email": None}


def _sounds_like_approval(text: str) -> bool:
    """Quick local check if the message sounds like approval."""
    lower = text.lower().strip()
    approval_phrases = [
        "listo", "dale", "aprobado", "vamos", "avancemos",
        "vamos con el prd", "me gusta", "perfecto", "ok vamos",
        "genial", "todo bien", "apruebo", "sigamos",
        "looks good", "let's move", "approved", "go ahead",
    ]
    return any(phrase in lower for phrase in approval_phrases)
