"""SenseCloud - the RetailSense multi-store cloud API.

Package layout (one concern per module, all glued together by ``app.create_app``):

* ``settings``   - ``CloudSettings`` (env-driven, injectable clock for tests)
* ``db``         - thin SQLAlchemy Core wrapper over ``retailsense_contracts.db.cloud_metadata``
* ``ingest``     - idempotent batch ingest with per-device seq-gap detection
* ``aggregator`` - series_5m / kpi_daily / view tables, incremental from ``agg_cursor``
* ``alerting``   - mirrors edge alerts + cloud-only rules (device_offline, shrink_suspect)
* ``dispatcher`` - alert -> WhatsApp-style OutboundMessage via the registry Notifier; webhook -> Command
* ``fleet``      - device registry, model manifests, canary rollout
* ``forecast``   - registry glue for the cloud forecasters + live MAE bookkeeping
* ``reports``    - DailyReport in json / csv / whatsapp text
* ``seed``       - demo store + 30 days of history so dashboards are never empty
* ``ws``         - per-store WebSocket fan-out
* ``mqtt_bridge``- optional paho subscriber feeding ``ingest``
* ``routers/*``  - the REST surface of spec section C.16
"""

from .app import create_app
from .settings import CloudSettings

__all__ = ["CloudSettings", "create_app"]
