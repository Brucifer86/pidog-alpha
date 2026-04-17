import logging
import os
import sys
from textwrap import dedent


def configure_logging() -> str:
    log_level = os.getenv("PIDOG_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    return logging.getLevelName(level).lower()


def main():
    uvicorn_log_level = configure_logging()

    try:
        import uvicorn
        from pidog_alpha.app import get_settings
    except ModuleNotFoundError as exc:
        if exc.name not in {"fastapi", "multipart", "pydantic", "uvicorn"}:
            raise

        message = f"""
        Missing Python dependency: {exc.name}

        Install this service into a virtual environment, then run it with that
        environment's Python:

            cd ~/pidog-alpha
            python3 -m venv .venv
            . .venv/bin/activate
            python -m pip install -U pip
            python -m pip install .

            PIDOG_API_MODE=real PIDOG_API_HOST=0.0.0.0 PIDOG_API_PORT=8000 \\
              .venv/bin/python main.py
        """
        print(dedent(message).strip(), file=sys.stderr)
        return 1

    settings = get_settings()
    logging.getLogger(__name__).info(
        "Starting PiDog API mode=%s host=%s port=%s log_level=%s",
        settings.mode,
        settings.host,
        settings.port,
        uvicorn_log_level,
    )
    uvicorn.run(
        "pidog_alpha.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=uvicorn_log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
