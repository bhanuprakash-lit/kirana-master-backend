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

Writes:
    counter/<version>.tflite
    counter/<version>.labels
    counter/manifest.json      ← written LAST, so clients never see a manifest
                                 pointing at a blob that isn't uploaded yet.
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
    ap.add_argument("--weights", required=True, help="path to the .tflite")
    ap.add_argument("--labels", help="optional path to the labels file")
    ap.add_argument(
        "--container", default=os.getenv("VISION_MODEL_CONTAINER", "vision-models")
    )
    args = ap.parse_args()

    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not conn:
        return _fail("Set AZURE_STORAGE_CONNECTION_STRING first.")
    if not os.path.exists(args.weights):
        return _fail(f"No such file: {args.weights}")

    with open(args.weights, "rb") as f:
        blob = f.read()
    digest = hashlib.sha256(blob).hexdigest()
    print(f"{args.weights}: {len(blob):,} bytes, sha256 {digest}")

    from azure.storage.blob import BlobServiceClient

    cc = BlobServiceClient.from_connection_string(conn).get_container_client(
        args.container
    )
    try:
        cc.create_container()
        print(f"created container {args.container}")
    except Exception:
        pass

    weights_name = f"{args.model}/{args.version}.tflite"
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
    manifest = {"version": args.version, "sha256": digest, "size": len(blob)}
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
