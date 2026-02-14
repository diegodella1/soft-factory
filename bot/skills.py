"""Skills loader — reads installed SKILL.md files and provides condensed
knowledge for each FactoryBot agent.

Skills are installed at ~/.agents/skills/<name>/SKILL.md via skills.sh.
Each agent gets a subset of relevant skills injected into its system prompt.
Content is cached after first load to avoid repeated disk reads.
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

SKILLS_DIR = Path.home() / ".agents" / "skills"

# Map each agent to the skills it should use
AGENT_SKILLS: dict[str, list[str]] = {
    "ideation": [],  # Brainstorming — no technical skills needed
    "prd": [
        "api-design-principles",
        "nodejs-best-practices",
    ],
    "marketing": [
        "seo-content-writer",
        "marketing-ideas",
        "marketing-psychology",
    ],
    "ux_ui": [
        "tailwind-patterns",
        "ui-ux-pro-max",
    ],
    "development": [
        "nodejs-best-practices",
        "tailwind-patterns",
        "docker-expert",
        "clean-code",
    ],
    "qa": [
        "code-reviewer",
        "testing-patterns",
        "clean-code",
    ],
    "deployment": [
        "docker-expert",
        "deployment-engineer",
    ],
    "revision": [
        "nodejs-best-practices",
        "tailwind-patterns",
        "clean-code",
    ],
}

# Cache loaded skills to avoid re-reading files
_cache: dict[str, str] = {}


def _load_skill(name: str) -> str:
    """Load and condense a single skill file."""
    if name in _cache:
        return _cache[name]

    skill_file = SKILLS_DIR / name / "SKILL.md"
    if not skill_file.exists():
        log.debug("Skill not found: %s", skill_file)
        _cache[name] = ""
        return ""

    try:
        raw = skill_file.read_text()
    except OSError:
        _cache[name] = ""
        return ""

    # Strip YAML front matter
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end != -1:
            raw = raw[end + 3:].strip()

    # Strip lines that are Claude Code-specific instructions (tool usage, bash commands, etc.)
    lines = []
    skip_block = False
    for line in raw.split("\n"):
        # Skip bash code blocks (tool commands, not useful for GPT-4o agents)
        if line.strip().startswith("```bash") or line.strip().startswith("```powershell"):
            skip_block = True
            continue
        if skip_block:
            if line.strip() == "```":
                skip_block = False
            continue
        # Skip "When invoked" sections with tool instructions
        if "Use internal tools" in line or "Shell commands are fallbacks" in line:
            continue
        lines.append(line)

    content = "\n".join(lines)

    # Truncate if too long (keep under ~2000 chars per skill)
    if len(content) > 2000:
        content = content[:2000] + "\n...(truncated)"

    _cache[name] = content
    return content


def get_agent_skills(agent_name: str) -> str:
    """Get concatenated skill knowledge for an agent.

    Returns empty string if no skills are mapped or none are installed.
    """
    skill_names = AGENT_SKILLS.get(agent_name, [])
    if not skill_names:
        return ""

    parts = []
    for name in skill_names:
        content = _load_skill(name)
        if content:
            parts.append(content)

    if not parts:
        return ""

    header = (
        "KNOWLEDGE BASE (apply these best practices in your work):\n"
        "=========================================================\n"
    )
    return header + "\n---\n".join(parts)
