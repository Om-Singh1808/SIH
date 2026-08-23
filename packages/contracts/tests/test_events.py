"""Event envelope, payload discriminated union, Observation/Event validators."""

import json

import pytest
from pydantic import ValidationError

from retailsense_contracts import VERSION
from retailsense_contracts.enums import EventClass
from retailsense_contracts.events import (
    EVENT_CLASS,
    EVENT_TYPES,
    PAYLOAD_CLASSES,
    Event,
    FootfallCrossing,
    Observation,
    ShelfScan,
    ZoneOccupancy,
    make_event,
)
from retailsense_contracts.testing import sample_event, sample_events_all, sample_observation, sample_payload


def test_version():
    assert VERSION == "1.0.0"


def test_event_types_table_complete():
    assert set(EVENT_TYPES) == set(EVENT_CLASS) == set(PAYLOAD_CLASSES)
    assert len(EVENT_TYPES) == 16
    for t, cls in PAYLOAD_CLASSES.items():
        assert cls.model_fields["type"].default == t


@pytest.mark.parametrize("type_", EVENT_TYPES)
def test_events_roundtrip(type_):
    ev = sample_event(type_)
    assert ev.type == type_ and ev.cls == EVENT_CLASS[type_]
    raw = ev.model_dump_json()
    back = Event.model_validate_json(raw)
    assert back == ev
    assert type(back.payload) is PAYLOAD_CLASSES[type_]
    # dict round trip too (what SQLite JSON columns hold)
    again = Event.model_validate(json.loads(raw))
    assert again == ev


@pytest.mark.parametrize("type_", EVENT_TYPES)
def test_observation_roundtrip_and_cls(type_):
    obs = sample_observation(type_)
    back = Observation.model_validate_json(obs.model_dump_json())
    assert back == obs
    assert obs.cls == EVENT_CLASS[type_]


def test_event_type_mismatch_rejected():
    with pytest.raises(ValidationError):
        Observation(
            type="zone.occupancy", ts=1.0, payload=FootfallCrossing(line_id="x", line_kind="entrance", direction="in")
        )
    ev = sample_event("footfall.crossing")
    with pytest.raises(ValidationError):
        Event.model_validate({**ev.model_dump(mode="json"), "type": "zone.occupancy"})
    with pytest.raises(ValidationError):
        Event.model_validate({**ev.model_dump(mode="json"), "cls": "telemetry"})


def test_unknown_payload_type_rejected():
    with pytest.raises(ValidationError):
        Observation.model_validate({"type": "bogus.type", "ts": 1.0, "payload": {"type": "bogus.type"}})


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        ZoneOccupancy(zone_id="a", zone_kind="aisle", count=1, window_s=1.0, extra=1)


def test_thumb_size_limit():
    ShelfScan(
        shelf_id="s",
        sku_id=None,
        coverage=0.5,
        facings=1,
        capacity_facings=2,
        state_raw="partial",
        thumb_b64="x" * 16384,
    )
    with pytest.raises(ValidationError):
        ShelfScan(
            shelf_id="s",
            sku_id=None,
            coverage=0.5,
            facings=1,
            capacity_facings=2,
            state_raw="partial",
            thumb_b64="x" * 16385,
        )


def test_make_event_stamps(cfg):
    obs = sample_observation("queue.snapshot")
    ev = make_event(obs, store_id="S", device_id="D", seq=7, hlc="0000000000001-0000-D", created_ts=5.0)
    assert (ev.store_id, ev.device_id, ev.seq, ev.hlc, ev.created_ts) == ("S", "D", 7, "0000000000001-0000-D", 5.0)
    assert ev.cls == EventClass.AGGREGATE and len(ev.event_id) == 26
    assert ev.to_observation() == obs


def test_sample_events_all_seq_contiguous():
    evs = sample_events_all()
    assert [e.seq for e in evs] == list(range(1, len(EVENT_TYPES) + 1))
    assert len({e.event_id for e in evs}) == len(evs)


def test_observation_of_infers_type():
    p = sample_payload("dwell.sample")
    obs = Observation.of(p, ts=10.0, camera_id="cam")
    assert obs.type == "dwell.sample" and obs.camera_id == "cam"
