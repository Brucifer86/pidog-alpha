import sys
from textwrap import dedent


def main():
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
    uvicorn.run(
        "pidog_alpha.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
