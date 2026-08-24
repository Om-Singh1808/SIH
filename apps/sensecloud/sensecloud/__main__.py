"""``python -m sensecloud`` entry point."""

from __future__ import annotations

import argparse
import sys
import uvicorn

from .app import create_app
from .settings import CloudSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SenseCloud server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    settings = CloudSettings(port=args.port)
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
