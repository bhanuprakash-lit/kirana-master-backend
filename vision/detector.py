"""On-server custom-YOLO shelf detector (ONNX Runtime).

The primary detector for shelf / onboarding photos: our own YOLO (trained on real
kirana shelves, exported to ONNX) run through onnxruntime — no PyTorch, so the image
stays lean. Gemini remains the FALLBACK that fills what YOLO doesn't know yet; those
Gemini-only detections are the products to label next, so coverage grows over time.

Fully optional and lazy:
  * disabled if the model file is absent or onnxruntime can't import (→ Gemini-only,
    exactly today's behaviour) so the server ALWAYS boots;
  * the ~40 MB session is built on first use, not at import, so cold start is unaffected
    for non-vision traffic.

Env:
  VISION_YOLO_ENABLED   "0"/"false" to force-disable even when the model is present.
"""
from __future__ import annotations

import logging
import os
import threading

import numpy as np

from .analyzer import DetectedProduct

logger = logging.getLogger("vision.detector")

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_HERE, "models", "kirana_v7.onnx")
_LABELS_PATH = os.path.join(_HERE, "models", "kirana_v7_labels.txt")
_CLASS_MAP_PATH = os.path.join(_HERE, "models", "kirana_v7_class_map.json")

_INPUT_SIZE = 640
_CONF_THRESHOLD = 0.25
_IOU_THRESHOLD = 0.45
_MAX_DET = 300


def _flag_disabled() -> bool:
    return os.getenv("VISION_YOLO_ENABLED", "1").strip().lower() in {"0", "false", "no"}


class _Detector:
    def __init__(self) -> None:
        self._session = None
        self._labels: list[str] = []
        self._class_map: dict[int, dict] = {}  # class_index -> {product_id, display_name}
        self._input_name = ""
        self._loaded = False
        self._failed = False
        self._lock = threading.Lock()

    def _load_class_map(self) -> None:
        """Curated class_index → product_id map (deterministic; supersedes fuzzy
        matching for confirmed classes). Absent/partial map ⇒ those classes just
        fall through to the fuzzy matcher, so this is purely additive."""
        import json
        try:
            with open(_CLASS_MAP_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                if v.get("confirmed") and v.get("product_id") is not None:
                    self._class_map[int(k)] = {
                        "product_id": int(v["product_id"]),
                        "display_name": v.get("display_name"),
                    }
            logger.info("vision.detector class map: %d confirmed", len(self._class_map))
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision.detector class map load failed: %s", exc)

    def available(self) -> bool:
        """Cheap check the caller uses to decide whether to even try YOLO."""
        if _flag_disabled() or self._failed:
            return False
        if self._loaded:
            return True
        return os.path.exists(_MODEL_PATH)

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        if self._failed or _flag_disabled():
            return False
        with self._lock:
            if self._loaded:
                return True
            if self._failed:
                return False
            try:
                import onnxruntime as ort  # local import: optional dep
                if not os.path.exists(_MODEL_PATH):
                    raise FileNotFoundError(_MODEL_PATH)
                so = ort.SessionOptions()
                so.intra_op_num_threads = int(os.getenv("VISION_YOLO_THREADS", "2"))
                self._session = ort.InferenceSession(
                    _MODEL_PATH, sess_options=so, providers=["CPUExecutionProvider"])
                self._input_name = self._session.get_inputs()[0].name
                with open(_LABELS_PATH, encoding="utf-8") as f:
                    self._labels = [ln.strip() for ln in f if ln.strip()]
                self._load_class_map()
                self._loaded = True
                logger.info("vision.detector loaded (%d classes)", len(self._labels))
                return True
            except Exception as exc:  # noqa: BLE001 — never break the request
                self._failed = True
                logger.warning("vision.detector disabled (load failed): %s", exc)
                return False

    def detect(self, image_bytes: bytes) -> list[DetectedProduct]:
        """Detect products in one image → detections AGGREGATED per class (count =
        number of boxes of that class, bbox = the most-confident box, normalized to
        the original image). Empty list if disabled or nothing found. Never raises."""
        if not self._ensure_loaded():
            return []
        try:
            arr, ratio, pad, (ow, oh) = self._preprocess(image_bytes)
            out = self._session.run(None, {self._input_name: arr})[0]  # [1, 4+nc, N]
            return self._postprocess(out, ratio, pad, ow, oh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vision.detector inference failed: %s", exc)
            return []

    # ── image → tensor ────────────────────────────────────────────────────────
    def _preprocess(self, image_bytes: bytes):
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        ow, oh = img.size
        r = min(_INPUT_SIZE / ow, _INPUT_SIZE / oh)
        nw, nh = round(ow * r), round(oh * r)
        resized = img.resize((nw, nh), Image.BILINEAR)
        canvas = Image.new("RGB", (_INPUT_SIZE, _INPUT_SIZE), (114, 114, 114))
        px, py = (_INPUT_SIZE - nw) // 2, (_INPUT_SIZE - nh) // 2
        canvas.paste(resized, (px, py))
        arr = np.asarray(canvas, dtype=np.float32) / 255.0  # HWC
        arr = arr.transpose(2, 0, 1)[None]  # 1CHW
        return np.ascontiguousarray(arr), r, (px, py), (ow, oh)

    # ── tensor → detections ───────────────────────────────────────────────────
    def _postprocess(self, out, ratio, pad, ow, oh) -> list[DetectedProduct]:
        preds = np.squeeze(out, 0).T  # [N, 4+nc]
        boxes = preds[:, :4]
        scores = preds[:, 4:]
        class_ids = scores.argmax(1)
        confs = scores.max(1)

        keep = confs >= _CONF_THRESHOLD
        boxes, class_ids, confs = boxes[keep], class_ids[keep], confs[keep]
        if boxes.shape[0] == 0:
            return []

        # Box coords come out normalized to the 640 canvas → scale to canvas px,
        # then undo the letterbox (remove padding, divide by resize ratio) → orig px.
        cx = boxes[:, 0] * _INPUT_SIZE
        cy = boxes[:, 1] * _INPUT_SIZE
        w = boxes[:, 2] * _INPUT_SIZE
        h = boxes[:, 3] * _INPUT_SIZE
        x1 = (cx - w / 2 - pad[0]) / ratio
        y1 = (cy - h / 2 - pad[1]) / ratio
        x2 = (cx + w / 2 - pad[0]) / ratio
        y2 = (cy + h / 2 - pad[1]) / ratio
        xyxy = np.stack([x1, y1, x2, y2], 1)

        keep_idx = _nms(xyxy, confs, _IOU_THRESHOLD)[:_MAX_DET]

        # Aggregate by class (mirrors Gemini's per-variant facings count).
        agg: dict[int, dict] = {}
        for i in keep_idx:
            cid = int(class_ids[i])
            b = xyxy[i]
            entry = agg.setdefault(cid, {"count": 0, "best_conf": 0.0, "box": b})
            entry["count"] += 1
            if confs[i] > entry["best_conf"]:
                entry["best_conf"] = float(confs[i])
                entry["box"] = b

        results: list[DetectedProduct] = []
        for cid, e in agg.items():
            name = self._labels[cid] if 0 <= cid < len(self._labels) else str(cid)
            bx1 = float(np.clip(e["box"][0] / ow, 0, 1))
            by1 = float(np.clip(e["box"][1] / oh, 0, 1))
            bx2 = float(np.clip(e["box"][2] / ow, 0, 1))
            by2 = float(np.clip(e["box"][3] / oh, 0, 1))
            dp = DetectedProduct(
                raw_name=_prettify(name),
                count=e["count"],
                x1=min(bx1, bx2), y1=min(by1, by2),
                x2=max(bx1, bx2), y2=max(by1, by2),
                visible_text=name,        # the class label grounds the detection
                confidence=e["best_conf"],
            )
            # Deterministic resolution via the curated map (skips fuzzy matching
            # downstream). Unmapped classes stay unknown → fuzzy fallback / review.
            mapped = self._class_map.get(cid)
            if mapped:
                dp.product_id = mapped["product_id"]
                dp.display_name = mapped.get("display_name")
                dp.match_score = 1.0
                dp.is_unknown = False
            results.append(dp)
        return results


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float) -> list[int]:
    """Class-agnostic NMS. Boxes xyxy, returns kept indices (score-desc)."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


def _prettify(class_name: str) -> str:
    return class_name.replace("_", " ").replace("-", " ").strip()


# Module singleton.
_detector = _Detector()


def is_available() -> bool:
    return _detector.available()


def detect(image_bytes: bytes) -> list[DetectedProduct]:
    return _detector.detect(image_bytes)
