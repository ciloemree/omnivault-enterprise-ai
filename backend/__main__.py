"""Allow ``python -m backend`` as a thin alias for the ASGI app launcher."""

from __future__ import annotations

import uvicorn

from backend.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
