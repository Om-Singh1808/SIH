"""SenseEdge - the per-store RetailSense edge process.

One Python process per store: camera worker threads feed a bounded queue, an
asyncio consumer turns frames into events (rules -> store -> websocket), and a
FastAPI app exposes the LAN-only REST/WS/MJPEG API consumed by SenseBoard.

Everything outside this package is reached through
``retailsense_contracts.registry.resolve`` so the app boots on contracts fakes
alone and picks up real packages the moment they are installed.
"""

from senseedge.app import create_app
from senseedge.wiring import Wiring

__version__ = "1.0.0"
__all__ = ["Wiring", "__version__", "create_app"]
