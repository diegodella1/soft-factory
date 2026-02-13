"""Development Agent — builds projects autonomously from approved PRDs.

This agent:
- Reads the PRD and creates a step-by-step build plan
- Generates code files iteratively
- Executes shell commands (npm install, pip install, etc.)
- Tests and fixes issues (up to 3 retries per step)
- Reports progress via Telegram at meaningful milestones
- Asks the user only for key decisions
- Guards against scope creep
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from bot.llm.client import chat
from bot.memory import store
from bot.config import MAX_DEV_RETRIES

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Development Agent for FactoryBot. You build software projects autonomously \
based on approved PRDs. You communicate in Argentine Spanish (vos, tuteo rioplatense).

You run on a Raspberry Pi 5 (8GB RAM, ARM64, Debian 12). Key constraints:
- All dependencies must be ARM-compatible
- Prefer lightweight solutions
- SQLite over PostgreSQL when possible
- Node 18 is on the system but for building Next.js use Docker
- Python 3.11.2 is available
- Docker and Docker Compose are available

Your approach:
1. Read the PRD carefully
2. Create a build plan (ordered list of concrete steps)
3. Execute each step: create files, run commands, validate
4. If something fails, debug and retry (up to 3 times)
5. Report progress at milestones

IMPORTANT:
- Use sensible defaults: placeholder text, default styles, etc.
- Follow the PRD's technical choices exactly
- Don't over-engineer — ship V1 fast
- Include basic security: input validation, CORS, rate limiting
- Generate a Dockerfile for the project
- Generate a docker-compose.yml if needed
"""

PLAN_PROMPT = """\
Read this PRD and create a detailed build plan. Each step should be a concrete action.

Return JSON with this structure:
{{
  "project_name": "...",
  "steps": [
    {{
      "id": 1,
      "description": "Short description of what this step does",
      "type": "create_file" | "run_command" | "create_directory" | "milestone",
      "path": "relative/path/to/file (for create_file/create_directory)",
      "command": "shell command (for run_command)",
      "milestone_message": "Progress message (for milestone type)"
    }}
  ]
}}

Rules:
- Start with directory structure and package.json/requirements.txt
- Then core configuration files
- Then implement features in priority order (Must-Have first)
- Include a Dockerfile
- End with a milestone: "Build complete"
- Use relative paths from the project src/ directory
- Keep commands simple and ARM-compatible
- For Node.js projects, use npm (not yarn/pnpm)

PRD:
{prd}
"""

FILE_PROMPT = """\
Generate the complete content for this file based on the PRD and build context.

PROJECT PRD:
{prd}

FILE TO CREATE: {filepath}
STEP DESCRIPTION: {description}

FILES CREATED SO FAR:
{existing_files}

Return ONLY the file content, no markdown fences, no explanation. Just the raw file content.
"""

FIX_PROMPT = """\
A command failed during the build. Analyze the error and suggest a fix.

COMMAND: {command}
ERROR OUTPUT:
{error}

PROJECT CONTEXT (PRD excerpt):
{prd_excerpt}

FILES IN PROJECT:
{existing_files}

Respond in JSON:
{{
  "diagnosis": "What went wrong",
  "fix_type": "modify_file" | "run_command" | "skip",
  "file_path": "path to modify (if fix_type is modify_file)",
  "file_content": "new file content (if fix_type is modify_file)",
  "command": "command to run (if fix_type is run_command)",
  "skip_reason": "why it's safe to skip (if fix_type is skip)"
}}
"""

QUESTION_PROMPT = """\
You're building a project and need the user's input on a key decision. \
Based on the context, formulate a concise question in Argentine Spanish. \
Include a recommended default.

CONTEXT: {context}
DECISION NEEDED: {decision}
"""


async def start_development(slug: str, send_fn) -> None:
    """Begin development from an approved PRD."""
    store.transition(slug, "development")
    prd = store.load_document(slug, "PRD.md")
    if not prd:
        await send_fn("Error: no encontré el PRD.md. Algo salió mal.")
        return

    await send_fn("Arranco el desarrollo. Primero creo el plan de build...")

    # Generate build plan
    plan_prompt = PLAN_PROMPT.format(prd=prd)
    raw_plan = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": plan_prompt}],
        heavy=True,
        temperature=0.3,
        max_tokens=4096,
        json_mode=True,
    )

    try:
        plan = json.loads(raw_plan)
    except json.JSONDecodeError:
        log.error("Failed to parse build plan: %s", raw_plan[:200])
        await send_fn("Error generando el plan de build. Reintentando...")
        # Retry once
        raw_plan = await chat(
            SYSTEM_PROMPT,
            [{"role": "user", "content": plan_prompt}],
            heavy=True,
            temperature=0.2,
            max_tokens=4096,
            json_mode=True,
        )
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError:
            await send_fn("No pude generar el plan de build. Revisá el PRD y probá de nuevo.")
            store.transition(slug, "blocked")
            return

    steps = plan.get("steps", [])
    total = len(steps)
    await send_fn(f"Plan de build listo: {total} pasos. Arranco...")

    # Save plan
    store.save_document(slug, "BUILD_PLAN.json", json.dumps(plan, indent=2))

    # Execute build plan
    src = store.src_dir(slug)
    src.mkdir(parents=True, exist_ok=True)

    completed = 0
    for step in steps:
        step_type = step.get("type", "")
        desc = step.get("description", "")
        step_id = step.get("id", completed + 1)

        log.info("Step %d/%d: %s (%s)", step_id, total, desc, step_type)

        try:
            if step_type == "create_directory":
                rel_path = step.get("path", "")
                (src / rel_path).mkdir(parents=True, exist_ok=True)

            elif step_type == "create_file":
                rel_path = step.get("path", "")
                await _create_file(slug, src, rel_path, desc, prd)

            elif step_type == "run_command":
                cmd = step.get("command", "")
                success = await _run_command(slug, src, cmd, prd, send_fn)
                if not success:
                    log.warning("Step %d failed after retries, continuing...", step_id)

            elif step_type == "milestone":
                msg = step.get("milestone_message", desc)
                await send_fn(f"[{step_id}/{total}] {msg}")

            completed += 1

        except Exception as e:
            log.exception("Step %d failed: %s", step_id, e)
            await send_fn(f"Error en paso {step_id}: {desc}\n{str(e)[:200]}")
            # Continue with next step

    # Build complete
    store.transition(slug, "deployment")
    await send_fn(
        f"Build completo ({completed}/{total} pasos).\n"
        "El proyecto está listo para deploy. Usá /approve para deployar."
    )

    # Log progress
    store.append_message(slug, "assistant", f"Build complete: {completed}/{total} steps")


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle messages during development phase."""
    state = store.load_state(slug)
    if not state:
        return

    # Check for scope creep
    is_scope_creep = await _check_scope_creep(user_message, slug)
    if is_scope_creep:
        store.add_deferred_feature(slug, user_message)
        await send_fn(
            "Eso suena a una feature de Phase 2. Lo anoto para después del deploy. "
            "¿Seguimos con V1?"
        )
        return

    # Otherwise, treat as answering a question or providing guidance
    context = store.get_context_messages(slug, limit=10)
    response = await chat(
        SYSTEM_PROMPT + "\nThe user is providing input during the build. Respond helpfully and concisely.",
        context + [{"role": "user", "content": user_message}],
        heavy=False,
        temperature=0.5,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)


async def _create_file(slug: str, src: Path, rel_path: str, description: str, prd: str) -> None:
    """Generate and write a file using the LLM."""
    # List existing files for context
    existing = _list_files(src)

    prompt = FILE_PROMPT.format(
        prd=prd[:3000],  # Truncate PRD to save tokens
        filepath=rel_path,
        description=description,
        existing_files="\n".join(existing[:30]),
    )

    content = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=4096,
    )

    # Clean up markdown fences if the LLM added them
    content = _strip_markdown_fences(content)

    filepath = src / rel_path
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    log.info("Created file: %s", filepath)


async def _run_command(slug: str, src: Path, cmd: str, prd: str, send_fn) -> bool:
    """Run a shell command with retries and error fixing."""
    for attempt in range(MAX_DEV_RETRIES):
        log.info("Running command (attempt %d): %s", attempt + 1, cmd[:100])

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                shell=True,
                cwd=str(src),
                capture_output=True,
                text=True,
                timeout=300,  # 5 min timeout
            )

            if result.returncode == 0:
                log.info("Command succeeded: %s", cmd[:80])
                return True

            error_output = (result.stderr or result.stdout or "Unknown error")[:1000]
            log.warning("Command failed: %s\n%s", cmd[:80], error_output[:200])

            if attempt < MAX_DEV_RETRIES - 1:
                # Try to fix
                fix = await _get_fix(cmd, error_output, prd, src)
                if fix:
                    fix_type = fix.get("fix_type", "skip")
                    if fix_type == "modify_file":
                        fpath = src / fix.get("file_path", "")
                        fpath.parent.mkdir(parents=True, exist_ok=True)
                        fpath.write_text(fix.get("file_content", ""))
                        log.info("Applied fix: modified %s", fpath)
                    elif fix_type == "run_command":
                        cmd = fix.get("command", cmd)
                        log.info("Applied fix: new command: %s", cmd[:80])
                    elif fix_type == "skip":
                        log.info("Skipping step: %s", fix.get("skip_reason", ""))
                        return True

        except subprocess.TimeoutExpired:
            log.warning("Command timed out: %s", cmd[:80])
            if attempt == MAX_DEV_RETRIES - 1:
                await send_fn(f"Comando timeout después de 5 min: `{cmd[:80]}`")
                return False

        except Exception as e:
            log.exception("Command execution error: %s", e)
            return False

    await send_fn(f"No pude ejecutar: `{cmd[:80]}` después de {MAX_DEV_RETRIES} intentos.")
    return False


async def _get_fix(cmd: str, error: str, prd: str, src: Path) -> dict | None:
    """Ask LLM to diagnose and fix a build error."""
    existing = _list_files(src)
    prompt = FIX_PROMPT.format(
        command=cmd,
        error=error[:800],
        prd_excerpt=prd[:1500],
        existing_files="\n".join(existing[:20]),
    )

    raw = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=2000,
        json_mode=True,
    )

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _check_scope_creep(message: str, slug: str) -> bool:
    """Check if a user message constitutes scope creep."""
    prd = store.load_document(slug, "PRD.md") or ""

    prompt = (
        "Is this user message requesting a new feature that's NOT in the PRD? "
        "If yes, it's scope creep. Respond in JSON: {\"is_scope_creep\": true/false}\n\n"
        f"PRD excerpt (V1 features section):\n{prd[:2000]}\n\n"
        f"User message: {message}"
    )

    raw = await chat(
        "You classify whether a message is scope creep relative to a PRD.",
        [{"role": "user", "content": prompt}],
        heavy=False,
        temperature=0.1,
        max_tokens=50,
        json_mode=True,
    )

    try:
        return json.loads(raw).get("is_scope_creep", False)
    except (json.JSONDecodeError, AttributeError):
        return False


def _list_files(directory: Path) -> list[str]:
    """List all files in a directory recursively (relative paths)."""
    files = []
    if not directory.exists():
        return files
    for f in sorted(directory.rglob("*")):
        if f.is_file() and "node_modules" not in str(f) and ".git" not in str(f):
            files.append(str(f.relative_to(directory)))
    return files


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
