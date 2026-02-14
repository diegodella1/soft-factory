"""Revision Agent — modifies existing deployed projects based on user requests.

This agent:
- Reads the existing source code of a deployed project
- Takes the user's change request
- Uses LLM to modify/add/delete files as needed
- Redeploys the updated project
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from bot.llm.client import chat
from bot.memory import store

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Revision Agent for FactoryBot. You modify existing deployed projects \
based on user requests. You communicate in Argentine Spanish (vos, tuteo rioplatense).

You run on a Raspberry Pi 5 (8GB RAM, ARM64, Debian 12).

Your approach:
1. Read the existing code carefully
2. Understand what the user wants to change
3. Make MINIMAL, targeted changes — don't rewrite things that work
4. Preserve existing functionality unless the user explicitly wants it removed

CRITICAL Node.js RULES:
- ALWAYS keep "type": "module" in package.json when using import/export
- EVERY imported package MUST be in package.json dependencies
- Use Express 4 (^4.21.0), NOT Express 5
- Use Node 20 in Dockerfiles: FROM node:20-alpine

CRITICAL general rules:
- Only modify files that need to change
- Don't break what already works
- If adding new dependencies, add them to package.json
"""

REVISION_PROMPT = """\
The user wants to modify an existing deployed project. Analyze the request and \
create a list of file changes needed.

USER REQUEST:
{user_request}

ORIGINAL DESIGN SPEC (UX/UI):
{ux_spec}

ORIGINAL MARKETING BRIEF (copy, microcopy, CTAs):
{marketing_brief}

EXISTING PROJECT FILES AND CONTENT:
{file_contents}

PROJECT INFO:
- Name: {project_name}
- URL: {project_url}
- Port: {port}

IMPORTANT: Use the design spec and marketing brief above as reference for any visual \
or copy changes. When the user references colors, styles, typography, or branding, \
cross-reference with the original specs to maintain design consistency.

Return JSON with this structure:
{{
  "summary": "Brief description of changes in Argentine Spanish",
  "changes": [
    {{
      "action": "modify" | "create" | "delete",
      "path": "relative/path/to/file",
      "content": "full new file content (for modify/create)",
      "reason": "why this change is needed"
    }}
  ],
}}

Rules:
- Only include files that actually need to change
- For "modify": include the COMPLETE new file content, not a diff
"""


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle a revision request for a deployed project."""
    state = store.load_state(slug)
    if not state:
        return

    src = store.src_dir(slug)
    if not src.exists():
        await send_fn("No encontré el código fuente del proyecto.")
        store.transition(slug, "blocked")
        return

    await send_fn("Analizando tu pedido de cambio...")

    # Read existing files
    file_contents = _read_project_files(src)

    # Load original design specs for context
    ux_spec = store.load_document(slug, "UX_SPEC.md") or "(no UX spec available)"
    marketing_brief = store.load_document(slug, "MARKETING_BRIEF.md") or "(no marketing brief available)"

    prompt = REVISION_PROMPT.format(
        user_request=user_message,
        file_contents=file_contents,
        ux_spec=ux_spec[:3000],
        marketing_brief=marketing_brief[:2000],
        project_name=state.get("name", ""),
        project_url=state.get("url", "N/A"),
        port=state.get("port", "N/A"),
    )

    # Inject skills
    from bot.skills import get_agent_skills
    system = SYSTEM_PROMPT
    skills_ctx = get_agent_skills("revision")
    if skills_ctx:
        system += f"\n\n{skills_ctx}"

    raw = await chat(
        system,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=8192,
        json_mode=True,
        project_slug=slug,
    )

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Failed to parse revision plan: %s", raw[:200])
        await send_fn("No pude generar el plan de cambios. Intentá ser más específico.")
        return

    changes = plan.get("changes", [])
    if not changes:
        await send_fn("No encontré cambios necesarios. ¿Podés ser más específico?")
        return

    summary = plan.get("summary", "Cambios aplicados")
    await send_fn(f"{summary}\n\nAplicando {len(changes)} cambio(s)...")

    # Apply changes
    for change in changes:
        action = change.get("action", "")
        rel_path = change.get("path", "")
        content = change.get("content", "")

        if not rel_path:
            continue

        filepath = src / rel_path

        if action == "delete":
            if filepath.exists():
                filepath.unlink()
                log.info("Deleted: %s", filepath)
        elif action in ("modify", "create"):
            content = _strip_fences(content)
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content)
            log.info("%s: %s", action.capitalize(), filepath)

    await send_fn("Cambios aplicados. Redesplegando...")

    # Redeploy — always rebuild (Dockerfile COPY requires it for any file change)
    success = await _redeploy(src, slug, True, send_fn)

    if success:
        url = state.get("url", "N/A")
        store.transition(slug, "deployed")
        await send_fn(
            f"Listo! Cambios deployados.\n"
            f"URL: {url}\n\n"
            "Si querés hacer más cambios, seguí mandándome lo que necesitás."
        )
    else:
        await send_fn(
            "El redeploy falló. Revisá los logs con "
            f"`docker logs factorybot-{slug}`\n"
            "Los cambios de código se aplicaron pero no se deployaron. "
            "Mandame otro mensaje para reintentar o usá /revisit para arrancar de nuevo."
        )
        store.transition(slug, "revision")


async def _redeploy(src: Path, slug: str, needs_rebuild: bool, send_fn) -> bool:
    """Redeploy the project container."""
    try:
        if needs_rebuild:
            await send_fn("Rebuildeando imagen Docker...")
            result = await asyncio.to_thread(
                subprocess.run,
                "docker compose build --no-cache",
                shell=True,
                cwd=str(src),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                error = (result.stderr or result.stdout)[:500]
                log.error("Rebuild failed: %s", error[:200])
                await send_fn(f"Build falló:\n```\n{error[:300]}\n```")
                return False

        # Restart container
        result = await asyncio.to_thread(
            subprocess.run,
            "docker compose up -d --force-recreate",
            shell=True,
            cwd=str(src),
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error = (result.stderr or result.stdout)[:500]
            log.error("Restart failed: %s", error[:200])
            return False

        # Wait and smoke test
        await asyncio.sleep(5)
        state = store.load_state(slug)
        url = state.get("url", "")
        if url:
            smoke = await asyncio.to_thread(
                subprocess.run,
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {url}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            status = smoke.stdout.strip().strip("'")
            if status.startswith("2") or status.startswith("3"):
                return True
            log.warning("Smoke test after redeploy: status %s", status)
            # Try once more
            await asyncio.sleep(5)
            smoke = await asyncio.to_thread(
                subprocess.run,
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {url}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            status = smoke.stdout.strip().strip("'")
            return status.startswith("2") or status.startswith("3")

        return True

    except subprocess.TimeoutExpired:
        log.error("Redeploy timeout")
        return False
    except Exception as e:
        log.exception("Redeploy error: %s", e)
        return False


def _read_project_files(src: Path) -> str:
    """Read all project files (excluding node_modules, etc.) for LLM context."""
    parts = []
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(src))
        # Skip binary/large/generated files
        if any(skip in rel for skip in ["node_modules", ".git", ".sqlite", "package-lock"]):
            continue
        if f.suffix in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf"):
            continue
        try:
            content = f.read_text()
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            parts.append(f"--- {rel} ---\n{content}")
        except (UnicodeDecodeError, OSError):
            continue

    return "\n\n".join(parts) if parts else "(no files found)"


def _strip_fences(text: str) -> str:
    """Remove markdown fences."""
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
