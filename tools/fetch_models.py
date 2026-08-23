"""Export the person-detection weights to ONNX and record them in ``models/manifest.json``.

Usage::

    python tools/fetch_models.py                 # yolo11n.pt -> models/yolo11n.onnx (opset 12, imgsz 640)
    python tools/fetch_models.py --yolox         # (P2) download yolox_nano.onnx, Apache-2.0 licence-clean alternative
    python tools/fetch_models.py --manifest-only # only refresh sha256/size for files already present

``ultralytics`` (and torch) are heavy; they are imported *inside* ``export_yolo``
so this file can be imported by tests and the CLI without pulling them in.
If the package is missing we ``pip install ultralytics`` on demand (needs
internet once).  Nothing here runs at import time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MANIFEST = MODELS_DIR / "manifest.json"

YOLO_WEIGHTS = "yolo11n.pt"
YOLO_ONNX = "yolo11n.onnx"
YOLOX_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
YOLOX_ONNX = "yolox_nano.onnx"
OPSET = 12
IMGSZ = 640


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_ultralytics() -> None:
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        print("ultralytics not installed - installing (one-off, needs internet)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "ultralytics"])


def export_yolo(out_dir: Path = MODELS_DIR, imgsz: int = IMGSZ, opset: int = OPSET) -> Path:
    """``yolo11n.pt`` -> ``out_dir/yolo11n.onnx`` via ultralytics (lazy import)."""
    _ensure_ultralytics()
    from ultralytics import YOLO  # type: ignore[import-not-found]

    out_dir.mkdir(parents=True, exist_ok=True)
    model = YOLO(YOLO_WEIGHTS)  # downloads the .pt on first use
    exported = Path(model.export(format="onnx", imgsz=imgsz, opset=opset, simplify=True, dynamic=False))
    target = out_dir / YOLO_ONNX
    if exported.resolve() != target.resolve():
        shutil.move(str(exported), str(target))
    return target


def download_yolox(out_dir: Path = MODELS_DIR) -> Path:
    """P2: fetch the Apache-2.0 YOLOX-nano ONNX as a licence-clean alternative (decode format 'yolox')."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / YOLOX_ONNX
    print(f"downloading {YOLOX_URL} -> {target}")
    urllib.request.urlretrieve(YOLOX_URL, target)  # noqa: S310 - fixed https URL
    return target


def update_manifest(manifest_path: Path = MANIFEST, *, models_dir: Path = MODELS_DIR) -> dict:
    """Fill ``sha256``/``size_bytes`` for every manifest entry whose file exists; bump ``generated_ts``."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = 0
    for entry in data.get("models", []):
        rel = entry.get("file") or ""
        if not rel:
            continue
        path = ROOT / rel if not Path(rel).is_absolute() else Path(rel)
        if not path.exists():
            path = models_dir / Path(rel).name
        if not path.exists():
            print(f"  - {entry['model_id']}: {rel} missing, left as placeholder")
            continue
        entry["sha256"] = sha256_of(path)
        entry["size_bytes"] = path.stat().st_size
        changed += 1
        print(f"  + {entry['model_id']}: {path.name} sha256={entry['sha256'][:12]}... size={entry['size_bytes']}")
    data["generated_ts"] = time.time()
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # validate against the frozen contract so a hand edit can never ship a broken manifest
    from retailsense_contracts.manifest import ModelManifest

    ModelManifest.model_validate(data)
    print(f"manifest updated ({changed} entries) -> {manifest_path}")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yolox", action="store_true", help="also download yolox_nano.onnx (Apache-2.0)")
    ap.add_argument("--manifest-only", action="store_true", help="skip export; just refresh sha256/size")
    ap.add_argument("--imgsz", type=int, default=IMGSZ)
    ap.add_argument("--opset", type=int, default=OPSET)
    ap.add_argument("--out", type=Path, default=MODELS_DIR)
    args = ap.parse_args(argv)

    if not args.manifest_only:
        target = export_yolo(args.out, args.imgsz, args.opset)
        print(f"exported -> {target}")
        if args.yolox:
            download_yolox(args.out)
    update_manifest(MANIFEST, models_dir=args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
