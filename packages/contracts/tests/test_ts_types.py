"""Generated schemas + TypeScript mirror are committed and up to date (skips the TS part if npx is missing)."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parents[1]
GEN = ROOT / "tools" / "gen_ts_types.py"
SCHEMAS = PKG / "retailsense_contracts" / "schemas"
TS = PKG / "ts" / "types.gen.ts"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_ts_types", GEN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_ts_types"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_schemas_committed_and_valid():
    files = sorted(SCHEMAS.glob("*.json"))
    assert len(files) > 90, "schemas/*.json missing - run python tools/gen_ts_types.py"
    names = {f.stem for f in files}
    for must in (
        "Event",
        "Alert",
        "StoreConfig",
        "KpiToday",
        "IngestBatch",
        "IngestAck",
        "WsMessage",
        "ModelManifest",
        "SyncStatus",
        "_all",
    ):
        assert must in names
    ev = json.loads((SCHEMAS / "Event.json").read_text(encoding="utf-8"))
    assert ev["properties"]["type"] == {"$ref": "#/$defs/EventType"}
    assert ev["properties"]["payload"] == {"$ref": "#/$defs/Payload"}
    assert len(ev["$defs"]["EventType"]["enum"]) == 16
    assert set(ev["required"]) == set(ev["properties"])  # every field present on the wire
    allschema = json.loads((SCHEMAS / "_all.json").read_text(encoding="utf-8"))
    assert "Payload" in allschema["$defs"] and "AlertDetails" in allschema["$defs"]


def test_ts_files_present():
    assert TS.exists() and TS.stat().st_size > 20_000
    text = TS.read_text(encoding="utf-8")
    for needle in (
        "export interface Event",
        "export type Payload",
        "export type EventType",
        "export interface KpiToday",
        'CONTRACTS_VERSION = "1.0.0"',
        "open_hours: [string, string]",
    ):
        assert needle in text, needle
    index = (PKG / "ts" / "index.ts").read_text(encoding="utf-8")
    assert (
        'export * from "./types.gen"' in index
        and "export function fmtInr" in index
        and "export function isAlertKind" in index
    )


def test_ts_types_up_to_date():
    gen = _load_gen()
    engine = gen.committed_engine() or "builtin"
    if engine == "npx" and gen._npx() is None:
        pytest.skip("committed types.gen.ts was produced by npx json-schema-to-typescript, which is not installed here")
    outputs = gen.build(engine)
    stale = []
    for rel, content in outputs.items():
        p = gen._paths(rel)
        if not p.exists() or p.read_text(encoding="utf-8").replace("\r\n", "\n") != content:
            stale.append(rel)
    assert not stale, f"regenerate with python tools/gen_ts_types.py; stale: {stale}"
    extra = {f"schemas/{p.name}" for p in SCHEMAS.glob("*.json")} - set(outputs)
    assert not extra


def test_builtin_emitter_deterministic():
    gen = _load_gen()
    models = gen.exported_models()
    schema = gen.combined_schema(models)
    a = gen.emit_builtin(schema)
    b = gen.emit_builtin(schema)
    assert a == b and "export interface Event {" in a and "export type Payload = FootfallCrossing |" in a
