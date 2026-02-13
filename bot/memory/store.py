"""File-based Project Memory Store for FactoryBot."""

import json
import re
import logging
from pathlib import Path
from datetime import datetime, timezone

from bot.config import PROJECTS_DIR, MAX_HISTORY_STORED

log = logging.getLogger(__name__)

# Valid project states
STATES = [
    "ideation",
    "prd_generation",
    "prd_review",
    "marketing",
    "marketing_review",
    "ux_design",
    "ux_review",
    "approved",
    "development",
    "deployment",
    "deployed",
    "paused",
    "blocked",
]


def slugify(name: str) -> str:
    """Convert a project name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "untitled"


def _project_dir(slug: str) -> Path:
    return PROJECTS_DIR / slug


def _state_file(slug: str) -> Path:
    return _project_dir(slug) / "state.json"


def _history_file(slug: str) -> Path:
    return _project_dir(slug) / "conversation_history.json"


# --- Project lifecycle ---


def create_project(name: str, email: str = "") -> dict:
    """Create a new project directory and initial state."""
    slug = slugify(name)
    d = _project_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "src").mkdir(exist_ok=True)

    state = {
        "name": name,
        "slug": slug,
        "email": email,
        "state": "ideation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "paused_from": None,
        "port": None,
        "url": None,
        "deferred_features": [],
    }
    _save_state(slug, state)
    _save_history(slug, [])
    log.info("Created project: %s (%s)", name, slug)
    return state


def list_projects() -> list[dict]:
    """List all projects with their current state."""
    projects = []
    if not PROJECTS_DIR.exists():
        return projects
    for d in sorted(PROJECTS_DIR.iterdir()):
        if d.is_dir() and (d / "state.json").exists():
            state = load_state(d.name)
            if state:
                projects.append(state)
    return projects


def find_project(name_or_slug: str) -> dict | None:
    """Find a project by name or slug (fuzzy)."""
    slug = slugify(name_or_slug)
    # Exact match
    state = load_state(slug)
    if state:
        return state
    # Partial match
    for p in list_projects():
        if slug in p["slug"] or slug in slugify(p["name"]):
            return p
    return None


def get_active_project() -> dict | None:
    """Get the currently active (non-deployed, non-paused) project."""
    for p in list_projects():
        if p["state"] not in ("deployed", "paused"):
            return p
    return None


# --- State management ---


def load_state(slug: str) -> dict | None:
    f = _state_file(slug)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        log.exception("Failed to load state for %s", slug)
        return None


def _save_state(slug: str, state: dict):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _state_file(slug).write_text(json.dumps(state, indent=2))


def update_state(slug: str, **updates) -> dict | None:
    state = load_state(slug)
    if not state:
        return None
    state.update(updates)
    _save_state(slug, state)
    return state


def transition(slug: str, new_state: str) -> dict | None:
    """Transition project to a new state."""
    if new_state not in STATES:
        raise ValueError(f"Invalid state: {new_state}")
    return update_state(slug, state=new_state)


def pause_project(slug: str) -> dict | None:
    state = load_state(slug)
    if not state or state["state"] == "paused":
        return state
    return update_state(slug, state="paused", paused_from=state["state"])


def resume_project(slug: str) -> dict | None:
    state = load_state(slug)
    if not state or state["state"] != "paused":
        return state
    resume_to = state.get("paused_from") or "ideation"
    return update_state(slug, state=resume_to, paused_from=None)


# --- Conversation history ---


def _save_history(slug: str, history: list[dict]):
    _history_file(slug).write_text(json.dumps(history, indent=2))


def load_history(slug: str) -> list[dict]:
    f = _history_file(slug)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def append_message(slug: str, role: str, content: str):
    """Add a message to conversation history."""
    history = load_history(slug)
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Trim to max
    if len(history) > MAX_HISTORY_STORED:
        history = history[-MAX_HISTORY_STORED:]
    _save_history(slug, history)


def get_context_messages(slug: str, limit: int = 20) -> list[dict]:
    """Get recent messages formatted for LLM context."""
    history = load_history(slug)
    recent = history[-limit:]
    return [{"role": m["role"], "content": m["content"]} for m in recent]


# --- Document management ---


def save_document(slug: str, filename: str, content: str):
    """Save a document (IDEA_SUMMARY.md, PRD.md, PROJECT_LOG.md) to project dir."""
    path = _project_dir(slug) / filename
    path.write_text(content)
    log.info("Saved %s for project %s", filename, slug)


def load_document(slug: str, filename: str) -> str | None:
    """Load a document from the project directory."""
    path = _project_dir(slug) / filename
    if not path.exists():
        return None
    return path.read_text()


def project_dir(slug: str) -> Path:
    """Get the project directory path."""
    return _project_dir(slug)


def src_dir(slug: str) -> Path:
    """Get the project source code directory."""
    return _project_dir(slug) / "src"


def add_deferred_feature(slug: str, feature: str):
    """Add a feature to the deferred list."""
    state = load_state(slug)
    if not state:
        return
    deferred = state.get("deferred_features", [])
    if feature not in deferred:
        deferred.append(feature)
    update_state(slug, deferred_features=deferred)
