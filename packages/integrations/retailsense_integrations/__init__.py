"""RetailSense integrations package.

Everything that talks to the *outside* of RetailSense lives here:

* ``tally`` / ``tally_xml`` / ``tally_mock`` - Tally ERP over its native XML
  HTTP interface (port 9000), plus a faithful mock server for demos and tests.
* ``reconcile`` - visual shelf count (camera) vs ERP stock -> shrink report.
* ``whatsapp`` / ``telegram`` - :class:`retailsense_contracts.interfaces.Notifier`
  implementations: an offline simulator (what the stage demo uses), the Meta
  WhatsApp Cloud API and Telegram Bot API.
* ``ondc`` - Beckn ``on_update`` availability publisher for ONDC.
* ``routers`` - the FastAPI router SenseCloud mounts for ``/v1/whatsapp/*`` and
  ``/v1/stores/{id}/integrations/*``.

Design rule: this package depends **only** on ``retailsense_contracts``; every
network call goes through ``httpx`` so tests can swap in ``MockTransport``.
"""

from .ondc import OndcStubPublisher
from .reconcile import reconcile
from .tally import TallyClient
from .telegram import TelegramNotifier, parse_callback_data
from .whatsapp import WhatsAppCloudNotifier, WhatsAppSimulator

__all__ = [
    "OndcStubPublisher",
    "TallyClient",
    "TelegramNotifier",
    "WhatsAppCloudNotifier",
    "WhatsAppSimulator",
    "parse_callback_data",
    "reconcile",
]
