"""Fixed objective cases drafted with Gemini and reviewed locally before live runs."""

from pathlib import Path

_CASE_ROOT = Path(__file__).with_name("evaluation_cases")
ADDITIONAL_CASES = {
    path.stem: {
        "requirements": path.read_text(encoding="utf-8"),
        "checks": path.with_suffix(".py").read_text(encoding="utf-8"),
    }
    for path in sorted(_CASE_ROOT.glob("*.md"))
}
