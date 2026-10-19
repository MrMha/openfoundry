"""Build and capture a real offline mission at 1280x800, then encode its four stages.

Requires npm ci (Playwright) and a Chromium installation. No API key, live provider,
external publication, or state fabrication is involved. The temporary API is removed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def encode_assets(frames_dir: Path, output: Path) -> dict[str, object]:
    manifest = json.loads((frames_dir / "manifest.json").read_text())
    if len(manifest) != 4:
        raise ValueError("A visual demo requires all four captured mission phases")
    output.mkdir(parents=True, exist_ok=True)
    images: list[Image.Image] = []
    for item in manifest:
        with Image.open(frames_dir / f"{item['name']}.png") as source:
            if source.size != (2560, 1600):
                raise ValueError("Capture must use a 1280x800 viewport at 2x device scale")
            image = source.convert("RGB").resize((1280, 800), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 758, 1280, 800), fill="#090D16")
        draw.text((22, 770), item["label"], font=ImageFont.load_default(size=17), fill="#6ee7b7")
        images.append(image.quantize(colors=256, method=Image.Quantize.MEDIANCUT))
    shutil.copyfile(frames_dir / "03-auto-patch.png", output / "war-room-hero.png")
    images[0].save(
        output / "war-room-demo.gif",
        save_all=True,
        append_images=images[1:],
        duration=[1800, 2200, 2200, 3200],
        loop=0,
        disposal=2,
        optimize=False,
    )
    summary = {
        "viewport": [1280, 800],
        "hero_pixels": [2560, 1600],
        "phases": [item["label"] for item in manifest],
        "api_cost_usd": "0.00",
    }
    (output / "demo-assets.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/assets")
    args = parser.parse_args()
    if not args.skip_build:
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Build the War Room first: cd frontend && npm ci && npm run build")
    with tempfile.TemporaryDirectory(prefix="openfoundry-capture-") as temporary:
        directory = Path(temporary)
        environment = dict(os.environ)
        for key in (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "ANTHROPIC_API_KEY",
            "OLLAMA_BASE_URL",
            "DATABASE_URL",
            "OPENFOUNDRY_WORKSPACE_ROOT",
            "OPENFOUNDRY_MCP_SERVERS",
            "OPENFOUNDRY_DEPARTMENT_PLUGINS",
        ):
            environment.pop(key, None)
        token = secrets.token_hex(24)
        environment.update(
            OPENFOUNDRY_HOME=str(directory / "data"),
            OPENFOUNDRY_API_TOKEN=token,
            OPENFOUNDRY_SANDBOX_MODE="guarded",
            OPENFOUNDRY_DEMO_STEP_DELAY_SECONDS="2.5",
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        with (directory / "backend.log").open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "backend.app.main", "--port", str(port)],
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urlopen(origin + "/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError("Temporary capture API failed to start") from None
                        time.sleep(0.1)
                capture_environment = {**os.environ, "OPENFOUNDRY_CAPTURE_TOKEN": token}
                subprocess.run(
                    ["node", "scripts/capture_demo.mjs", origin, str(directory / "frames")],
                    cwd=ROOT / "frontend",
                    env=capture_environment,
                    check=True,
                    timeout=120,
                )
                summary = encode_assets(directory / "frames", args.output)
                print(json.dumps({"output": str(args.output), **summary}, indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
