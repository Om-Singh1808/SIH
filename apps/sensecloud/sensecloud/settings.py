"""Runtime settings for SenseCloud.

Values come from the ``SENSECLOUD_*`` environment variables whose names and
defaults are owned by ``retailsense_contracts.settings`` (so ``tools/demo.py``
and docker-compose can never disagree with us).  Tests construct
``CloudSettings`` directly and inject a ``FrozenClock`` so "60 s without a
heartbeat" is deterministic.
"""

from dataclasses import dataclass, field

from retailsense_contracts import settings as env
from retailsense_contracts.clock import Clock, SystemClock


@dataclass
class CloudSettings:
    db_url: str = "sqlite:///var/sensecloud.db"
    dev: bool = False  # accept any device token (demo only)
    seed_history: bool = False  # seed 30 days of KPIs at boot
    seed_demo_store: bool = True  # register examples/store_demo.yaml at boot
    notifier: str = "simulator"  # simulator | telegram | cloud_api
    mqtt_host: str = ""  # empty = bridge disabled
    mqtt_port: int = 1883
    port: int = 8000
    history_days: int = 30
    series_seed_days: int = 7  # how many of the seeded days also get 5-minute series rows
    device_offline_s: float = 60.0
    background_tasks: bool = True  # periodic monitors; tests drive the same functions by hand
    fit_forecasters: bool = True  # fit cloud forecasters at boot (skipped automatically in tests when False)
    clock: Clock = field(default_factory=SystemClock)

    @classmethod
    def from_env(cls) -> "CloudSettings":
        return cls(
            db_url=env.get("SENSECLOUD_DB_URL"),
            dev=env.get_bool("SENSECLOUD_DEV"),
            seed_history=env.get_bool("SENSECLOUD_SEED_HISTORY"),
            notifier=env.get("SENSECLOUD_NOTIFIER") or "simulator",
            mqtt_host=env.get("SENSECLOUD_MQTT_HOST"),
            port=env.get_int("SENSECLOUD_PORT"),
        )

    def now(self) -> float:
        return self.clock.now()


__all__ = ["CloudSettings"]
