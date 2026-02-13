"""Deployment Agent — handles Docker/Coolify deployment, SSL, env config, smoke tests.

This agent:
- Ensures the project has a Dockerfile (generates one if missing)
- Creates docker-compose.yml if needed
- Allocates a port
- Deploys via Coolify API or direct Docker
- Runs smoke tests
- Notifies the user with the live URL
"""

import asyncio
import json
import hashlib
import logging
import subprocess
from pathlib import Path

from bot.llm.client import chat
from bot.memory import store
from bot.config import (
    LAN_IP,
    PORT_RANGE_START,
    PORT_RANGE_END,
    COOLIFY_BASE_URL,
    COOLIFY_SERVER_UUID,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the Deployment Agent for FactoryBot. You deploy projects to a Raspberry Pi 5 \
running Docker and Coolify. You communicate in Argentine Spanish.

The host:
- Raspberry Pi 5, 8GB RAM, ARM64
- Docker 29.2.1 + Docker Compose
- Coolify at http://localhost:8000
- LAN IP: 192.168.1.14
- Traefik as reverse proxy (via Coolify)

Your job:
1. Ensure the project has a Dockerfile (generate one if missing)
2. Build the Docker image
3. Run it on an allocated port
4. Run a basic smoke test (HTTP GET to /)
5. Report the URL to the user

Keep it simple. Use docker compose up -d for deployment.
"""

DOCKERFILE_PROMPT = """\
Generate a Dockerfile for this project. It must:
- Be ARM64 compatible
- Be as lightweight as possible (use alpine or slim base images)
- Install only necessary dependencies
- Expose the correct port
- Have a proper CMD/ENTRYPOINT

Project files:
{file_list}

PRD technical section:
{tech_section}

Package file content (if exists):
{package_content}

Return ONLY the Dockerfile content, no markdown fences.
"""


async def start_deployment(slug: str, send_fn) -> None:
    """Deploy the project."""
    state = store.load_state(slug)
    if not state:
        return

    src = store.src_dir(slug)
    if not src.exists() or not any(src.iterdir()):
        await send_fn("Error: el directorio src/ está vacío. No hay nada para deployar.")
        store.transition(slug, "blocked")
        return

    await send_fn("Arrancando el deploy...")

    # 1. Ensure Dockerfile exists
    dockerfile = src / "Dockerfile"
    if not dockerfile.exists():
        await send_fn("No hay Dockerfile. Generando uno...")
        await _generate_dockerfile(slug, src)

    if not dockerfile.exists():
        await send_fn("No pude generar el Dockerfile. Deploy abortado.")
        store.transition(slug, "blocked")
        return

    # 2. Allocate port
    port = _allocate_port(slug)
    store.update_state(slug, port=port)
    await send_fn(f"Puerto asignado: {port}")

    # 3. Generate docker-compose.yml
    await _generate_compose(slug, src, port)

    # 4. Build and run
    await send_fn("Construyendo imagen Docker...")
    success = await _docker_compose_up(src, slug, send_fn)

    if not success:
        await send_fn("Falló el deploy con Docker. Revisá los logs.")
        store.transition(slug, "blocked")
        return

    # 5. Smoke test
    url = f"http://{LAN_IP}:{port}"
    await send_fn("Corriendo smoke test...")
    await asyncio.sleep(5)  # Wait for container to start

    smoke_ok = await _smoke_test(url)

    if smoke_ok:
        store.update_state(slug, url=url)
        store.transition(slug, "deployed")
        await send_fn(
            f"Deploy exitoso!\n\n"
            f"Tu proyecto está en: {url}\n\n"
            f"Generando documentación post-deploy..."
        )
        # Generate post-deployment docs
        await _generate_project_log(slug, send_fn)
    else:
        await send_fn(
            f"El container está corriendo pero el smoke test falló en {url}.\n"
            "Puede que la app tarde en iniciar. Probá en un minuto.\n"
            "Si no funciona, revisá los logs con:\n"
            f"`docker logs factorybot-{slug}`"
        )
        store.update_state(slug, url=url)
        store.transition(slug, "deployed")
        await _generate_project_log(slug, send_fn)


async def handle(slug: str, user_message: str, send_fn) -> None:
    """Handle messages during deployment phase."""
    await send_fn(
        "El proyecto está en fase de deploy. "
        "Usá /approve para arrancar el deploy o esperá a que termine."
    )


async def _generate_dockerfile(slug: str, src: Path) -> None:
    """Generate a Dockerfile using LLM."""
    files = _list_files(src)
    prd = store.load_document(slug, "PRD.md") or ""

    # Try to read package.json or requirements.txt
    package_content = ""
    for pkg_file in ["package.json", "requirements.txt", "Pipfile", "pyproject.toml"]:
        pkg_path = src / pkg_file
        if pkg_path.exists():
            package_content = f"--- {pkg_file} ---\n{pkg_path.read_text()[:1000]}"
            break

    # Extract tech section from PRD
    tech_section = ""
    if "## 4" in prd:
        start = prd.index("## 4")
        end = prd.index("## 5") if "## 5" in prd else start + 1000
        tech_section = prd[start:end]

    prompt = DOCKERFILE_PROMPT.format(
        file_list="\n".join(files[:30]),
        tech_section=tech_section[:1000],
        package_content=package_content,
    )

    content = await chat(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.2,
        max_tokens=1000,
    )

    content = _strip_fences(content)
    (src / "Dockerfile").write_text(content)
    log.info("Generated Dockerfile for %s", slug)


async def _generate_compose(slug: str, src: Path, port: int) -> None:
    """Generate a docker-compose.yml for the project."""
    compose = {
        "version": "3.8",
        "services": {
            "app": {
                "build": ".",
                "container_name": f"factorybot-{slug}",
                "ports": [f"{port}:3000"],
                "restart": "unless-stopped",
                "env_file": [".env"] if (src / ".env").exists() else [],
            }
        }
    }

    # Check if there's a different internal port in Dockerfile
    dockerfile = src / "Dockerfile"
    if dockerfile.exists():
        df_content = dockerfile.read_text()
        for line in df_content.split("\n"):
            if line.strip().startswith("EXPOSE"):
                try:
                    internal_port = int(line.strip().split()[-1])
                    compose["services"]["app"]["ports"] = [f"{port}:{internal_port}"]
                except (ValueError, IndexError):
                    pass

    compose_path = src / "docker-compose.yml"
    compose_path.write_text(_dict_to_yaml(compose))
    log.info("Generated docker-compose.yml for %s on port %d", slug, port)


async def _docker_compose_up(src: Path, slug: str, send_fn) -> bool:
    """Build and start the Docker container."""
    try:
        # Build
        result = await asyncio.to_thread(
            subprocess.run,
            "docker compose build --no-cache",
            shell=True,
            cwd=str(src),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min for build
        )

        if result.returncode != 0:
            error = (result.stderr or result.stdout)[:500]
            log.error("Docker build failed: %s", error)
            await send_fn(f"Docker build falló:\n```\n{error[:300]}\n```")
            return False

        await send_fn("Imagen construida. Levantando container...")

        # Up
        result = await asyncio.to_thread(
            subprocess.run,
            "docker compose up -d",
            shell=True,
            cwd=str(src),
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            error = (result.stderr or result.stdout)[:500]
            log.error("Docker up failed: %s", error)
            await send_fn(f"Docker up falló:\n```\n{error[:300]}\n```")
            return False

        return True

    except subprocess.TimeoutExpired:
        await send_fn("Docker build timeout (10 min). El proyecto puede ser muy pesado para la Pi.")
        return False
    except Exception as e:
        log.exception("Docker error: %s", e)
        return False


async def _smoke_test(url: str) -> bool:
    """Run a basic HTTP smoke test."""
    for attempt in range(3):
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 10 {url}",
                shell=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            status = result.stdout.strip().strip("'")
            if status.startswith("2") or status.startswith("3"):
                log.info("Smoke test passed: %s -> %s", url, status)
                return True
            log.warning("Smoke test attempt %d: status %s", attempt + 1, status)
        except Exception as e:
            log.warning("Smoke test attempt %d failed: %s", attempt + 1, e)

        if attempt < 2:
            await asyncio.sleep(5)

    return False


async def _generate_project_log(slug: str, send_fn) -> None:
    """Generate PROJECT_LOG.md after successful deployment."""
    state = store.load_state(slug)
    prd = store.load_document(slug, "PRD.md") or ""
    idea = store.load_document(slug, "IDEA_SUMMARY.md") or ""

    prompt = (
        "Generate a PROJECT_LOG.md for this deployed project. Include:\n"
        "- Project name, email, description\n"
        "- Key decisions made and why\n"
        "- Technical stack summary\n"
        "- V1 feature list (what shipped)\n"
        "- Deferred features\n"
        "- Known limitations\n"
        "- Deployment details (URL, port, container name)\n"
        "- How to revisit (commands, what to modify)\n\n"
        f"PROJECT STATE: {json.dumps(state, indent=2)}\n\n"
        f"IDEA SUMMARY:\n{idea[:1500]}\n\n"
        f"PRD:\n{prd[:2000]}\n"
    )

    project_log = await chat(
        "You generate concise post-deployment documentation in markdown. Use Argentine Spanish.",
        [{"role": "user", "content": prompt}],
        heavy=True,
        temperature=0.3,
        max_tokens=2000,
    )

    store.save_document(slug, "PROJECT_LOG.md", project_log)
    store.append_message(slug, "assistant", "[PROJECT_LOG.md generado]")
    await send_fn("Documentación post-deploy generada (PROJECT_LOG.md).")


def _allocate_port(slug: str) -> int:
    """Allocate a port for the project, avoiding conflicts."""
    used_ports = set()
    for p in store.list_projects():
        if p.get("port"):
            used_ports.add(p["port"])

    # Also check what's actually in use
    try:
        result = subprocess.run(
            "docker ps --format '{{.Ports}}'",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().split("\n"):
            for part in line.split(","):
                if "->" in part:
                    try:
                        host_part = part.split("->")[0].strip()
                        port_str = host_part.split(":")[-1]
                        used_ports.add(int(port_str))
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass

    for port in range(PORT_RANGE_START, PORT_RANGE_END + 1):
        if port not in used_ports:
            return port

    # Fallback: deterministic from slug
    return PORT_RANGE_START + (int(hashlib.md5(slug.encode()).hexdigest(), 16) % 100)


def _list_files(directory: Path) -> list[str]:
    """List files recursively."""
    files = []
    if not directory.exists():
        return files
    for f in sorted(directory.rglob("*")):
        if f.is_file() and "node_modules" not in str(f) and ".git" not in str(f):
            files.append(str(f.relative_to(directory)))
    return files


def _strip_fences(text: str) -> str:
    """Remove markdown fences."""
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _dict_to_yaml(d: dict, indent: int = 0) -> str:
    """Simple dict-to-YAML converter (no external deps)."""
    lines = []
    prefix = "  " * indent
    for key, value in d.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_dict_to_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    # First key on same line as -
                    first = True
                    for k, v in item.items():
                        if first:
                            lines.append(f"{prefix}  - {k}: {_yaml_value(v)}")
                            first = False
                        else:
                            lines.append(f"{prefix}    {k}: {_yaml_value(v)}")
                else:
                    lines.append(f"{prefix}  - {_yaml_value(item)}")
        else:
            lines.append(f"{prefix}{key}: {_yaml_value(value)}")
    return "\n".join(lines)


def _yaml_value(v) -> str:
    """Format a YAML value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if any(c in v for c in ":{}\n[]&*?|>!%@`"):
            return f'"{v}"'
        return v
    return str(v)
