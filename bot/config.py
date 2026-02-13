import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


# Telegram
TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_OWNER_ID = int(_require("TELEGRAM_OWNER_ID"))

# OpenAI
OPENAI_API_KEY = _require("OPENAI_API_KEY")
OPENAI_MODEL_HEAVY = os.getenv("OPENAI_MODEL_HEAVY", "gpt-4o")
OPENAI_MODEL_LIGHT = os.getenv("OPENAI_MODEL_LIGHT", "gpt-4o-mini")

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", str(BASE_DIR / "projects")))

# Coolify
COOLIFY_BASE_URL = os.getenv("COOLIFY_BASE_URL", "http://localhost:8000")
COOLIFY_SERVER_UUID = os.getenv("COOLIFY_SERVER_UUID", "b0oso8o40ogogo0wwwow0cw4")

# Network
LAN_IP = os.getenv("LAN_IP", "192.168.1.14")
PORT_RANGE_START = int(os.getenv("PORT_RANGE_START", "3100"))
PORT_RANGE_END = int(os.getenv("PORT_RANGE_END", "3199"))

# Agent limits
MAX_CONVERSATION_CONTEXT = 20  # messages sent to LLM
MAX_HISTORY_STORED = 50  # messages stored per project
MAX_DEV_RETRIES = 3  # retries per build step
