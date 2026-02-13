"""UX/UI Agent — defines navigation, page structure, components, and design tokens.

This agent runs after the Marketing Brief and before Development. It produces a
UX_SPEC.md that gives the Development Agent a complete blueprint for building
the frontend: what pages exist, how they connect, what components they use,
and what design tokens (colors, spacing, typography) to apply.

Outputs:
- Site map / navigation structure
- Page-by-page wireframe descriptions
- Component inventory
- Design tokens (colors, typography, spacing, borders, shadows)
- Responsive behavior notes
- Interaction patterns (forms, modals, toasts, loading states)
- Accessibility notes
"""

import logging
from bot.llm.client import chat
from bot.llm.web_research import auto_research, research
from bot.memory import store
from bot.config import MAX_CONVERSATION_CONTEXT

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the UX/UI Agent for FactoryBot. You design the complete user interface \
and user experience for software projects. You communicate in Argentine Spanish (vos, tuteo rioplatense).

Your personality: opinionated but flexible. You have strong defaults and propose solutions, \
not questions. You think mobile-first, accessibility-first.

Context: Projects deploy on a Raspberry Pi 5. Prefer:
- Tailwind CSS (utility-first, no build step needed with CDN)
- System fonts or Google Fonts (1-2 max)
- Minimal JavaScript — vanilla JS or Alpine.js for interactivity
- SVG icons (Lucide or Heroicons)
- No heavy frameworks unless the PRD specifies one

Your job:
1. Read the PRD and MARKETING_BRIEF
2. Ask the user 2-3 visual/UX preference questions
3. Generate UX_SPEC.md with everything the dev agent needs to build pixel-perfect pages

IMPORTANT:
- Define actual Tailwind classes and color hex codes, not abstract descriptions
- Think about every state: loading, empty, error, success
- Mobile-first — describe mobile layout then desktop variations
- Include actual spacing values (Tailwind scale: p-4, gap-6, etc.)
- Navigation must be clear and consistent
- Every interactive element needs a defined behavior
- Keep it V1 simple — no animations beyond basic transitions
"""

QUESTIONS_PROMPT = """\
Based on this project, ask the user 2-3 focused UX/UI questions. \
Propose a strong default for each so they can just approve.

Think about:
- Visual style: minimal/clean, bold/colorful, playful, corporate?
- Color preference: the PRD may have suggested colors — confirm or propose alternatives
- Layout style: sidebar nav, top nav, single page?
- Any reference sites or apps they like?

IDEA_SUMMARY:
{idea_summary}

PRD (UI/UX section):
{prd_ux}

MARKETING_BRIEF (brand voice):
{marketing_excerpt}

Ask in Argentine Spanish. Be direct, propose your recommendation.
"""

SPEC_PROMPT = """\
Generate a complete UX_SPEC.md for this project. This is the blueprint for the frontend.

Format:
```markdown
# UX/UI Spec: [Project Name]

## Design Tokens

### Colors
| Token | Hex | Tailwind Class | Usage |
|-------|-----|---------------|-------|
| primary | #... | bg-[hex]/text-[hex] | Main actions, links |
| primary-hover | #... | hover:bg-[hex] | Hover state |
| secondary | #... | ... | Secondary actions |
| background | #... | bg-[hex] | Page background |
| surface | #... | bg-[hex] | Cards, panels |
| text-primary | #... | text-[hex] | Body text |
| text-secondary | #... | text-[hex] | Muted text |
| border | #... | border-[hex] | Borders, dividers |
| success | #... | ... | Success states |
| error | #... | ... | Error states |
| warning | #... | ... | Warning states |

### Typography
- Font family: [font name] / `font-sans` or specific
- Headings: [sizes with Tailwind classes]
  - h1: `text-3xl font-bold` (mobile) / `text-5xl` (desktop)
  - h2: `text-2xl font-semibold` / `text-3xl`
  - h3: `text-xl font-medium` / `text-2xl`
- Body: `text-base` (16px)
- Small: `text-sm` (14px)
- Caption: `text-xs` (12px)

### Spacing Scale
- Section padding: `py-16 px-4` (mobile) / `py-24 px-8` (desktop)
- Card padding: `p-4` (mobile) / `p-6` (desktop)
- Element gap: `gap-4`
- Max content width: `max-w-6xl mx-auto`

### Borders & Shadows
- Card: `rounded-lg border border-[border-color] shadow-sm`
- Button: `rounded-md`
- Input: `rounded-md border border-[border-color]`
- Modal: `rounded-xl shadow-xl`

## Navigation
### Structure
- Type: [top-bar / sidebar / bottom-nav]
- Logo position: [left]
- Nav items: [list with routes]
- Mobile: [hamburger / bottom tabs / drawer]

### Sitemap
```
[page] → [page]
  └→ [subpage]
```

## Pages

### [Page Name] (`/route`)
**Purpose**: [what this page does]
**Layout**: [description]

#### Mobile
- [layout description with Tailwind context]

#### Desktop
- [layout description]

#### Components Used
- [Component 1]
- [Component 2]

#### States
- Loading: [description]
- Empty: [description with copy from marketing brief]
- Error: [description]
- Success: [description]

(repeat for each page)

## Component Inventory

### [Component Name]
- **Props/Variants**: [what changes]
- **Structure**: [HTML structure description]
- **Tailwind classes**: `[actual classes]`
- **States**: default, hover, active, disabled, loading
- **Accessibility**: [ARIA labels, keyboard nav]

(repeat for each reusable component: Button, Card, Input, Modal, Toast, Nav, Footer, etc.)

## Interaction Patterns
### Forms
- Validation: [inline / on-submit]
- Error display: [below field / toast]
- Submit button: [loading state description]

### Feedback
- Success: [toast / redirect / inline message]
- Error: [toast / inline / modal]
- Loading: [skeleton / spinner / text]

### Modals
- Overlay: `bg-black/50`
- Close: X button + click outside + Escape key
- Animation: none (V1)

## Responsive Breakpoints
- Mobile: default (< 768px)
- Tablet: `md:` (768px+)
- Desktop: `lg:` (1024px+)

## Accessibility
- Focus indicators: `focus:ring-2 focus:ring-[primary]`
- Color contrast: [WCAG AA minimum]
- Keyboard navigation: all interactive elements
- Alt text: required for all images
- Labels: required for all form inputs
```

CONTEXT:
PRD:
{prd}

MARKETING_BRIEF:
{marketing_brief}

USER PREFERENCES (from conversation):
{conversation}

DESIGN INSPIRATION (from research, if available):
{research_data}

Be specific with Tailwind classes and hex codes. The dev agent will copy-paste from this spec.
"""


async def start_ux_design(slug: str, send_fn) -> None:
    """Begin UX/UI spec generation by asking preference questions."""
    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md") or ""
    prd = store.load_document(slug, "PRD.md") or ""
    marketing = store.load_document(slug, "MARKETING_BRIEF.md") or ""

    # Extract UX section from PRD if it exists
    prd_ux = ""
    if "## 5" in prd:
        start = prd.index("## 5")
        end = prd.index("## 6") if "## 6" in prd else start + 1000
        prd_ux = prd[start:end]

    prompt = QUESTIONS_PROMPT.format(
        idea_summary=idea_summary[:1000],
        prd_ux=prd_ux[:1000],
        marketing_excerpt=marketing[:500],
    )

    response = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=False,
        temperature=0.7,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle messages during UX/UI design phase."""
    state = store.load_state(slug)
    if not state:
        return

    context = store.get_context_messages(slug, limit=MAX_CONVERSATION_CONTEXT)
    current_state = state["state"]

    if current_state == "ux_design":
        # User answered questions — generate the spec
        await _generate_spec(slug, context, send_fn)
    elif current_state == "ux_review":
        # User wants changes
        existing = store.load_document(slug, "UX_SPEC.md") or ""
        await _revise_spec(slug, existing, user_message, send_fn)


async def _generate_spec(slug: str, context: list[dict], send_fn) -> None:
    """Generate the full UX_SPEC.md."""
    await send_fn("Diseñando la interfaz completa... Investigando inspiración si hace falta...")

    prd = store.load_document(slug, "PRD.md") or ""
    marketing = store.load_document(slug, "MARKETING_BRIEF.md") or ""
    idea_summary = store.load_document(slug, "IDEA_SUMMARY.md") or ""
    conversation = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in context[-10:]
    )

    # Research design inspiration if relevant
    research_data = await auto_research(
        conversation[-500:] if conversation else idea_summary[:300],
        f"UX/UI design for: {idea_summary[:200]}",
    ) or ""

    prompt = SPEC_PROMPT.format(
        research_data=research_data[:1500],
        prd=prd[:3000],
        marketing_brief=marketing[:2000],
        conversation=conversation,
    )

    spec = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.4,
        max_tokens=4096,
    )

    store.save_document(slug, "UX_SPEC.md", spec)
    store.transition(slug, "ux_review")
    store.append_message(slug, "assistant", "[UX_SPEC.md generado]")

    if len(spec) > 3500:
        chunks = [spec[i:i + 3500] for i in range(0, len(spec), 3500)]
        for chunk in chunks:
            await send_fn(chunk)
    else:
        await send_fn(spec)

    await send_fn(
        "Esa es la spec de UX/UI completa. Revisala y decime:\n"
        "- /approve si está bien y arrancamos a construir\n"
        "- O decime qué cambiarías"
    )


async def _revise_spec(slug: str, existing: str, feedback: str, send_fn) -> None:
    """Revise the UX spec based on user feedback."""
    await send_fn("Revisando el diseño...")

    prompt = (
        f"The user wants changes to the UX/UI spec. Apply feedback and regenerate.\n\n"
        f"CURRENT SPEC:\n{existing}\n\n"
        f"USER FEEDBACK:\n{feedback}\n\n"
        f"Generate the updated spec. Keep the same structure."
    )

    spec = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.4,
        max_tokens=4096,
    )

    store.save_document(slug, "UX_SPEC.md", spec)
    store.append_message(slug, "assistant", "[UX_SPEC.md actualizado]")

    if len(spec) > 3500:
        chunks = [spec[i:i + 3500] for i in range(0, len(spec), 3500)]
        for chunk in chunks:
            await send_fn(chunk)
    else:
        await send_fn(spec)

    await send_fn("Spec actualizada. ¿/approve o más cambios?")
