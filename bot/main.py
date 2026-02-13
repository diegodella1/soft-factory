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
    app = build_app()
    log.info("Bot is running. Polling for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
