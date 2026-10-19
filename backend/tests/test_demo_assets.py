import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image


def generator():
    path = Path(__file__).resolve().parents[2] / "scripts/generate_demo_assets.py"
    spec = importlib.util.spec_from_file_location("demo_assets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asset_encoder_preserves_high_resolution_hero_and_all_four_gif_phases(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    records = []
    for name, color in [
        ("01-mission-start", "navy"),
        ("02-bug-detected", "red"),
        ("03-auto-patch", "orange"),
        ("04-verified", "green"),
    ]:
        Image.new("RGB", (2560, 1600), color).save(frames / f"{name}.png")
        records.append({"name": name, "label": name})
    (frames / "manifest.json").write_text(json.dumps(records))
    output = tmp_path / "assets"
    summary = generator().encode_assets(frames, output)
    with Image.open(output / "war-room-hero.png") as hero:
        assert hero.size == (2560, 1600)
    with Image.open(output / "war-room-demo.gif") as gif:
        assert gif.size == (1280, 800) and gif.n_frames == 4 and gif.info["loop"] == 0
    assert len(summary["phases"]) == 4


def test_asset_encoder_rejects_incomplete_capture(tmp_path):
    (tmp_path / "manifest.json").write_text("[]")
    with pytest.raises(ValueError, match="all four"):
        generator().encode_assets(tmp_path, tmp_path / "output")
