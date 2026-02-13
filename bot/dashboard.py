"""Lightweight web dashboard for FactoryBot.

Runs on port 3099. Lets you view/edit agent prompts, see project status,
and monitor the system.
"""

import json
import logging
import importlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from threading import Thread

from bot.config import LAN_IP

log = logging.getLogger(__name__)

DASHBOARD_PORT = 3099

# Map of agent modules and their editable prompt attributes
AGENT_PROMPTS = {
    "ideation": {
        "module": "bot.agents.ideation",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del Ideation Agent",
            "SUMMARY_PROMPT": "Prompt para generar IDEA_SUMMARY.md",
        },
    },
    "prd": {
        "module": "bot.agents.prd_agent",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del PRD Agent",
            "QUESTIONS_PROMPT": "Prompt para preguntas técnicas",
            "GENERATE_PRD_PROMPT": "Prompt para generar el PRD",
        },
    },
    "marketing": {
        "module": "bot.agents.marketing",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del Marketing Agent",
            "QUESTIONS_PROMPT": "Prompt para preguntas de marketing",
            "BRIEF_PROMPT": "Prompt para generar MARKETING_BRIEF.md",
        },
    },
    "ux_ui": {
        "module": "bot.agents.ux_ui",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del UX/UI Agent",
            "QUESTIONS_PROMPT": "Prompt para preguntas de diseño",
            "SPEC_PROMPT": "Prompt para generar UX_SPEC.md",
        },
    },
    "development": {
        "module": "bot.agents.development",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del Development Agent",
            "PLAN_PROMPT": "Prompt para generar el plan de build",
            "FILE_PROMPT": "Prompt para generar archivos",
            "FIX_PROMPT": "Prompt para diagnosticar errores",
        },
    },
    "deployment": {
        "module": "bot.agents.deployment",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del Deployment Agent",
            "DOCKERFILE_PROMPT": "Prompt para generar Dockerfile",
        },
    },
}

# Custom prompts override file
PROMPTS_FILE = Path(__file__).resolve().parent.parent / "custom_prompts.json"


def _load_custom_prompts() -> dict:
    if PROMPTS_FILE.exists():
        try:
            return json.loads(PROMPTS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_custom_prompts(data: dict):
    PROMPTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _get_prompt(agent: str, prompt_name: str) -> str:
    """Get current prompt value (custom override or default from module)."""
    custom = _load_custom_prompts()
    key = f"{agent}.{prompt_name}"
    if key in custom:
        return custom[key]
    # Load from module
    info = AGENT_PROMPTS.get(agent)
    if not info:
        return ""
    mod = importlib.import_module(info["module"])
    return getattr(mod, prompt_name, "")


def _set_prompt(agent: str, prompt_name: str, value: str):
    """Set a custom prompt override and apply it to the loaded module."""
    custom = _load_custom_prompts()
    key = f"{agent}.{prompt_name}"
    custom[key] = value
    _save_custom_prompts(custom)
    # Hot-reload into the module
    info = AGENT_PROMPTS.get(agent)
    if info:
        mod = importlib.import_module(info["module"])
        setattr(mod, prompt_name, value)
    log.info("Updated prompt: %s.%s", agent, prompt_name)


def _get_projects_status() -> list[dict]:
    from bot.memory.store import list_projects
    return list_projects()


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "":
            self._serve_dashboard()
        elif path == "/api/prompts":
            self._api_get_prompts()
        elif path == "/api/projects":
            self._api_get_projects()
        else:
            self._respond(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/prompts":
            self._api_set_prompt()
        else:
            self._respond(404, "Not found")

    def _api_get_prompts(self):
        result = {}
        for agent, info in AGENT_PROMPTS.items():
            result[agent] = {}
            for prompt_name, label in info["prompts"].items():
                result[agent][prompt_name] = {
                    "label": label,
                    "value": _get_prompt(agent, prompt_name),
                }
        self._respond_json(result)

    def _api_get_projects(self):
        projects = _get_projects_status()
        self._respond_json(projects)

    def _api_set_prompt(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        try:
            data = json.loads(body)
            agent = data["agent"]
            prompt_name = data["prompt"]
            value = data["value"]
            _set_prompt(agent, prompt_name, value)
            self._respond_json({"ok": True})
        except (json.JSONDecodeError, KeyError) as e:
            self._respond(400, str(e))

    def _serve_dashboard(self):
        html = _dashboard_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _respond(self, code: int, msg: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())

    def _respond_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())


def _dashboard_html() -> str:
    return """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FactoryBot Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; background: #0f0f0f; color: #e0e0e0; }
.container { max-width: 1100px; margin: 0 auto; padding: 20px; }
h1 { font-size: 1.5rem; margin-bottom: 20px; color: #fff; }
h2 { font-size: 1.1rem; margin: 20px 0 10px; color: #a0a0a0; text-transform: uppercase; letter-spacing: 1px; }
.projects { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 30px; }
.project-card { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 14px 18px; min-width: 200px; }
.project-card .name { font-weight: 600; color: #fff; }
.project-card .state { font-size: 0.85rem; color: #888; margin-top: 4px; }
.tabs { display: flex; gap: 4px; margin-bottom: 16px; flex-wrap: wrap; }
.tab { padding: 8px 16px; background: #1a1a1a; border: 1px solid #333; border-radius: 6px; cursor: pointer; color: #aaa; font-size: 0.9rem; }
.tab.active { background: #2a2a2a; color: #fff; border-color: #555; }
.prompt-section { margin-bottom: 20px; }
.prompt-label { font-size: 0.85rem; color: #888; margin-bottom: 6px; }
textarea { width: 100%; min-height: 200px; background: #111; color: #ddd; border: 1px solid #333;
  border-radius: 6px; padding: 12px; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem;
  resize: vertical; line-height: 1.5; }
textarea:focus { outline: none; border-color: #666; }
.btn { padding: 8px 20px; background: #2563eb; color: #fff; border: none; border-radius: 6px;
  cursor: pointer; font-size: 0.9rem; margin-top: 8px; }
.btn:hover { background: #1d4ed8; }
.btn:disabled { opacity: 0.5; cursor: default; }
.saved { color: #22c55e; font-size: 0.85rem; margin-left: 10px; display: none; }
.agent-section { display: none; }
.agent-section.active { display: block; }
</style>
</head>
<body>
<div class="container">
  <h1>FactoryBot Dashboard</h1>

  <h2>Proyectos</h2>
  <div class="projects" id="projects">Cargando...</div>

  <h2>Prompts de Agentes</h2>
  <div class="tabs" id="tabs"></div>
  <div id="agents"></div>
</div>

<script>
const AGENTS = {
  ideation: 'Ideation',
  prd: 'PRD',
  marketing: 'Marketing',
  ux_ui: 'UX/UI',
  development: 'Development',
  deployment: 'Deployment'
};

let currentAgent = 'ideation';

async function loadProjects() {
  const res = await fetch('/api/projects');
  const projects = await res.json();
  const el = document.getElementById('projects');
  if (!projects.length) {
    el.innerHTML = '<div style="color:#666">No hay proyectos</div>';
    return;
  }
  el.innerHTML = projects.map(p => `
    <div class="project-card">
      <div class="name">${p.name}</div>
      <div class="state">${p.state}${p.url ? ' — ' + p.url : ''}</div>
    </div>
  `).join('');
}

async function loadPrompts() {
  const res = await fetch('/api/prompts');
  const data = await res.json();

  const tabs = document.getElementById('tabs');
  const agents = document.getElementById('agents');
  tabs.innerHTML = '';
  agents.innerHTML = '';

  for (const [agent, label] of Object.entries(AGENTS)) {
    // Tab
    const tab = document.createElement('div');
    tab.className = 'tab' + (agent === currentAgent ? ' active' : '');
    tab.textContent = label;
    tab.onclick = () => switchAgent(agent);
    tabs.appendChild(tab);

    // Section
    const section = document.createElement('div');
    section.className = 'agent-section' + (agent === currentAgent ? ' active' : '');
    section.id = 'section-' + agent;

    const prompts = data[agent] || {};
    for (const [pname, info] of Object.entries(prompts)) {
      section.innerHTML += `
        <div class="prompt-section">
          <div class="prompt-label">${info.label} (${pname})</div>
          <textarea id="p-${agent}-${pname}">${escHtml(info.value)}</textarea>
          <button class="btn" onclick="savePrompt('${agent}','${pname}')">Guardar</button>
          <span class="saved" id="saved-${agent}-${pname}">Guardado</span>
        </div>`;
    }
    agents.appendChild(section);
  }
}

function switchAgent(agent) {
  currentAgent = agent;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.agent-section').forEach(s => s.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('section-' + agent).classList.add('active');
}

async function savePrompt(agent, prompt) {
  const textarea = document.getElementById('p-' + agent + '-' + prompt);
  const res = await fetch('/api/prompts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ agent, prompt, value: textarea.value })
  });
  if (res.ok) {
    const saved = document.getElementById('saved-' + agent + '-' + prompt);
    saved.style.display = 'inline';
    setTimeout(() => saved.style.display = 'none', 2000);
  }
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

loadProjects();
loadPrompts();
</script>
</body>
</html>
"""


def start_dashboard():
    """Start the dashboard server in a background thread."""
    server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Dashboard running at http://%s:%d", LAN_IP, DASHBOARD_PORT)
