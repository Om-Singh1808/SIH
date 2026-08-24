"""``python -m senseedge`` main runner."""

from __future__ import annotations

import argparse
import sys
import uvicorn

from senseedge.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SenseEdge per-store server")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--config", default=None)
    parser.add_argument("--cloud", default="http://localhost:8000")
    parser.add_argument("--detector", default="auto")
    parser.add_argument("--clock", type=float, default=10.0)
    parser.add_argument("--uplink", default="http")
    args, _ = parser.parse_known_args(argv)

    app = create_app(args.config, clock_factor=args.clock)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
