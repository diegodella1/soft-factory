"""QA Agent — validates generated code against PRD, marketing brief, and UX spec.

Runs after development, before deployment:
  development → qa_testing → deployment (if PASS)
                           → qa_review  (if FAIL → user decides /approve or fix)

Checks:
1. Static validation (no LLM): file existence, package.json, dependencies
2. PRD compliance (LLM): must-have features and endpoints present
3. Marketing copy compliance (LLM): real copy used, no placeholders
4. UX/UI compliance (LLM): design tokens, colors, typography respected
"""

import json
import logging
import re
from pathlib import Path

from bot.llm.client import chat
from bot.memory import store

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the QA Agent for FactoryBot. You validate that generated code matches the project \
specifications. You communicate in Argentine Spanish (vos, tuteo rioplatense).

Your job:
- Compare generated source code against the PRD, Marketing Brief, and UX Spec
- Identify missing features, placeholder text, wrong design tokens
- Be strict but fair — flag real issues, not nitpicks
- Output structured JSON when asked
"""

PRD_CHECK_PROMPT = """\
Compare this source code against the PRD. Check:
1. Are all Must-Have features from the PRD implemented?
2. Are all API endpoints/routes from the PRD present in the code?
3. Are there any critical features completely missing?

PRD:
{prd}

SOURCE FILES:
{source_files}

Respond in JSON:
{{
  "checks": [
    {{"feature": "feature name", "status": "PASS" | "FAIL", "detail": "explanation"}}
  ],
  "summary": "brief overall assessment"
}}
"""

COPY_CHECK_PROMPT = """\
Compare the source code against the Marketing Brief. Check:
1. Is the actual marketing copy used in the frontend (headings, CTAs, descriptions)?
2. Are there placeholder texts like "Lorem ipsum", "TODO", "placeholder", "sample text"?
3. Are the brand voice and messaging consistent with the brief?

MARKETING BRIEF:
{marketing_brief}

SOURCE FILES (HTML/JSX/frontend):
{source_files}

Respond in JSON:
{{
  "checks": [
    {{"element": "what was checked", "status": "PASS" | "WARN" | "FAIL", "detail": "explanation"}}
  ],
  "summary": "brief overall assessment"
}}
"""

UX_CHECK_PROMPT = """\
Compare the source code against the UX/UI Spec. Check:
1. Are the specified color tokens (primary, secondary, etc.) used in CSS/styles?
2. Is the correct font family applied?
3. Are the layout patterns (grid, flexbox) consistent with the spec?
4. Are the responsive breakpoints present?

UX/UI SPEC:
{ux_spec}

SOURCE FILES (CSS/HTML/JSX/style files):
{source_files}

Respond in JSON:
{{
  "checks": [
    {{"element": "what was checked", "status": "PASS" | "WARN" | "FAIL", "detail": "explanation"}}
  ],
  "summary": "brief overall assessment"
}}
"""

FIX_PROMPT = """\
The QA process found issues in this project. Fix them.

QA REPORT (issues to fix):
{issues}

PRD:
{prd}

MARKETING BRIEF:
{marketing_brief}

UX/UI SPEC:
{ux_spec}

CURRENT SOURCE FILES:
{source_files}

For each fixable issue, respond in JSON:
{{
  "fixes": [
    {{
      "file_path": "relative/path/to/file",
      "action": "replace" | "create",
      "content": "complete new file content"
    }}
  ],
  "unfixable": ["issues that can't be auto-fixed"]
}}
"""


# ---------------------------------------------------------------------------
# Static checks (no LLM)
# ---------------------------------------------------------------------------

def _static_checks(src: Path) -> list[dict]:
    """Run static validation checks on the source directory."""
    results = []

    # Check key files exist
    key_files = {
        "package.json": "Package manifest",
        "Dockerfile": "Docker build file",
    }
    # Find server entry point
    entry_candidates = ["server.js", "app.js", "index.js", "src/server.js", "src/index.js", "src/app.js", "main.py", "app.py"]
    has_entry = any((src / f).exists() for f in entry_candidates)

    for fname, label in key_files.items():
        exists = (src / fname).exists()
        results.append({
            "check": f"{label} ({fname})",
            "status": "PASS" if exists else "FAIL",
            "detail": "Presente" if exists else "Falta",
        })

    results.append({
        "check": "Entry point del servidor",
        "status": "PASS" if has_entry else "FAIL",
        "detail": "Presente" if has_entry else "No se encontró ningún entry point",
    })

    # Check package.json validity (if exists)
    pkg_path = src / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text())

            # Check type: module if ESM imports found
            uses_esm = False
            for js_file in src.rglob("*.js"):
                if "node_modules" in str(js_file):
                    continue
                content = js_file.read_text()
                if re.search(r"^\s*import\s+", content, re.MULTILINE):
                    uses_esm = True
                    break

            has_type_module = pkg.get("type") == "module"
            if uses_esm:
                results.append({
                    "check": "package.json type:module (ESM)",
                    "status": "PASS" if has_type_module else "FAIL",
                    "detail": "Presente" if has_type_module else "Usa imports ESM pero falta \"type\": \"module\"",
                })

            # Check all imported deps are in package.json
            deps = set(pkg.get("dependencies", {}).keys())
            deps.update(pkg.get("devDependencies", {}).keys())
            node_builtins = {
                "url", "path", "fs", "http", "https", "crypto", "stream", "util",
                "events", "os", "child_process", "net", "tls", "dns", "readline",
                "zlib", "buffer", "string_decoder", "querystring", "assert",
                "worker_threads", "cluster", "perf_hooks", "v8", "vm", "module",
            }
            missing = set()
            for js_file in src.rglob("*.js"):
                if "node_modules" in str(js_file):
                    continue
                content = js_file.read_text()
                for match in re.finditer(r"""(?:import|from)\s+['"]([^./][^'"]*?)(?:/[^'"]*)?['"]""", content):
                    pkg_name = match.group(1).replace("node:", "")
                    if pkg_name not in node_builtins and pkg_name not in deps:
                        missing.add(pkg_name)

            if missing:
                results.append({
                    "check": "Dependencias completas",
                    "status": "FAIL",
                    "detail": f"Faltan en package.json: {', '.join(sorted(missing))}",
                })
            else:
                results.append({
                    "check": "Dependencias completas",
                    "status": "PASS",
                    "detail": "Todas las dependencias importadas están declaradas",
                })

        except json.JSONDecodeError:
            results.append({
                "check": "package.json válido",
                "status": "FAIL",
                "detail": "JSON inválido",
            })

    # Check for empty files
    empty_files = []
    for f in src.rglob("*"):
        if f.is_file() and f.stat().st_size == 0 and "node_modules" not in str(f) and ".git" not in str(f):
            empty_files.append(str(f.relative_to(src)))
    if empty_files:
        results.append({
            "check": "Archivos vacíos",
            "status": "WARN",
            "detail": f"Archivos vacíos: {', '.join(empty_files[:5])}",
        })

    return results


# ---------------------------------------------------------------------------
# LLM compliance checks
# ---------------------------------------------------------------------------

def _read_source_files(src: Path, extensions: tuple | None = None, max_chars: int = 12000) -> str:
    """Read source files and return as a formatted string for LLM context."""
    if extensions is None:
        extensions = (".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".py", ".json")

    parts = []
    total = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file() or "node_modules" in str(f) or ".git" in str(f):
            continue
        if f.suffix not in extensions:
            continue
        try:
            content = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(f.relative_to(src))
        chunk = f"--- {rel} ---\n{content}\n"
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n[...truncated]")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts) if parts else "(no source files found)"


def _read_frontend_files(src: Path, max_chars: int = 10000) -> str:
    """Read only frontend-relevant files."""
    return _read_source_files(src, extensions=(".html", ".css", ".jsx", ".tsx", ".js"), max_chars=max_chars)


def _read_style_files(src: Path, max_chars: int = 10000) -> str:
    """Read style-related files."""
    return _read_source_files(src, extensions=(".css", ".html", ".jsx", ".tsx", ".js"), max_chars=max_chars)


def _qa_system_prompt() -> str:
    """Build QA system prompt with skills injected."""
    from bot.skills import get_agent_skills
    system = SYSTEM_PROMPT
    skills_ctx = get_agent_skills("qa")
    if skills_ctx:
        system += f"\n\n{skills_ctx}"
    return system


async def _prd_compliance(slug: str, src: Path) -> tuple[list[dict], str]:
    """Check code against PRD using LLM. Returns (checks, summary)."""
    prd = store.load_document(slug, "PRD.md")
    if not prd:
        return [], "No PRD found — skipping"

    source = _read_source_files(src)
    prompt = PRD_CHECK_PROMPT.format(prd=prd[:4000], source_files=source)

    raw = await chat(
        _qa_system_prompt(),
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=2000,
        json_mode=True,
        project_slug=slug,
    )

    try:
        data = json.loads(raw)
        return data.get("checks", []), data.get("summary", "")
    except json.JSONDecodeError:
        log.warning("Failed to parse PRD compliance response")
        return [], "Error parsing LLM response"


async def _copy_compliance(slug: str, src: Path) -> tuple[list[dict], str]:
    """Check code against marketing brief using LLM."""
    brief = store.load_document(slug, "MARKETING_BRIEF.md")
    if not brief:
        return [], "No Marketing Brief found — skipping"

    source = _read_frontend_files(src)
    prompt = COPY_CHECK_PROMPT.format(marketing_brief=brief[:3000], source_files=source)

    raw = await chat(
        _qa_system_prompt(),
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=2000,
        json_mode=True,
        project_slug=slug,
    )

    try:
        data = json.loads(raw)
        return data.get("checks", []), data.get("summary", "")
    except json.JSONDecodeError:
        log.warning("Failed to parse copy compliance response")
        return [], "Error parsing LLM response"


async def _ux_compliance(slug: str, src: Path) -> tuple[list[dict], str]:
    """Check code against UX spec using LLM."""
    spec = store.load_document(slug, "UX_SPEC.md")
    if not spec:
        return [], "No UX Spec found — skipping"

    source = _read_style_files(src)
    prompt = UX_CHECK_PROMPT.format(ux_spec=spec[:3000], source_files=source)

    raw = await chat(
        _qa_system_prompt(),
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=2000,
        json_mode=True,
        project_slug=slug,
    )

    try:
        data = json.loads(raw)
        return data.get("checks", []), data.get("summary", "")
    except json.JSONDecodeError:
        log.warning("Failed to parse UX compliance response")
        return [], "Error parsing LLM response"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _build_report(
    project_name: str,
    static_results: list[dict],
    prd_checks: list[dict],
    prd_summary: str,
    copy_checks: list[dict],
    copy_summary: str,
    ux_checks: list[dict],
    ux_summary: str,
) -> tuple[str, bool]:
    """Build QA_REPORT.md and return (report_text, passed)."""
    issues = []

    # Collect all failures
    for r in static_results:
        if r["status"] == "FAIL":
            issues.append(("CRITICAL", f"Static: {r['check']} — {r['detail']}"))
        elif r["status"] == "WARN":
            issues.append(("WARNING", f"Static: {r['check']} — {r['detail']}"))

    for c in prd_checks:
        if c.get("status") == "FAIL":
            issues.append(("CRITICAL", f"PRD: {c.get('feature', '?')} — {c.get('detail', '')}"))

    for c in copy_checks:
        if c.get("status") == "FAIL":
            issues.append(("CRITICAL", f"Copy: {c.get('element', '?')} — {c.get('detail', '')}"))
        elif c.get("status") == "WARN":
            issues.append(("WARNING", f"Copy: {c.get('element', '?')} — {c.get('detail', '')}"))

    for c in ux_checks:
        if c.get("status") == "FAIL":
            issues.append(("CRITICAL", f"UX: {c.get('element', '?')} — {c.get('detail', '')}"))
        elif c.get("status") == "WARN":
            issues.append(("WARNING", f"UX: {c.get('element', '?')} — {c.get('detail', '')}"))

    has_critical = any(sev == "CRITICAL" for sev, _ in issues)
    passed = not has_critical

    # Build markdown
    lines = [
        f"# QA Report: {project_name}",
        "",
        f"## Resultado: {'PASS' if passed else 'FAIL'}",
        "",
    ]

    # Static checks
    lines.append("## Checks estáticos")
    for r in static_results:
        lines.append(f"- [{r['status']}] {r['check']}: {r['detail']}")
    lines.append("")

    # PRD compliance
    lines.append("## PRD Compliance")
    if prd_checks:
        for c in prd_checks:
            lines.append(f"- [{c.get('status', '?')}] {c.get('feature', '?')}: {c.get('detail', '')}")
        lines.append(f"\n_{prd_summary}_")
    else:
        lines.append(f"_{prd_summary}_")
    lines.append("")

    # Marketing copy
    lines.append("## Marketing Copy")
    if copy_checks:
        for c in copy_checks:
            lines.append(f"- [{c.get('status', '?')}] {c.get('element', '?')}: {c.get('detail', '')}")
        lines.append(f"\n_{copy_summary}_")
    else:
        lines.append(f"_{copy_summary}_")
    lines.append("")

    # UX/UI compliance
    lines.append("## UX/UI Compliance")
    if ux_checks:
        for c in ux_checks:
            lines.append(f"- [{c.get('status', '?')}] {c.get('element', '?')}: {c.get('detail', '')}")
        lines.append(f"\n_{ux_summary}_")
    else:
        lines.append(f"_{ux_summary}_")
    lines.append("")

    # Issues summary
    if issues:
        lines.append("## Issues encontrados")
        for i, (sev, desc) in enumerate(issues, 1):
            lines.append(f"{i}. [{sev}] {desc}")
        lines.append("")

    # Recommendation
    lines.append("## Recomendación")
    if passed:
        lines.append("Deploy — todos los checks críticos pasaron.")
    else:
        lines.append("Fix needed — hay issues críticos que resolver antes del deploy.")
    lines.append("")

    return "\n".join(lines), passed


def _strip_code_fences(text: str) -> str:
    """Remove wrapping code fences from LLM output."""
    stripped = text.strip()
    m = re.match(r'^```\w*\n(.*?)```\s*$', stripped, re.DOTALL)
    if m:
        return m.group(1).strip()
    return stripped


# ---------------------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------------------

async def _auto_fix(slug: str, src: Path, report: str, send_fn) -> bool:
    """Attempt to auto-fix issues found by QA. Returns True if fixes were applied."""
    prd = store.load_document(slug, "PRD.md") or ""
    brief = store.load_document(slug, "MARKETING_BRIEF.md") or ""
    spec = store.load_document(slug, "UX_SPEC.md") or ""
    source = _read_source_files(src)

    prompt = FIX_PROMPT.format(
        issues=report[:3000],
        prd=prd[:2000],
        marketing_brief=brief[:1500],
        ux_spec=spec[:1500],
        source_files=source,
    )

    raw = await chat(
        SYSTEM_PROMPT + "\nYou fix code issues found during QA. Return precise file modifications.",
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=4096,
        json_mode=True,
        project_slug=slug,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Failed to parse auto-fix response")
        return False

    fixes = data.get("fixes", [])
    if not fixes:
        return False

    applied = 0
    for fix in fixes:
        file_path = fix.get("file_path", "")
        content = fix.get("content", "")
        if not content or not file_path:
            continue
        # Strip leading "src/" prefix — files are already relative to src/
        if file_path.startswith("src/"):
            file_path = file_path[4:]
        # Strip code fences from content
        content = _strip_code_fences(content)
        fpath = src / file_path
        try:
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content)
            applied += 1
            log.info("QA auto-fix: wrote %s", fpath)
        except OSError as e:
            log.warning("QA auto-fix failed for %s: %s", fpath, e)

    unfixable = data.get("unfixable", [])
    if applied > 0:
        await send_fn(f"Apliqué {applied} fix(es) automáticos.")
    if unfixable:
        await send_fn(f"Issues que no pude resolver automáticamente:\n" + "\n".join(f"- {u}" for u in unfixable))

    return applied > 0


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

async def start_qa(slug: str, send_fn) -> None:
    """Run the full QA suite on a project. Called after development completes."""
    state = store.load_state(slug)
    if not state:
        return

    project_name = state.get("name", slug)
    src = store.src_dir(slug)

    await send_fn("Arranco la validación QA del proyecto...")

    # 1. Static checks
    static_results = _static_checks(src)
    static_fails = sum(1 for r in static_results if r["status"] == "FAIL")
    await send_fn(f"Checks estáticos: {len(static_results)} verificaciones, {static_fails} fallas.")

    # 2. LLM checks (run all three)
    await send_fn("Verificando compliance con PRD, copy y diseño...")
    prd_checks, prd_summary = await _prd_compliance(slug, src)
    copy_checks, copy_summary = await _copy_compliance(slug, src)
    ux_checks, ux_summary = await _ux_compliance(slug, src)

    # 3. Build report
    report, passed = _build_report(
        project_name, static_results,
        prd_checks, prd_summary,
        copy_checks, copy_summary,
        ux_checks, ux_summary,
    )

    # Save report
    store.save_document(slug, "QA_REPORT.md", report)
    store.append_message(slug, "assistant", f"QA Report generated: {'PASS' if passed else 'FAIL'}")

    if passed:
        # Auto-transition to deployment
        store.transition(slug, "deployment")
        await send_fn(
            "QA pasó todos los checks. El proyecto está listo para deploy.\n"
            "Usá /approve para deployar."
        )
    else:
        # Transition to qa_review — user decides
        store.transition(slug, "qa_review")
        # Send a summary of issues
        issue_lines = []
        for r in static_results:
            if r["status"] == "FAIL":
                issue_lines.append(f"- {r['check']}: {r['detail']}")
        for checks in (prd_checks, copy_checks, ux_checks):
            for c in checks:
                if c.get("status") == "FAIL":
                    name = c.get("feature") or c.get("element") or "?"
                    issue_lines.append(f"- {name}: {c.get('detail', '')}")

        issues_text = "\n".join(issue_lines[:10]) if issue_lines else "Ver QA_REPORT.md"
        await send_fn(
            "QA encontró issues:\n\n"
            f"{issues_text}\n\n"
            "Opciones:\n"
            "- /approve → ignorar issues y deployar igual\n"
            "- Mandame un mensaje describiendo qué arreglar y lo intento"
        )


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle messages during qa_review state — attempt auto-fix and re-test."""
    state = store.load_state(slug)
    if not state:
        return

    src = store.src_dir(slug)
    report = store.load_document(slug, "QA_REPORT.md") or ""

    await send_fn("Intentando arreglar los issues...")

    # Combine user guidance with the QA report for the fix
    store.append_message(slug, "user", user_message)
    combined_report = f"User guidance: {user_message}\n\n{report}"

    fixed = await _auto_fix(slug, src, combined_report, send_fn)

    if fixed:
        # Re-run only static checks to verify the fix, skip full LLM re-check
        await send_fn("Verificando los fixes...")
        static_results = _static_checks(src)
        static_fails = sum(1 for r in static_results if r["status"] == "FAIL")

        if static_fails == 0:
            await send_fn(
                "Fixes aplicados, checks estáticos OK.\n"
                "Usá /approve para deployar o pedí más cambios."
            )
        else:
            fail_details = [f"- {r['check']}: {r['detail']}" for r in static_results if r["status"] == "FAIL"]
            await send_fn(
                "Fixes aplicados pero quedan issues estáticos:\n"
                f"{chr(10).join(fail_details)}\n\n"
                "Usá /approve para deployar igual o pedí más cambios."
            )
    else:
        await send_fn(
            "No pude aplicar fixes automáticos.\n"
            "Podés /approve para deployar igual, o describí más específicamente qué arreglar."
        )
