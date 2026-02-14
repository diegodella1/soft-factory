"""Lightweight web dashboard for FactoryBot.

Runs on port 3099. Lets you view/edit agent prompts, see project status,
monitor token usage and costs.
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
    "qa": {
        "module": "bot.agents.qa_agent",
        "prompts": {
            "SYSTEM_PROMPT": "Prompt principal del QA Agent",
            "PRD_CHECK_PROMPT": "Prompt para verificar compliance con PRD",
            "COPY_CHECK_PROMPT": "Prompt para verificar marketing copy",
            "UX_CHECK_PROMPT": "Prompt para verificar UX/UI",
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


def _get_all_usage() -> dict:
    """Get usage data for all projects + global totals."""
    from bot.memory.store import list_projects, load_usage
    projects = list_projects()
    per_project = {}
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "calls": 0}

    for p in projects:
        slug = p.get("slug", "")
        usage = load_usage(slug)
        per_project[slug] = usage
        totals["prompt_tokens"] += usage.get("total_prompt_tokens", 0)
        totals["completion_tokens"] += usage.get("total_completion_tokens", 0)
        totals["cost_usd"] += usage.get("total_cost_usd", 0.0)
        totals["calls"] += usage.get("calls", 0)

    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return {"projects": per_project, "totals": totals}


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
        elif path == "/api/usage":
            self._api_get_usage()
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

    def _api_get_usage(self):
        usage = _get_all_usage()
        self._respond_json(usage)

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
body { font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #09090b; color: #e4e4e7; line-height: 1.5; }
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }

/* Header */
.header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 32px; padding-bottom: 16px; border-bottom: 1px solid #27272a; }
.header h1 { font-size: 1.5rem; font-weight: 700; color: #fafafa; letter-spacing: -0.02em; }
.header .subtitle { font-size: 0.8rem; color: #71717a; }

/* Global stats bar */
.stats-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 32px; }
.stat-card { background: #18181b; border: 1px solid #27272a; border-radius: 10px; padding: 16px 20px; }
.stat-card .label { font-size: 0.75rem; color: #71717a; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
.stat-card .value { font-size: 1.5rem; font-weight: 700; color: #fafafa; }
.stat-card .value.cost { color: #a78bfa; }
.stat-card .detail { font-size: 0.75rem; color: #52525b; margin-top: 2px; }

/* Section headers */
h2 { font-size: 0.85rem; font-weight: 600; color: #71717a; text-transform: uppercase; letter-spacing: 0.08em; margin: 28px 0 14px; }

/* Projects grid */
.projects { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; margin-bottom: 32px; }
.project-card { background: #18181b; border: 1px solid #27272a; border-radius: 10px; padding: 18px 20px; transition: border-color 0.15s; }
.project-card:hover { border-color: #3f3f46; }
.project-card .top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.project-card .name { font-weight: 600; font-size: 1rem; color: #fafafa; }
.badge { display: inline-block; font-size: 0.7rem; font-weight: 600; padding: 2px 8px; border-radius: 9999px; text-transform: uppercase; letter-spacing: 0.04em; }
.badge-deployed { background: #052e16; color: #4ade80; border: 1px solid #166534; }
.badge-development { background: #172554; color: #60a5fa; border: 1px solid #1e40af; }
.badge-ideation { background: #1c1917; color: #fbbf24; border: 1px solid #92400e; }
.badge-paused { background: #1c1917; color: #a8a29e; border: 1px solid #44403c; }
.badge-blocked { background: #450a0a; color: #f87171; border: 1px solid #991b1b; }
.badge-default { background: #18181b; color: #a1a1aa; border: 1px solid #3f3f46; }
.project-card .meta { display: flex; flex-wrap: wrap; gap: 16px; font-size: 0.8rem; color: #a1a1aa; margin-bottom: 8px; }
.project-card .meta span { display: flex; align-items: center; gap: 4px; }
.project-card .url-link { display: inline-block; font-size: 0.8rem; color: #60a5fa; text-decoration: none; margin-top: 6px; word-break: break-all; }
.project-card .url-link:hover { text-decoration: underline; color: #93bbfd; }
.project-card .usage-row { display: flex; gap: 12px; font-size: 0.75rem; color: #71717a; margin-top: 8px; padding-top: 8px; border-top: 1px solid #27272a; }
.project-card .usage-row .usage-item { display: flex; align-items: center; gap: 4px; }
.no-projects { color: #52525b; font-style: italic; padding: 20px; }

/* Tabs */
.tabs { display: flex; gap: 4px; margin-bottom: 16px; flex-wrap: wrap; }
.tab { padding: 8px 16px; background: #18181b; border: 1px solid #27272a; border-radius: 8px; cursor: pointer; color: #a1a1aa; font-size: 0.85rem; font-weight: 500; transition: all 0.15s; }
.tab:hover { background: #27272a; color: #e4e4e7; }
.tab.active { background: #27272a; color: #fafafa; border-color: #3f3f46; }

/* Prompts */
.prompt-section { margin-bottom: 20px; }
.prompt-label { font-size: 0.8rem; color: #71717a; margin-bottom: 6px; font-weight: 500; }
textarea { width: 100%; min-height: 200px; background: #09090b; color: #d4d4d8; border: 1px solid #27272a;
  border-radius: 8px; padding: 12px; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.8rem;
  resize: vertical; line-height: 1.6; transition: border-color 0.15s; }
textarea:focus { outline: none; border-color: #3f3f46; }
.btn { padding: 8px 20px; background: #2563eb; color: #fff; border: none; border-radius: 8px;
  cursor: pointer; font-size: 0.85rem; font-weight: 500; margin-top: 8px; transition: background 0.15s; }
.btn:hover { background: #1d4ed8; }
.btn:disabled { opacity: 0.5; cursor: default; }
.saved { color: #4ade80; font-size: 0.8rem; margin-left: 10px; display: none; font-weight: 500; }
.agent-section { display: none; }
.agent-section.active { display: block; }

/* SVG icons inline */
.icon { width: 14px; height: 14px; display: inline-block; vertical-align: middle; }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>FactoryBot</h1>
      <div class="subtitle">Software Factory Dashboard</div>
    </div>
    <div style="font-size:0.75rem;color:#52525b;" id="last-refresh"></div>
  </div>

  <!-- Global stats -->
  <div class="stats-bar" id="stats-bar">
    <div class="stat-card"><div class="label">Proyectos</div><div class="value" id="s-projects">-</div></div>
    <div class="stat-card"><div class="label">Llamadas LLM</div><div class="value" id="s-calls">-</div></div>
    <div class="stat-card"><div class="label">Tokens totales</div><div class="value" id="s-tokens">-</div><div class="detail" id="s-tokens-detail"></div></div>
    <div class="stat-card"><div class="label">Costo estimado</div><div class="value cost" id="s-cost">-</div></div>
  </div>

  <h2>Proyectos</h2>
  <div class="projects" id="projects"><div class="no-projects">Cargando...</div></div>

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
  qa: 'QA',
  deployment: 'Deployment'
};

const STATE_BADGES = {
  deployed: 'badge-deployed',
  development: 'badge-development',
  qa_testing: 'badge-development',
  qa_review: 'badge-development',
  ideation: 'badge-ideation',
  paused: 'badge-paused',
  blocked: 'badge-blocked',
};

let currentAgent = 'ideation';

function fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

function badgeClass(state) {
  for (const [key, cls] of Object.entries(STATE_BADGES)) {
    if (state.includes(key)) return cls;
  }
  return 'badge-default';
}

async function loadAll() {
  const [projRes, usageRes] = await Promise.all([
    fetch('/api/projects'),
    fetch('/api/usage'),
  ]);
  const projects = await projRes.json();
  const usage = await usageRes.json();

  // Global stats
  const t = usage.totals || {};
  document.getElementById('s-projects').textContent = projects.length;
  document.getElementById('s-calls').textContent = fmtNum(t.calls || 0);
  const totalTokens = (t.prompt_tokens || 0) + (t.completion_tokens || 0);
  document.getElementById('s-tokens').textContent = fmtNum(totalTokens);
  document.getElementById('s-tokens-detail').textContent =
    fmtNum(t.prompt_tokens || 0) + ' in / ' + fmtNum(t.completion_tokens || 0) + ' out';
  document.getElementById('s-cost').textContent = '$' + (t.cost_usd || 0).toFixed(2);

  // Project cards
  const el = document.getElementById('projects');
  if (!projects.length) {
    el.innerHTML = '<div class="no-projects">No hay proyectos todavia</div>';
    return;
  }

  el.innerHTML = projects.map(p => {
    const slug = p.slug || '';
    const u = (usage.projects || {})[slug] || {};
    const ptk = u.total_prompt_tokens || 0;
    const ctk = u.total_completion_tokens || 0;
    const calls = u.calls || 0;
    const cost = u.total_cost_usd || 0;

    let urlHtml = '';
    if (p.url) {
      urlHtml = '<a class="url-link" href="' + escAttr(p.url) + '" target="_blank" rel="noopener">' + escHtml(p.url) + ' &#8599;</a>';
    }

    let usageHtml = '';
    if (calls > 0) {
      usageHtml = '<div class="usage-row">' +
        '<span class="usage-item">' + fmtNum(calls) + ' calls</span>' +
        '<span class="usage-item">' + fmtNum(ptk + ctk) + ' tokens</span>' +
        '<span class="usage-item">$' + cost.toFixed(2) + '</span>' +
        '</div>';
    }

    const created = p.created_at ? new Date(p.created_at).toLocaleDateString('es-AR') : '';

    return '<div class="project-card">' +
      '<div class="top">' +
        '<span class="name">' + escHtml(p.name) + '</span>' +
        '<span class="badge ' + badgeClass(p.state) + '">' + escHtml(p.state) + '</span>' +
      '</div>' +
      '<div class="meta">' +
        (created ? '<span>' + created + '</span>' : '') +
        (p.port ? '<span>:' + p.port + '</span>' : '') +
      '</div>' +
      urlHtml +
      usageHtml +
    '</div>';
  }).join('');

  document.getElementById('last-refresh').textContent = 'Actualizado: ' + new Date().toLocaleTimeString('es-AR');
}

async function loadPrompts() {
  const res = await fetch('/api/prompts');
  const data = await res.json();

  const tabs = document.getElementById('tabs');
  const agents = document.getElementById('agents');
  tabs.innerHTML = '';
  agents.innerHTML = '';

  for (const [agent, label] of Object.entries(AGENTS)) {
    const tab = document.createElement('div');
    tab.className = 'tab' + (agent === currentAgent ? ' active' : '');
    tab.textContent = label;
    tab.onclick = () => switchAgent(agent);
    tabs.appendChild(tab);

    const section = document.createElement('div');
    section.className = 'agent-section' + (agent === currentAgent ? ' active' : '');
    section.id = 'section-' + agent;

    const prompts = data[agent] || {};
    for (const [pname, info] of Object.entries(prompts)) {
      const ta_id = 'p-' + agent + '-' + pname;
      const sv_id = 'saved-' + agent + '-' + pname;
      section.innerHTML += '<div class="prompt-section">' +
        '<div class="prompt-label">' + escHtml(info.label) + ' (' + pname + ')</div>' +
        '<textarea id="' + ta_id + '">' + escHtml(info.value) + '</textarea>' +
        '<button class="btn" data-agent="' + agent + '" data-prompt="' + pname + '" onclick="savePrompt(this.dataset.agent, this.dataset.prompt)">Guardar</button>' +
        '<span class="saved" id="' + sv_id + '">Guardado</span>' +
      '</div>';
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
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escAttr(s) {
  return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

loadAll();
loadPrompts();
// Auto-refresh projects + usage every 30s
setInterval(loadAll, 30000);
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
