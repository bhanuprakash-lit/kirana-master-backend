"""Vision model delivery — Azure Blob (PAI-15).

The counter model used to ship inside the APK as a plain asset, so anyone could
rename the APK to .zip and walk off with 38 MB of trained weights. Encrypting
the asset would not have fixed that: the app has to decrypt at runtime with no
server involved, so the key ships in the same binary.

Instead the model is not in the APK at all. It lives in a private blob
container and is fetched once, by an authenticated user, over the Bearer-authed
endpoints in `vision/routes.py`. That means:
  * an attacker needs a valid account, not just the APK;
  * every fetch is attributable (see `vision_model_fetch`), so a leaked account
    can be identified and cut off;
  * the download shrinks by ~38 MB.

Container layout, one prefix per (model, runtime) pair:
    counter/manifest.json          {"version", "sha256", "size", "ext", "format"}
    counter/<version>.tflite       Android weights (TensorFlow Lite)
    counter/<version>.labels       label list (small, shipped alongside)
    counter-ios/manifest.json
    counter-ios/<version>.mlpackage.zip   iOS weights (CoreML)
    counter-ios/<version>.labels

Android and iOS are separate prefixes because the two runtimes take different
formats and nothing about them is shared — the `ultralytics_yolo` plugin is
TFLite on Android and CoreML-only on iOS, and a `.mlpackage` is a *directory*,
so it travels zipped and is extracted on the device.

The blob extension comes from the manifest's `ext`, defaulting to `.tflite` so
manifests published before iOS existed keep resolving.

Configure via env:
  AZURE_STORAGE_CONNECTION_STRING   (shared with consent storage)
  VISION_MODEL_CONTAINER            (default "vision-models")
"""
from __future__ import annotations

import json
import threading

from config import get_settings

_lock = threading.Lock()
_container_client = None  # type: ignore
_ready = False

# Manifests are tiny and change only on a model release — cache per process so
# every app launch doesn't cost a blob round-trip.
_manifest_cache: dict[str, dict] = {}


def is_configured() -> bool:
    s = get_settings()
    return bool(getattr(s, "azure_storage_connection_string", ""))


def _client():
    """Ready ContainerClient. Raises RuntimeError when unconfigured (→ 503)."""
    global _container_client, _ready
    if _ready and _container_client is not None:
        return _container_client
    with _lock:
        if _ready and _container_client is not None:
            return _container_client
        s = get_settings()
        if not s.azure_storage_connection_string:
            raise RuntimeError(
                "Azure Blob not configured (AZURE_STORAGE_CONNECTION_STRING)"
            )
        from azure.storage.blob import BlobServiceClient  # optional dep

        svc = BlobServiceClient.from_connection_string(
            s.azure_storage_connection_string
        )
        cc = svc.get_container_client(
            getattr(s, "vision_model_container", "vision-models")
        )
        try:
            cc.create_container()
        except Exception:
            pass  # already exists, or pre-provisioned without create rights
        _container_client = cc
        _ready = True
        return cc


def get_manifest(model: str = "counter", *, refresh: bool = False) -> dict:
    """Version + checksum for a model. Cached until the process restarts."""
    if not refresh and model in _manifest_cache:
        return _manifest_cache[model]
    cc = _client()
    raw = cc.get_blob_client(f"{model}/manifest.json").download_blob().readall()
    manifest = json.loads(raw)
    _manifest_cache[model] = manifest
    return manifest


def artifact_ext(manifest: dict) -> str:
    """Blob extension for a release. Pre-iOS manifests have no `ext` key."""
    return str(manifest.get("ext") or ".tflite")


def download_model(model: str, version: str, ext: str = ".tflite") -> bytes:
    """Fetch the weights for an exact version.

    The version comes from the manifest, never straight from the client, so a
    caller can't walk the container by guessing blob names.
    """
    cc = _client()
    return cc.get_blob_client(f"{model}/{version}{ext}").download_blob().readall()


def stream_model(
    model: str,
    version: str,
    *,
    start: int = 0,
    length: int | None = None,
    ext: str = ".tflite",
):
    """Yield the weights in chunks, optionally from a byte offset.

    Reading the whole 38 MB into the server process per request would cost
    38 MB × concurrent downloads of RAM, and gives the client nothing to show a
    progress bar against until it's over. Streaming keeps the backend flat and
    lets the app report progress as bytes land.

    [start]/[length] back HTTP Range, so an app whose download dropped at 20 MB
    resumes there instead of paying for the first 20 MB twice.
    """
    cc = _client()
    bc = cc.get_blob_client(f"{model}/{version}{ext}")
    return bc.download_blob(offset=start, length=length).chunks()


def download_labels(model: str, version: str) -> bytes | None:
    """Label list for a version; None when the release didn't ship one."""
    try:
        cc = _client()
        return (
            cc.get_blob_client(f"{model}/{version}.labels").download_blob().readall()
        )
    except Exception:
        return None
