"""Unit tests for vision.analyzer.parse_detections (pure, no I/O)."""
from __future__ import annotations

import json

from vision.analyzer import MIN_CONFIDENCE, parse_detections


def _item(name="Parle-G Biscuits 200g", text="PARLE-G", conf=0.9, count=2,
          bbox=(0.1, 0.2, 0.8, 0.7)):
    return {
        "product_name": name,
        "visible_text": text,
        "confidence": conf,
        "count": count,
        "bbox": list(bbox),
    }


def test_parses_a_valid_detection():
    raw = json.dumps([_item()])
    out = parse_detections(raw)
    assert len(out) == 1
    d = out[0]
    assert d.raw_name == "Parle-G Biscuits 200g"
    assert d.visible_text == "PARLE-G"
    assert d.count == 2
    assert d.is_unknown is True  # matcher hasn't run yet


def test_bbox_is_reordered_from_yxyx_to_xyxy():
    # Gemini returns [y1, x1, y2, x2]; analyzer stores x1,y1,x2,y2 (sorted).
    raw = json.dumps([_item(bbox=(0.2, 0.1, 0.7, 0.8))])
    d = parse_detections(raw)[0]
    assert d.x1 == 0.1 and d.y1 == 0.2
    assert d.x2 == 0.8 and d.y2 == 0.7


def test_strips_markdown_code_fences():
    raw = "```json\n" + json.dumps([_item()]) + "\n```"
    assert len(parse_detections(raw)) == 1


def test_extracts_array_embedded_in_prose():
    raw = "Here is what I see: " + json.dumps([_item()]) + " — that's all."
    assert len(parse_detections(raw)) == 1


def test_drops_low_confidence():
    raw = json.dumps([_item(conf=MIN_CONFIDENCE - 0.01)])
    assert parse_detections(raw) == []


def test_keeps_confidence_at_threshold():
    raw = json.dumps([_item(conf=MIN_CONFIDENCE)])
    assert len(parse_detections(raw)) == 1


def test_drops_items_without_visible_text_grounding():
    raw = json.dumps([_item(text="")])
    assert parse_detections(raw) == []


def test_drops_items_with_empty_name():
    raw = json.dumps([_item(name="")])
    assert parse_detections(raw) == []


def test_count_is_clamped_to_at_least_one():
    raw = json.dumps([_item(count=0)])
    assert parse_detections(raw)[0].count == 1


def test_missing_or_short_bbox_defaults_to_full_frame():
    item = _item()
    item["bbox"] = [0.1]  # too short
    d = parse_detections(json.dumps([item]))[0]
    assert (d.x1, d.y1, d.x2, d.y2) == (0.0, 0.0, 1.0, 1.0)


def test_empty_array_returns_empty():
    assert parse_detections("[]") == []


def test_garbage_input_returns_empty():
    assert parse_detections("not json at all") == []
    assert parse_detections("") == []
    assert parse_detections("{}") == []  # object, not a list


def test_skips_malformed_items_but_keeps_good_ones():
    raw = json.dumps([_item(), "not-a-dict", {"product_name": "x"}, _item(name="Tata Salt")])
    out = parse_detections(raw)
    # The good two survive; the string and the ungrounded one are dropped.
    assert {d.raw_name for d in out} == {"Parle-G Biscuits 200g", "Tata Salt"}


def test_bbox_json_round_trips():
    d = parse_detections(json.dumps([_item(bbox=(0.2, 0.1, 0.7, 0.8))]))[0]
    assert json.loads(d.bbox_json()) == [0.1, 0.2, 0.8, 0.7]
