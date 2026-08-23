# retailsense-contracts

Frozen shared contracts for RetailSense (`VERSION = "1.0.0"`): pydantic v2 models for every
event, alert, config and REST/WS message; `typing.Protocol`s for every pluggable component;
deterministic fakes so each package can run its own tests with nothing else installed;
SQLAlchemy Core DDL for edge and cloud; Hindi/English alert templates; generated JSON Schemas
and a TypeScript mirror for SenseBoard.

```
pip install -e packages/contracts
python -m pytest packages/contracts -q
python tools/gen_ts_types.py          # regenerate schemas/*.json and ts/types.gen.ts
```

Apps resolve sibling packages **only** through `retailsense_contracts.registry.resolve(key)`,
which falls back to the fake in `retailsense_contracts.testing` (with one WARNING) when the real
implementation is not installed.
