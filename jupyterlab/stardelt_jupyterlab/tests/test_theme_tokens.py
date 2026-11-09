"""FAST cannot parse the space-separated rgb() form (stardelt-python#67)."""

import pathlib
import re

THEMES = pathlib.Path(__file__).resolve().parents[2] / "style"
SPACE_RGB = re.compile(r"rgb\(\s*\d+\s+\d+\s+\d+\s*\)")


def test_theme_tokens_use_the_comma_form():
    for name in ("graphite.css", "silver.css"):
        text = (THEMES / name).read_text(encoding="utf-8")
        assert not SPACE_RGB.search(text), (
            f"{name}: use rgb(r, g, b); JupyterLab's FAST token parser rejects rgb(r g b)"
        )
