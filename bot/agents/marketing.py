"""Marketing Agent — generates copy, tone of voice, CTAs, messaging, and SEO basics.

This agent runs after PRD approval and before UX/UI design. It produces a
MARKETING_BRIEF.md that the UX/UI and Development agents use to build
with real, contextual copy instead of Lorem Ipsum.

Outputs:
- Brand voice and tone guidelines
- Homepage hero copy (headline, subheadline, CTA)
- Key page copy (features, about, pricing if applicable)
- Call-to-action variants
- SEO meta titles and descriptions
- Email templates if applicable
- Error messages and empty states copy
- Microcopy (button labels, tooltips, confirmation messages)
"""

import logging
from bot.llm.client import chat
from bot.llm.web_research import auto_research, research
from bot.memory import store
from bot.config import MAX_CONVERSATION_CONTEXT

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Marketing Agent for FactoryBot. You create all the copy, messaging, \
and brand voice for software projects. You communicate in Argentine Spanish (vos, tuteo rioplatense).

Your personality: creative but practical. You write copy that converts, not copy that wins awards. \
You think about the end user, not the developer.

Your job:
1. Read the PRD and IDEA_SUMMARY
2. Ask the user 2-3 key questions about tone, audience, and positioning
3. Generate a complete MARKETING_BRIEF.md with all the copy the project needs

IMPORTANT:
- Write copy in the language the project targets (ask if unclear — usually Spanish or English)
- Be specific — write actual headlines, not "insert headline here"
- Think about conversion: every page needs a clear CTA
- Write for the target audience described in the PRD
- Include empty states and error messages — these matter for UX
- Keep it V1 focused — no enterprise marketing fluff
- Microcopy matters: button labels, tooltips, confirmation messages
"""

QUESTIONS_PROMPT = """\
Based on this project, I need to ask the user a few key marketing questions \
before generating the copy. Read the context and ask 2-3 focused questions.

Think about:
- What language should the copy be in?
- What tone? (formal, casual, playful, professional)
- Is there a specific audience or brand personality?
- Any existing tagline or messaging to match?

IDEA_SUMMARY:
{idea_summary}

PRD (excerpt):
{prd_excerpt}

Ask your questions in Argentine Spanish. Be conversational, not corporate.
"""

BRIEF_PROMPT = """\
Generate a complete MARKETING_BRIEF.md for this project. Include ALL the copy \
the development team needs to build the frontend with real content.

Format:
```markdown
# Marketing Brief: [Project Name]

## Brand Voice
- Tone: [casual/formal/playful/professional]
- Personality: [2-3 adjectives]
- Language: [target language for copy]
- Key principle: [one sentence guide for all copy]

## Homepage
### Hero Section
- Headline: [main headline]
- Subheadline: [supporting text, 1-2 sentences]
- Primary CTA: [button text]
- Secondary CTA: [link text, if applicable]

### Features Section
- Feature 1: [title] — [one-line description]
- Feature 2: [title] — [one-line description]
- ...

### Social Proof / Trust
- [testimonial placeholder or trust signal]

## Key Pages Copy
### [Page Name]
- Title: ...
- Description: ...
- CTA: ...
(repeat for each page in the PRD)

## Calls to Action
| Context | CTA Text | Variant |
|---------|----------|---------|
| [where it appears] | [text] | [alternative] |

## Microcopy
### Buttons & Actions
- Submit form: [text]
- Cancel: [text]
- Delete: [text]
- Confirm: [text]
- ...

### Empty States
- No [items] yet: [friendly message + CTA]
- ...

### Success Messages
- [action] success: [message]
- ...

### Error Messages
- Generic error: [message]
- Not found: [message]
- Validation error: [message]
- ...

## SEO
### Meta Tags
- Title: [50-60 chars]
- Description: [150-160 chars]
- OG Title: ...
- OG Description: ...

### Key Phrases
- [phrase 1]
- [phrase 2]
- ...

## Email Templates (if applicable)
### [Template Name]
- Subject: ...
- Preview text: ...
- Body: ...
```

CONTEXT:
IDEA_SUMMARY:
{idea_summary}

PRD:
{prd}

USER PREFERENCES (from conversation):
{conversation}

Write ALL copy ready to be used in code. No placeholders like "[insert here]" — write the actual text.

RESEARCH DATA (if available — use real info from here):
{research_data}
"""


async def start_marketing(slug: str, send_fn) -> None:
    """Begin marketing brief generation by asking key questions."""
    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md") or ""
    prd = store.load_document(slug, "PRD.md") or ""

    # Research the project topic for real-world context
    research_data = await research(
        f"marketing copy examples for {idea_summary[:100]}",
        f"Creating marketing copy for a software project: {idea_summary[:200]}",
    )

    prompt = QUESTIONS_PROMPT.format(
        idea_summary=idea_summary[:1500],
        prd_excerpt=prd[:2000],
    )

    response = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=False,
        temperature=0.7,
        project_slug=slug,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle messages during marketing phase."""
    state = store.load_state(slug)
    if not state:
        return

    context = store.get_context_messages(slug, limit=MAX_CONVERSATION_CONTEXT)
    current_state = state["state"]

    if current_state == "marketing":
        # User answered questions — generate the brief
        await _generate_brief(slug, context, send_fn)
    elif current_state == "marketing_review":
        # User wants changes
        existing = store.load_document(slug, "MARKETING_BRIEF.md") or ""
        await _revise_brief(slug, existing, user_message, send_fn)


async def _generate_brief(slug: str, context: list[dict], send_fn) -> None:
    """Generate the full MARKETING_BRIEF.md."""
    await send_fn("Generando el brief de marketing con todo el copy... Investigando si hace falta...")

    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md") or ""
    prd = store.load_document(slug, "PRD.md") or ""
    conversation = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in context[-10:]
    )

    # Research for real data
    research_data = await auto_research(
        conversation[-500:] if conversation else idea_summary[:300],
        idea_summary[:300],
    ) or ""

    prompt = BRIEF_PROMPT.format(
        idea_summary=idea_summary[:1500],
        prd=prd[:3000],
        conversation=conversation,
        research_data=research_data[:1500],
    )

    # Inject skills
    from bot.skills import get_agent_skills
    system = SYSTEM_PROMPT
    skills_ctx = get_agent_skills("marketing")
    if skills_ctx:
        system += f"\n\n{skills_ctx}"

    brief = await chat(
        system,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.5,
        max_tokens=4096,
        project_slug=slug,
    )

    store.save_document(slug, "MARKETING_BRIEF.md", brief)
    store.transition(slug, "marketing_review")
    store.append_message(slug, "assistant", f"[MARKETING_BRIEF.md generado]")

    # Send in chunks if needed
    if len(brief) > 3500:
        chunks = [brief[i:i + 3500] for i in range(0, len(brief), 3500)]
        for chunk in chunks:
            await send_fn(chunk)
    else:
        await send_fn(brief)

    await send_fn(
        "Ese es el copy completo. Revisalo y decime:\n"
        "- /approve si está bien y pasamos al diseño UX/UI\n"
        "- O decime qué cambiarías"
    )


async def _revise_brief(slug: str, existing: str, feedback: str, send_fn) -> None:
    """Revise the marketing brief based on user feedback."""
    await send_fn("Revisando el copy...")

    prompt = (
        f"The user wants changes to the marketing brief. Apply feedback and regenerate.\n\n"
        f"CURRENT BRIEF:\n{existing}\n\n"
        f"USER FEEDBACK:\n{feedback}\n\n"
        f"Generate the updated brief. Keep the same structure."
    )

    brief = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.5,
        max_tokens=4096,
        project_slug=slug,
    )

    store.save_document(slug, "MARKETING_BRIEF.md", brief)
    store.append_message(slug, "assistant", "[MARKETING_BRIEF.md actualizado]")

    if len(brief) > 3500:
        chunks = [brief[i:i + 3500] for i in range(0, len(brief), 3500)]
        for chunk in chunks:
            await send_fn(chunk)
    else:
        await send_fn(brief)

    await send_fn("Brief actualizado. ¿/approve o más cambios?")
