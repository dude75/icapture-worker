"""Run uvicorn for local development."""

from __future__ import annotations

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        workers=1,
        log_level=settings.LOG_LEVEL,
    )


if __name__ == "__main__":
    main()
