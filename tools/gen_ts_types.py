#!/usr/bin/env python
"""pydantic models -> JSON Schema -> TypeScript mirror for SenseBoard.

    python tools/gen_ts_types.py            # regenerate schemas/*.json + ts/types.gen.ts
    python tools/gen_ts_types.py --check    # exit 1 if the committed output is stale (CI)
    python tools/gen_ts_types.py --engine builtin|npx|auto

Outputs (all committed):
  packages/contracts/retailsense_contracts/schemas/<Model>.json   one JSON Schema per exported model
  packages/contracts/retailsense_contracts/schemas/_all.json      one combined schema ($defs) used for TS
  packages/contracts/ts/types.gen.ts                               TypeScript interfaces/types

Engine: ``npx --yes json-schema-to-typescript@15`` when available (pinned major so
output is reproducible); otherwise a small built-in JSON-Schema->TS emitter so the
file is *always* generated (deterministic output, sorted by name).  The header
line of types.gen.ts records which engine produced it; ``--check`` re-runs that
same engine and skips gracefully if it is unavailable.

Schema post-processing (so the TS is what the dashboard actually receives):
* every model field is ``required`` - pydantic always serialises defaults;
* ``prefixItems`` tuples are rewritten to draft-7 ``items: [...]`` (tuple types);
* the 16-value event-type enum becomes a named ``EventType``; the WS kind enum ``WsKind``;
* the discriminated payload union becomes a named ``Payload``; alert details ``AlertDetails``.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "packages" / "contracts"
SCHEMAS_DIR = PKG_DIR / "retailsense_contracts" / "schemas"
TS_OUT = PKG_DIR / "ts" / "types.gen.ts"
NPX_PACKAGE = "json-schema-to-typescript@15"

sys.path.insert(0, str(PKG_DIR))

import retailsense_contracts as rc
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema
from retailsense_contracts import (
    alerts,
    api,
    config,
    events,
    impact,
    manifest,
    privacy,
    ws,
)
from retailsense_contracts.events import EVENT_TYPES
from retailsense_contracts.ws import WS_KINDS

MODEL_MODULES = (events, alerts, impact, config, api, ws, manifest, privacy)


# ---------------------------------------------------------------------------
# collect models
# ---------------------------------------------------------------------------


def exported_models() -> list[type[BaseModel]]:
    seen: dict[str, type[BaseModel]] = {}
    for mod in MODEL_MODULES:
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == mod.__name__
            ):
                seen[name] = obj
    return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------------------
# schema post-processing
# ---------------------------------------------------------------------------


def _walk(node: Any, fn) -> Any:
    if isinstance(node, dict):
        node = fn(node)
        return {k: _walk(v, fn) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(v, fn) for v in node]
    return node


def _fix_node(node: dict) -> dict:
    # pydantic always serialises every field -> mark all properties required
    if (
        node.get("type") == "object"
        and "properties" in node
        and node.get("additionalProperties") is False
    ):
        node = dict(node)
        node["required"] = list(node["properties"].keys())
    # prefixItems (2020-12) -> items tuple form (draft-7) understood by json-schema-to-typescript
    if "prefixItems" in node:
        node = dict(node)
        node["items"] = node.pop("prefixItems")
    # named enums for the big Literal unions
    if "enum" in node and "title" not in node.get("$ref", ""):
        vals = node["enum"]
        if list(vals) == list(EVENT_TYPES):
            return {"$ref": "#/$defs/EventType"}
        if list(vals) == list(WS_KINDS):
            return {"$ref": "#/$defs/WsKind"}
    # named discriminated union for payloads
    if "discriminator" in node and node["discriminator"].get("propertyName") == "type":
        return {"$ref": "#/$defs/Payload"}
    return node


def _named_union(title: str, refs: list[str], discriminator: str | None = None) -> dict:
    out: dict[str, Any] = {"title": title, "oneOf": [{"$ref": r} for r in refs]}
    if discriminator:
        out["discriminator"] = {"propertyName": discriminator}
    return out


def combined_schema(models: list[type[BaseModel]]) -> dict:
    _, top = models_json_schema(
        [(m, "serialization") for m in models],
        ref_template="#/$defs/{model}",
        title="RetailSenseContracts",
    )
    defs: dict[str, Any] = dict(top["$defs"])
    payload_refs = [
        f"#/$defs/{events.PAYLOAD_CLASSES[t].__name__}" for t in EVENT_TYPES
    ]
    defs["EventType"] = {
        "title": "EventType",
        "type": "string",
        "enum": list(EVENT_TYPES),
    }
    defs["WsKind"] = {"title": "WsKind", "type": "string", "enum": list(WS_KINDS)}
    defs["Payload"] = _named_union("Payload", payload_refs, "type")
    # AlertDetails: the anyOf inside Alert.details, named
    alert_details = defs["Alert"]["properties"]["details"]
    if "anyOf" in alert_details:
        defs["AlertDetails"] = {
            "title": "AlertDetails",
            "oneOf": alert_details["anyOf"],
        }
        defs["Alert"]["properties"]["details"] = {"$ref": "#/$defs/AlertDetails"}
    defs = {
        k: _walk(v, _fix_node)
        if k not in ("Payload", "EventType", "WsKind", "AlertDetails")
        else v
        for k, v in defs.items()
    }
    # clean noisy auto-titles on properties ("Store Id") so TS docs stay quiet
    defs = _walk(defs, _strip_prop_titles)
    ordered = {k: defs[k] for k in sorted(defs)}
    root = {
        "$schema": "https://json-schema.org/draft-07/schema#",
        "title": "RetailSenseContracts",
        "description": f"Index of every RetailSense contract model (contracts v{rc.VERSION}).",
        "type": "object",
        "properties": {k: {"$ref": f"#/$defs/{k}"} for k in ordered},
        "additionalProperties": False,
        "$defs": ordered,
    }
    return root


def _strip_prop_titles(node: dict) -> dict:
    if "properties" in node and isinstance(node["properties"], dict):
        node = dict(node)
        props = {}
        for k, v in node["properties"].items():
            if (
                isinstance(v, dict)
                and "title" in v
                and v.get("title", "").replace(" ", "_").lower() == k.lower()
            ):
                v = {kk: vv for kk, vv in v.items() if kk != "title"}
            props[k] = v
        node["properties"] = props
    return node


def per_model_schema(model: type[BaseModel]) -> dict:
    s = model.model_json_schema(mode="serialization", ref_template="#/$defs/{model}")
    s = _walk(s, _fix_node)
    s = _walk(s, _strip_prop_titles)
    defs = s.get("$defs", {})
    if any(v == {"$ref": "#/$defs/Payload"} for v in _flatten(s)):
        defs["Payload"] = _named_union(
            "Payload",
            [f"#/$defs/{events.PAYLOAD_CLASSES[t].__name__}" for t in EVENT_TYPES],
            "type",
        )
    if any(v == {"$ref": "#/$defs/EventType"} for v in _flatten(s)):
        defs["EventType"] = {
            "title": "EventType",
            "type": "string",
            "enum": list(EVENT_TYPES),
        }
    if any(v == {"$ref": "#/$defs/WsKind"} for v in _flatten(s)):
        defs["WsKind"] = {"title": "WsKind", "type": "string", "enum": list(WS_KINDS)}
    if defs:
        s["$defs"] = {k: defs[k] for k in sorted(defs)}
    s["$schema"] = "https://json-schema.org/draft-07/schema#"
    return s


def _flatten(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _flatten(v)
    elif isinstance(node, list):
        for v in node:
            yield from _flatten(v)


def dump_json(obj: Any) -> str:
    # sort_keys=False on purpose: pydantic's property order is the model's field order,
    # which is what we want to see in the TypeScript interfaces ($defs are sorted by us).
    return json.dumps(obj, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# built-in JSON Schema -> TS emitter (fallback; deterministic)
# ---------------------------------------------------------------------------


class BuiltinEmitter:
    def __init__(self, schema: dict):
        self.defs: dict[str, Any] = schema["$defs"]

    @staticmethod
    def _ref_name(ref: str) -> str:
        return ref.rsplit("/", 1)[-1]

    def ts_type(self, node: dict, indent: int = 1) -> str:
        if "$ref" in node:
            return self._ref_name(node["$ref"])
        if "const" in node:
            return json.dumps(node["const"], ensure_ascii=False)
        if "enum" in node:
            return " | ".join(json.dumps(v, ensure_ascii=False) for v in node["enum"])
        for key in ("anyOf", "oneOf"):
            if key in node:
                parts = [self.ts_type(n, indent) for n in node[key]]
                # dedupe while keeping order
                seen: list[str] = []
                for p in parts:
                    if p not in seen:
                        seen.append(p)
                return " | ".join(seen)
        if "allOf" in node and len(node["allOf"]) == 1:
            return self.ts_type(node["allOf"][0], indent)
        t = node.get("type")
        if isinstance(t, list):
            return " | ".join(self.ts_type({**node, "type": tt}, indent) for tt in t)
        if t == "string":
            return "string"
        if t in ("number", "integer"):
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "null":
            return "null"
        if t == "array":
            items = node.get("items")
            if isinstance(items, list):
                return "[" + ", ".join(self.ts_type(i, indent) for i in items) + "]"
            if isinstance(items, dict):
                inner = self.ts_type(items, indent)
                return f"({inner})[]" if " | " in inner else f"{inner}[]"
            return "unknown[]"
        if t == "object" or "properties" in node or "additionalProperties" in node:
            if "properties" in node:
                return self.object_literal(node, indent)
            ap = node.get("additionalProperties")
            if isinstance(ap, dict):
                return "{ [k: string]: " + self.ts_type(ap, indent) + " }"
            return "{ [k: string]: unknown }"
        return "unknown"

    def object_literal(self, node: dict, indent: int) -> str:
        pad = "  " * indent
        req = set(node.get("required", []))
        lines = ["{"]
        for name, prop in node["properties"].items():
            opt = "" if name in req else "?"
            desc = prop.get("description")
            if desc:
                lines.append(f"{pad}/** {desc} */")
            lines.append(f"{pad}{name}{opt}: {self.ts_type(prop, indent + 1)};")
        lines.append("  " * (indent - 1) + "}")
        return "\n".join(lines)

    def emit(self) -> str:
        out: list[str] = []
        for name in sorted(self.defs):
            node = self.defs[name]
            desc = node.get("description")
            if desc:
                out.append("/**\n * " + desc.replace("\n", "\n * ") + "\n */")
            if node.get("type") == "object" and "properties" in node:
                out.append(f"export interface {name} {self.object_literal(node, 1)}")
            else:
                out.append(f"export type {name} = {self.ts_type(node)};")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# engines
# ---------------------------------------------------------------------------


def _npx() -> str | None:
    return shutil.which("npx") or shutil.which("npx.cmd")


def emit_with_npx(schema: dict) -> str | None:
    npx = _npx()
    if npx is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "_all.json"
        src.write_text(dump_json(schema), encoding="utf-8")
        try:
            proc = subprocess.run(
                [npx, "--yes", NPX_PACKAGE, str(src), "--bannerComment", ""],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                timeout=300,
                shell=os.name == "nt",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
            print(f"[gen_ts_types] npx failed: {exc}", file=sys.stderr)
            return None
    if proc.returncode != 0 or not proc.stdout.strip():
        print(
            f"[gen_ts_types] npx failed (rc={proc.returncode}): {proc.stderr[-2000:]}",
            file=sys.stderr,
        )
        return None
    body = proc.stdout.replace("\r\n", "\n").strip() + "\n"
    return body


def emit_builtin(schema: dict) -> str:
    return BuiltinEmitter(schema).emit()


def header(engine: str, schema_hash: str) -> str:
    return (
        "/* eslint-disable */\n"
        f"// GENERATED FILE - do not edit. engine={engine} contracts={rc.VERSION} schema={schema_hash}\n"
        "// Regenerate: python tools/gen_ts_types.py   (source: packages/contracts/retailsense_contracts/*.py)\n\n"
        f'export const CONTRACTS_VERSION = "{rc.VERSION}";\n\n'
    )


def generate_ts(schema: dict, engine: str) -> tuple[str, str]:
    """Return (ts_text, engine_used)."""
    schema_hash = hashlib.sha256(dump_json(schema).encode("utf-8")).hexdigest()[:12]
    body: str | None = None
    used = engine
    if engine in ("auto", "npx"):
        body = emit_with_npx(schema)
        used = "npx"
        if body is None and engine == "npx":
            raise SystemExit(
                "npx json-schema-to-typescript unavailable and --engine npx requested"
            )
    if body is None:
        body = emit_builtin(schema)
        used = "builtin"
    return header(used, schema_hash) + body, used


def committed_engine(path: Path = TS_OUT) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines()[:3]:
        if "engine=" in line:
            return line.split("engine=", 1)[1].split()[0]
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def build(engine: str = "auto") -> dict[str, str]:
    """Compute all outputs without writing. Returns {relative_path: content}."""
    models = exported_models()
    out: dict[str, str] = {}
    for m in models:
        out[f"schemas/{m.__name__}.json"] = dump_json(per_model_schema(m))
    schema = combined_schema(models)
    out["schemas/_all.json"] = dump_json(schema)
    ts, _used = generate_ts(schema, engine)
    out["ts/types.gen.ts"] = ts
    return out


def _paths(rel: str) -> Path:
    if rel.startswith("schemas/"):
        return SCHEMAS_DIR / rel.split("/", 1)[1]
    return PKG_DIR / rel


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="verify committed outputs are up to date (exit 1 on drift)",
    )
    ap.add_argument(
        "--engine",
        choices=["auto", "npx", "builtin"],
        default=None,
        help="TS emitter (default: auto, or the committed engine under --check)",
    )
    args = ap.parse_args(argv)

    engine = args.engine
    if engine is None:
        engine = (committed_engine() if args.check else None) or "auto"
    if args.check and engine == "npx" and _npx() is None:
        print(
            "[gen_ts_types] --check: committed file was produced by npx, which is not available here; skipping TS check",
            file=sys.stderr,
        )
        engine = "skip-ts"

    outputs = build("builtin" if engine == "skip-ts" else engine)
    if engine == "skip-ts":
        outputs.pop("ts/types.gen.ts")

    if args.check:
        stale = []
        for rel, content in outputs.items():
            p = _paths(rel)
            if (
                not p.exists()
                or p.read_text(encoding="utf-8").replace("\r\n", "\n") != content
            ):
                stale.append(rel)
        existing = {f"schemas/{p.name}" for p in SCHEMAS_DIR.glob("*.json")}
        extra = sorted(existing - set(outputs))
        if stale or extra:
            print(
                "[gen_ts_types] STALE:",
                *stale,
                *[f"(extra) {e}" for e in extra],
                sep="\n  ",
            )
            return 1
        print(f"[gen_ts_types] up to date ({len(outputs)} files)")
        return 0

    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    TS_OUT.parent.mkdir(parents=True, exist_ok=True)
    for old in SCHEMAS_DIR.glob("*.json"):
        if f"schemas/{old.name}" not in outputs:
            old.unlink()
    for rel, content in outputs.items():
        p = _paths(rel)
        p.write_text(content, encoding="utf-8", newline="\n")
    used = committed_engine()
    print(
        f"[gen_ts_types] wrote {len(outputs)} files (engine={used}) -> {TS_OUT.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
