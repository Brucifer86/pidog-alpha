from pidog_alpha.app import get_settings


def main():
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "pidog_alpha.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
