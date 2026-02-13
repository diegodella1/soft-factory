"""FactoryBot — Agentic Software Factory entry point."""

import logging
import sys
from bot.telegram.handlers import build_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("factorybot")


def main():
    log.info("Starting FactoryBot...")

    # Load custom prompt overrides
    from bot.dashboard import start_dashboard, _load_custom_prompts, AGENT_PROMPTS
    import importlib
    custom = _load_custom_prompts()
    for key, value in custom.items():
        agent, prompt_name = key.split(".", 1)
        info = AGENT_PROMPTS.get(agent)
        if info:
            mod = importlib.import_module(info["module"])
            setattr(mod, prompt_name, value)
            log.info("Loaded custom prompt: %s", key)

    # Start dashboard
    start_dashboard()

    # Start Telegram bot
    app = build_app()
    log.info("Bot is running. Polling for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
