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

You run INSIDE a Python Docker container (no Node.js/npm/yarn available). \
The target platform is a Raspberry Pi 5 (8GB RAM, ARM64, Debian 12). Key constraints:
- All dependencies must be ARM-compatible
- Prefer lightweight solutions
- Database: Supabase (PostgreSQL) local — REST API: http://192.168.1.14:54321/rest/v1/, \
use @supabase/supabase-js or direct REST. Para proyectos sin DB, archivos JSON está bien.
- Placeholder images: use https://placehold.co/ (ej: https://placehold.co/600x400)
- You CANNOT run npm/node/pip commands — you only CREATE FILES. \
Dependencies are installed when the Deployment Agent builds the Docker image.
- Docker and Docker Compose are available (Docker CLI is installed)

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

CRITICAL Node.js RULES (MUST follow):
- ALWAYS add "type": "module" to package.json when using import/export syntax
- EVERY package imported in code MUST be listed in package.json dependencies
- Use Express 4 (^4.21.0), NOT Express 5 (it's alpha/unstable)
- Use Node 20 (not 18) in Dockerfiles: FROM node:20-alpine
- Do NOT create duplicate files (e.g., two server files like server.js and app.js)
- The server MUST serve static files correctly (use express.static with __dirname for ESM)
- For ESM with __dirname, use: import { fileURLToPath } from 'url'; import { dirname } from 'path';
- Add a .dockerignore that excludes node_modules and *.sqlite

CRITICAL general rules:
- Cross-check: every import/require in code MUST have a matching dependency in package.json
- Only create ONE entry point file (server.js OR app.js, not both)
- Test mental model: after build, will `node src/server.js` actually work?
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
- Include a Dockerfile (use node:20-alpine for Node.js) and .dockerignore
- Include a docker-compose.yml with env vars
- End with a milestone: "Build complete"
- Paths are relative to the project root. Do NOT prefix with "src/" — just use "server.js", "index.html", etc.
- CRITICAL: Do NOT include "run_command" steps for npm, yarn, pip, node, or any runtime commands. \
You run inside a Python container WITHOUT Node.js/npm. Dependencies install when Docker builds the project. \
Only use "create_file" and "create_directory" step types, plus "milestone" for progress messages.
- CRITICAL: package.json MUST include "type": "module" if using import/export syntax
- CRITICAL: ALL imported packages must be in package.json dependencies — check every import statement
- CRITICAL: Use Express 4 (^4.21.0), NOT Express 5
- CRITICAL: Only ONE server entry point (server.js). Do NOT create app.js AND server.js
- CRITICAL: Server must serve static HTML/CSS files using express.static()
- CRITICAL: Do NOT create the same file multiple times — generate COMPLETE file content in a single step

PRD:
{prd}

MARKETING BRIEF (use this copy in the frontend — do NOT use Lorem Ipsum):
{marketing_brief}

UX/UI SPEC (follow these design tokens, layouts, and components exactly):
{ux_spec}
"""

FILE_PROMPT = """\
Generate the complete content for this file based on the PRD, marketing copy, and UX/UI spec.

PROJECT PRD:
{prd}

MARKETING BRIEF (use actual copy from here):
{marketing_brief}

UX/UI SPEC (follow design tokens and component specs):
{ux_spec}

FILE TO CREATE: {filepath}
STEP DESCRIPTION: {description}

FILES CREATED SO FAR:
{existing_files}

RULES:
- Return ONLY the file content, no markdown fences, no explanation. Just the raw file content.
- If this is package.json: include "type": "module", and list EVERY dependency that any source file imports.
- If this is a .js file using import/export: the project's package.json MUST have "type": "module".
- If this is a server file: serve static files with express.static(), use ESM __dirname pattern.
- Use Express 4 (^4.21.0), NEVER Express 5.
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
    """Begin development from an approved PRD + Marketing Brief + UX Spec."""
    store.transition(slug, "development")
    prd = store.load_document(slug, "PRD.md")
    if not prd:
        await send_fn("Error: no encontré el PRD.md. Algo salió mal.")
        return

    marketing_brief = store.load_document(slug, "MARKETING_BRIEF.md") or ""
    ux_spec = store.load_document(slug, "UX_SPEC.md") or ""

    await send_fn("Arranco el desarrollo. Tengo el PRD, copy y diseño. Creo el plan de build...")

    # Inject skills and learnings
    from bot.skills import get_agent_skills
    from bot.agents.learning import learnings_context
    system = SYSTEM_PROMPT
    skills_ctx = get_agent_skills("development")
    if skills_ctx:
        system += f"\n\n{skills_ctx}"
    past_learnings = learnings_context()
    if past_learnings:
        system += f"\n\n{past_learnings}"

    # Generate build plan
    plan_prompt = PLAN_PROMPT.format(
        prd=prd,
        marketing_brief=marketing_brief[:2000],
        ux_spec=ux_spec[:2000],
    )
    raw_plan = await chat(
        system,
        [{"role": "user", "content": plan_prompt}],
        heavy=True,
        temperature=0.3,
        max_tokens=4096,
        json_mode=True,
        project_slug=slug,
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
            project_slug=slug,
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
                if rel_path.startswith("src/"):
                    rel_path = rel_path[4:]
                (src / rel_path).mkdir(parents=True, exist_ok=True)

            elif step_type == "create_file":
                rel_path = step.get("path", "")
                await _create_file(slug, src, rel_path, desc, prd, marketing_brief, ux_spec)

            elif step_type == "run_command":
                cmd = step.get("command", "")
                success = await _run_command(slug, src, cmd, prd, send_fn, project_slug=slug)
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

    # Post-build validation
    await send_fn("Validando el proyecto antes del deploy...")
    await _validate_project(slug, src, prd, marketing_brief, ux_spec, send_fn)

    # Build complete — hand off to QA
    from bot.agents.qa_agent import start_qa

    store.transition(slug, "qa_testing")
    await send_fn(f"Build completo ({completed}/{total} pasos). Paso a validación QA...")
    await start_qa(slug, send_fn)

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
        project_slug=slug,
    )

    store.append_message(slug, "assistant", response)
    await send_fn(response)


async def _create_file(
    slug: str, src: Path, rel_path: str, description: str,
    prd: str, marketing_brief: str = "", ux_spec: str = "",
) -> None:
    """Generate and write a file using the LLM."""
    existing = _list_files(src)

    prompt = FILE_PROMPT.format(
        prd=prd[:3000],
        marketing_brief=marketing_brief[:1500],
        ux_spec=ux_spec[:1500],
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
        project_slug=slug,
    )

    # Clean up markdown fences if the LLM added them
    content = _strip_markdown_fences(content)

    # Strip leading "src/" prefix — files are already relative to src/
    if rel_path.startswith("src/"):
        rel_path = rel_path[4:]

    filepath = src / rel_path
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    log.info("Created file: %s", filepath)


async def _run_command(slug: str, src: Path, cmd: str, prd: str, send_fn, *, project_slug: str = "") -> bool:
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
                fix = await _get_fix(cmd, error_output, prd, src, project_slug)
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


async def _get_fix(cmd: str, error: str, prd: str, src: Path, slug: str = "") -> dict | None:
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
        project_slug=slug or None,
    )

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _validate_project(
    slug: str, src: Path, prd: str, marketing_brief: str, ux_spec: str, send_fn
) -> None:
    """Post-build validation: check package.json has all deps, type:module, etc."""
    import re

    pkg_path = src / "package.json"
    if not pkg_path.exists():
        return  # Not a Node project

    try:
        pkg = json.loads(pkg_path.read_text())
    except json.JSONDecodeError:
        return

    deps = set(pkg.get("dependencies", {}).keys())
    deps.update(pkg.get("devDependencies", {}).keys())
    has_type_module = pkg.get("type") == "module"

    # Scan all JS files for imports
    missing_deps = set()
    uses_esm = False
    for js_file in src.rglob("*.js"):
        if "node_modules" in str(js_file):
            continue
        content = js_file.read_text()
        # Check for ESM imports
        if re.search(r"^\s*import\s+", content, re.MULTILINE):
            uses_esm = True
        # Extract imported package names (not relative paths)
        for match in re.finditer(r"""(?:import|from)\s+['"]([^./][^'"]*?)(?:/[^'"]*)?['"]""", content):
            pkg_name = match.group(1)
            # Skip Node built-ins
            if pkg_name in {"url", "path", "fs", "http", "https", "crypto", "stream", "util", "events", "os", "child_process", "net", "tls", "dns", "readline", "zlib", "buffer", "string_decoder", "querystring", "assert", "worker_threads", "cluster", "perf_hooks", "v8", "vm", "module", "node:url", "node:path", "node:fs", "node:http", "node:https", "node:crypto"}:
                continue
            if pkg_name not in deps:
                missing_deps.add(pkg_name)

    fixes_needed = []
    if uses_esm and not has_type_module:
        fixes_needed.append("add type:module")
        pkg["type"] = "module"
    if missing_deps:
        fixes_needed.append(f"add missing deps: {', '.join(missing_deps)}")
        if "dependencies" not in pkg:
            pkg["dependencies"] = {}
        for dep in missing_deps:
            pkg["dependencies"][dep] = "latest"

    if fixes_needed:
        pkg_path.write_text(json.dumps(pkg, indent=2) + "\n")
        await send_fn(f"Validación: corregí package.json ({'; '.join(fixes_needed)})")
        log.info("Post-build validation fixed package.json: %s", fixes_needed)

    # Check for duplicate server files
    server_files = [f for f in (src / "src").rglob("*.js") if f.stem in ("server", "app") and "node_modules" not in str(f)] if (src / "src").exists() else []
    if len(server_files) > 1:
        log.warning("Multiple server files found: %s", server_files)
        await send_fn(f"Advertencia: hay {len(server_files)} archivos de servidor. Puede causar confusión.")


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
        project_slug=slug,
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
