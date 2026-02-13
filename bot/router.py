"""Orchestrator / message router for FactoryBot.

Routes incoming Telegram messages to the appropriate agent based on
project state and detected intent.

Flow: ideation → PRD → marketing → UX/UI → development → deployment
"""

import logging
from bot.memory import store
from bot.llm.client import classify_intent

log = logging.getLogger(__name__)

# Lazy imports to avoid circular deps — agents import at call time


async def route_message(user_message: str, send_fn) -> None:
    """Route a user message to the appropriate agent.

    Args:
        user_message: The raw text from the user.
        send_fn: async callable(text) to send a Telegram reply.
    """
    from bot.agents.ideation import handle as ideation_handle
    from bot.agents.prd_agent import handle as prd_handle
    from bot.agents.marketing import handle as marketing_handle
    from bot.agents.ux_ui import handle as ux_handle
    from bot.agents.development import handle as dev_handle
    from bot.agents.deployment import handle as deploy_handle

    active = store.get_active_project()
    intent = await classify_intent(user_message)
    intent_name = intent.get("intent", "general")
    log.info("Intent: %s (confidence: %s)", intent_name, intent.get("confidence"))

    # --- No active project ---
    if not active:
        if intent_name == "new_idea":
            await _start_new_project(user_message, send_fn)
            return
        if intent_name == "revisit":
            await _handle_revisit(user_message, intent, send_fn)
            return
        if intent_name == "status_check":
            await _show_all_status(send_fn)
            return
        await send_fn(
            "No tenés ningún proyecto activo. Mandame una idea para empezar "
            "o usá /new para crear un proyecto nuevo."
        )
        return

    slug = active["slug"]
    state = active["state"]

    # --- Handle intents that override current flow ---
    if intent_name == "approve":
        await _handle_approve(active, send_fn)
        return
    if intent_name == "pause":
        store.pause_project(slug)
        await send_fn(f"Proyecto *{active['name']}* pausado. Usá /resume para retomar.")
        return
    if intent_name == "scope_change" and state == "development":
        store.add_deferred_feature(slug, user_message)
        await send_fn(
            "Eso suena a una feature de Phase 2. Lo anoto para después. "
            "Sigamos con V1 primero. ¿Te parece?"
        )
        return

    # --- Route to agent based on state ---
    store.append_message(slug, "user", user_message)

    if state == "ideation":
        await ideation_handle(slug, user_message, send_fn)
    elif state in ("prd_generation", "prd_review"):
        await prd_handle(slug, user_message, send_fn)
    elif state in ("marketing", "marketing_review"):
        await marketing_handle(slug, user_message, send_fn)
    elif state in ("ux_design", "ux_review"):
        await ux_handle(slug, user_message, send_fn)
    elif state in ("approved", "development"):
        await dev_handle(slug, user_message, send_fn)
    elif state == "deployment":
        await deploy_handle(slug, user_message, send_fn)
    elif state == "deployed":
        await send_fn(
            f"El proyecto *{active['name']}* ya está deployado en {active.get('url', 'N/A')}.\n"
            "Si querés hacer cambios, usá /revisit."
        )
    else:
        await send_fn("Estado desconocido. Usá /status para ver qué está pasando.")


async def handle_command(command: str, args: str, send_fn) -> None:
    """Handle an explicit bot command."""
    if command == "new":
        await _start_new_project(args, send_fn)

    elif command == "projects":
        await _show_all_status(send_fn)

    elif command == "revisit":
        if not args:
            await send_fn("Decime qué proyecto querés retomar. Ej: /revisit wedding-rsvp")
            return
        await _handle_revisit(args, {}, send_fn)

    elif command == "status":
        active = store.get_active_project()
        if not active:
            await _show_all_status(send_fn)
        else:
            await _show_project_status(active, send_fn)

    elif command == "approve":
        active = store.get_active_project()
        if not active:
            await send_fn("No hay proyecto activo para aprobar.")
            return
        await _handle_approve(active, send_fn)

    elif command == "pause":
        active = store.get_active_project()
        if not active:
            await send_fn("No hay proyecto activo para pausar.")
            return
        store.pause_project(active["slug"])
        await send_fn(f"Proyecto *{active['name']}* pausado.")

    elif command == "resume":
        paused = [p for p in store.list_projects() if p["state"] == "paused"]
        if not paused:
            await send_fn("No hay proyectos pausados.")
            return
        active = store.get_active_project()
        if active:
            await send_fn(
                f"Ya tenés un proyecto activo: *{active['name']}*. "
                "Pausalo primero con /pause."
            )
            return
        proj = paused[-1]
        store.resume_project(proj["slug"])
        state = store.load_state(proj["slug"])
        await send_fn(
            f"Retomando *{proj['name']}* en fase: {state['state']}."
        )

    elif command == "start":
        await send_fn(
            "Soy FactoryBot, tu fábrica de software personal.\n\n"
            "Mandame una idea de proyecto y arrancamos a laburar.\n"
            "O usá /new para empezar formalmente.\n\n"
            "Comandos:\n"
            "/new - Nuevo proyecto\n"
            "/projects - Ver todos los proyectos\n"
            "/status - Estado del proyecto actual\n"
            "/approve - Aprobar fase actual\n"
            "/pause - Pausar proyecto\n"
            "/resume - Retomar proyecto pausado\n"
            "/revisit [nombre] - Retomar proyecto deployado"
        )

    else:
        await send_fn(f"Comando desconocido: /{command}")


async def _start_new_project(idea_text: str, send_fn):
    """Start a new project from an idea."""
    from bot.agents.ideation import start_ideation

    active = store.get_active_project()
    if active:
        await send_fn(
            f"Ya tenés un proyecto activo: *{active['name']}* ({active['state']}). "
            "Pausalo con /pause antes de empezar uno nuevo."
        )
        return

    if not idea_text.strip():
        await send_fn("Contame tu idea. ¿Qué querés construir?")
        return

    await start_ideation(idea_text, send_fn)


async def _handle_approve(project: dict, send_fn):
    """Handle approval of current phase.

    Flow: ideation → PRD → marketing → UX/UI → development → deployment
    """
    from bot.agents.prd_agent import start_prd_generation
    from bot.agents.marketing import start_marketing
    from bot.agents.ux_ui import start_ux_design
    from bot.agents.development import start_development
    from bot.agents.deployment import start_deployment

    slug = project["slug"]
    state = project["state"]

    if state == "ideation":
        store.transition(slug, "prd_generation")
        await send_fn("Idea aprobada. Arranco a generar el PRD...")
        await start_prd_generation(slug, send_fn)

    elif state == "prd_review":
        store.transition(slug, "marketing")
        await send_fn("PRD aprobado. Ahora paso al copy y marketing...")
        await start_marketing(slug, send_fn)

    elif state == "marketing_review":
        store.transition(slug, "ux_design")
        await send_fn("Copy aprobado. Ahora paso al diseño UX/UI...")
        await start_ux_design(slug, send_fn)

    elif state == "ux_review":
        store.transition(slug, "approved")
        await send_fn("Diseño aprobado. Arranco el desarrollo con todo el material listo...")
        await start_development(slug, send_fn)

    elif state == "development":
        await send_fn("Recibido. Sigo adelante con la sugerencia.")

    elif state == "deployment":
        await start_deployment(slug, send_fn)

    else:
        await send_fn(f"No hay nada para aprobar en el estado actual ({state}).")


async def _handle_revisit(text: str, intent: dict, send_fn):
    """Handle project revisitation."""
    project_name = intent.get("project_name") or text.strip()
    proj = store.find_project(project_name)

    if not proj:
        projects = store.list_projects()
        if not projects:
            await send_fn("No hay proyectos registrados.")
            return
        names = "\n".join(f"  - {p['name']} ({p['state']})" for p in projects)
        await send_fn(f"No encontré ese proyecto. Proyectos disponibles:\n{names}")
        return

    active = store.get_active_project()
    if active and active["slug"] != proj["slug"]:
        await send_fn(
            f"Primero pausá el proyecto activo (*{active['name']}*) con /pause."
        )
        return

    project_log = store.load_document(proj["slug"], "PROJECT_LOG.md")
    if project_log:
        summary = project_log[:500]
        await send_fn(
            f"Retomando *{proj['name']}*.\n\n"
            f"Estado: {proj['state']}\n"
            f"URL: {proj.get('url', 'N/A')}\n\n"
            f"Resumen:\n{summary}\n\n"
            "¿Qué querés hacer?"
        )
    else:
        await send_fn(
            f"Retomando *{proj['name']}* (estado: {proj['state']}). ¿Qué querés hacer?"
        )

    if proj["state"] == "paused":
        store.resume_project(proj["slug"])
    if proj["state"] == "deployed":
        store.transition(proj["slug"], "ideation")


async def _show_all_status(send_fn):
    """Show status of all projects."""
    projects = store.list_projects()
    if not projects:
        await send_fn("No hay proyectos todavía. Mandame una idea para empezar.")
        return

    lines = ["*Proyectos:*\n"]
    for p in projects:
        emoji = {
            "ideation": "💡", "prd_generation": "📝", "prd_review": "📋",
            "marketing": "📣", "marketing_review": "📣",
            "ux_design": "🎨", "ux_review": "🎨",
            "approved": "✅", "development": "🔨", "deployment": "🚀",
            "deployed": "🌐", "paused": "⏸", "blocked": "🚫",
        }.get(p["state"], "❓")
        url = f" — {p['url']}" if p.get("url") else ""
        lines.append(f"{emoji} *{p['name']}* — {p['state']}{url}")

    await send_fn("\n".join(lines))


async def _show_project_status(project: dict, send_fn):
    """Show detailed status of a specific project."""
    deferred = project.get("deferred_features", [])
    deferred_text = "\n".join(f"  - {f}" for f in deferred) if deferred else "Ninguna"

    await send_fn(
        f"*{project['name']}*\n"
        f"Estado: {project['state']}\n"
        f"Email: {project.get('email', 'N/A')}\n"
        f"URL: {project.get('url', 'N/A')}\n"
        f"Features diferidas:\n{deferred_text}"
    )
