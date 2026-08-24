"""Convenient entry point for local, VM, and Cloud Run execution."""

import os


def run() -> None:
    if os.getenv("RUNTIME_MODE", "polling").strip().lower() == "webhook":
        from bot.webhook_runtime import run as run_webhook

        run_webhook()
        return
    from bot.runtime import run as run_polling

    run_polling()


if __name__ == "__main__":
    run()
