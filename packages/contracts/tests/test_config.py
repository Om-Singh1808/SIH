"""StoreConfig validation, referential integrity, hashing and YAML round trip."""

import copy

import pytest
import yaml
from pydantic import ValidationError

from retailsense_contracts.config import StoreConfig, dump_store_config, load_store_config
from retailsense_contracts.enums import Anchor, DetectorKind, Lang
from retailsense_contracts.testing import example_path, sample_store_config


def test_config_demo_validates_and_hash_stable(tmp_path):
    a = load_store_config(example_path("store_demo.yaml"))
    b = sample_store_config()
    assert a == b
    assert a.config_hash() == b.config_hash()
    assert len(a.config_hash()) == 16
    # normative demo numbers
    assert a.store.store_id == "STR-DL-001" and a.device.device_id == "EDGE-001"
    assert a.store.lang is Lang.HI and a.store.open_hours == ("08:00", "22:00")
    assert a.cameras[0].anchor is Anchor.CENTER and a.cameras[0].detector is DetectorKind.AUTO
    assert a.cameras[0].is_synthetic and a.cameras[0].scenario == "baseline"
    assert [z.zone_id for z in a.zones] == ["store", "aisle-1", "aisle-2", "queue-1"]
    assert a.line("entrance").start == [120, 315] and a.line("entrance").end == [60, 315]
    assert a.counter("counter-1").queue_zone_id == "queue-1"
    assert a.sku("AMUL-TAAZA-500").velocity_units_per_hr == 18 and a.sku("AMUL-TAAZA-500").mrp_inr == 27
    assert a.sku("nope") is None and a.sku(None) is None
    assert a.impact.lost_sale_factor == 0.31 and a.rules.persistence_scans == 3
    assert a.integrations.ondc.bpp_id == "ramesh-store.ondc.demo"
    assert a.demo.clock_factor == 10
    # dump -> load -> same hash
    out = tmp_path / "store.yaml"
    dump_store_config(a, out)
    c = load_store_config(out)
    assert c == a and c.config_hash() == a.config_hash()
    # hash changes when something changes
    d = a.model_copy(update={"config_version": 2})
    assert d.config_hash() != a.config_hash()


def _raw() -> dict:
    with open(example_path("store_demo.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda r: r["zones"][0].__setitem__("camera_id", "cam-x"), "unknown camera"),
        (lambda r: r["lines"][0].__setitem__("camera_id", "cam-x"), "unknown camera"),
        (lambda r: r["shelves"][0].__setitem__("camera_id", "cam-x"), "unknown camera"),
        (lambda r: r["counters"][0].__setitem__("queue_zone_id", "queue-9"), "unknown zone"),
        (lambda r: r["counters"][0].__setitem__("counter_line_id", "nope"), "unknown line"),
        (lambda r: r["shelves"][0].__setitem__("sku_id", "NOPE"), "unknown sku"),
        (lambda r: r["zones"].append(dict(r["zones"][1])), "duplicate zone_id"),
        (lambda r: r["skus"].append(dict(r["skus"][0])), "duplicate sku_id"),
        (lambda r: r["zones"][0].__setitem__("polygon", [[0, 0], [1, 1]]), "polygon needs >= 3"),
        (lambda r: r["lines"][0].__setitem__("end", r["lines"][0]["start"]), "must differ"),
        (lambda r: r.__setitem__("cameras", []), "at least one camera"),
        (lambda r: r["store"].__setitem__("bogus", 1), "extra"),
    ],
)
def test_config_rejects_dangling_ids(mutate, msg):
    raw = copy.deepcopy(_raw())
    mutate(raw)
    with pytest.raises(ValidationError) as ei:
        StoreConfig.model_validate(raw)
    assert msg.lower() in str(ei.value).lower()


def test_camera_lookup_and_filters(cfg):
    assert cfg.camera("cam-synth").width == 640
    with pytest.raises(KeyError):
        cfg.camera("nope")
    assert (
        len(cfg.zones_for("cam-synth")) == 4
        and len(cfg.lines_for("cam-synth")) == 2
        and len(cfg.shelves_for("cam-synth")) == 3
    )
    assert cfg.synthetic_camera is cfg.cameras[0]


def test_homography_validation():
    from retailsense_contracts.config import HomographyConfig

    with pytest.raises(ValidationError):
        HomographyConfig(image_points=[[0, 0], [1, 0], [1, 1]], floor_points=[[0, 0], [1, 0], [1, 1]])
    h = HomographyConfig(image_points=[[0, 0], [1, 0], [1, 1], [0, 1]], floor_points=[[0, 0], [2, 0], [2, 2], [0, 2]])
    assert len(h.image_points) == 4
