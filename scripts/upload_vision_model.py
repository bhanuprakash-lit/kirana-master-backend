"""Publish a vision model release to blob storage (PAI-15).

Run this once per model release, BEFORE removing the asset from the app bundle.
Until a manifest exists, the app keeps using its bundled copy, so the cutover is
safe in either order.

    set AZURE_STORAGE_CONNECTION_STRING=...
    python scripts/upload_vision_model.py \
        --model counter \
        --version 2026.07.1 \
        --weights ../FlutterProjects/kirana_ai/assets/models/counter_model.tflite \
        --labels  ../FlutterProjects/kirana_ai/assets/models/counter_labels.txt

    # iOS (CoreML) — a .mlpackage is a directory, so it is published zipped:
    python scripts/upload_vision_model.py \
        --model counter-ios --version 2026.07.1 --format coreml \
        --weights .../counter_model.mlpackage.zip --labels .../counter_labels.txt

Writes:
    <model>/<version><ext>     the weights (ext inferred from --weights)
    <model>/<version>.labels
    <model>/manifest.json      ← written LAST, so clients never see a manifest
                                 pointing at a blob that isn't uploaded yet.

Android and iOS are separate --model prefixes on purpose: different runtimes,
different formats, different checksums. See vision/model_storage.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="counter")
    ap.add_argument("--version", required=True, help="e.g. 2026.07.1")
    ap.add_argument("--weights", required=True, help="path to the .tflite / .mlpackage.zip")
    ap.add_argument("--labels", help="optional path to the labels file")
    ap.add_argument(
        "--format", default=None, choices=["tflite", "coreml"],
        help="runtime the artifact targets; inferred from the extension if omitted",
    )
    ap.add_argument(
        "--container", default=os.getenv("VISION_MODEL_CONTAINER", "vision-models")
    )
    args = ap.parse_args()

    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not conn:
        return _fail("Set AZURE_STORAGE_CONNECTION_STRING first.")
    if not os.path.exists(args.weights):
        return _fail(f"No such file: {args.weights}")

    # ".mlpackage.zip" is two suffixes — splitext would keep only ".zip" and the
    # app would then ask for a blob that doesn't exist.
    lower = args.weights.lower()
    if lower.endswith(".mlpackage.zip"):
        ext, fmt = ".mlpackage.zip", "coreml"
    elif lower.endswith(".tflite"):
        ext, fmt = ".tflite", "tflite"
    else:
        return _fail(f"Unsupported weights type: {args.weights}")
    fmt = args.format or fmt

    with open(args.weights, "rb") as f:
        blob = f.read()
    digest = hashlib.sha256(blob).hexdigest()
    print(f"{args.weights}: {len(blob):,} bytes, sha256 {digest} ({fmt})")

    from azure.storage.blob import BlobServiceClient

    cc = BlobServiceClient.from_connection_string(conn).get_container_client(
        args.container
    )
    try:
        cc.create_container()
        print(f"created container {args.container}")
    except Exception:
        pass

    weights_name = f"{args.model}/{args.version}{ext}"
    cc.upload_blob(name=weights_name, data=blob, overwrite=True)
    print(f"uploaded {weights_name}")

    if args.labels:
        with open(args.labels, "rb") as f:
            labels = f.read()
        labels_name = f"{args.model}/{args.version}.labels"
        cc.upload_blob(name=labels_name, data=labels, overwrite=True)
        print(f"uploaded {labels_name}")

    # Manifest last: it's what points clients at a version, so it must never
    # name a blob that hasn't finished uploading.
    manifest = {
        "version": args.version,
        "sha256": digest,
        "size": len(blob),
        "ext": ext,
        "format": fmt,
    }
    cc.upload_blob(
        name=f"{args.model}/manifest.json",
        data=json.dumps(manifest, indent=2).encode(),
        overwrite=True,
    )
    print(f"uploaded {args.model}/manifest.json -> {manifest}")
    print("\nDone. The app will pick this up on next launch.")
    return 0


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
