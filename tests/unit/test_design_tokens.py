"""The dashboard's colour tokens keep text readable: WCAG AA contrast (4.5:1) for every text colour on every surface,
in both themes, plus ink text on the lime/lavender/coral panels. Read from dashboard/src/styles/abstracts/_tokens.scss."""
import re
from pathlib import Path
import pytest

TOKENS = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "styles" / "abstracts" / "_tokens.scss"


def theme(name: str) -> dict[str, str]:
    source = TOKENS.read_text(encoding="utf-8")
    brand = dict(re.findall(r"([\w-]+): (#[0-9a-fA-F]{6})", re.search(r"\$brand: \((.*?)\);", source, re.S).group(1)))
    block = re.search(rf"\b{name}: \((.*?)\n  \)", source, re.S).group(1)
    colours = dict(re.findall(r"([\w-]+): (#[0-9a-fA-F]{6})", block))
    colours.update({key: brand[ref] for key, ref in re.findall(r"([\w-]+): map\.get\(\$brand, ([\w-]+)\)", block)})
    return colours


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("name", ["light", "dark"])
def test_text_tokens_meet_wcag_aa_on_every_surface(name):
    c = theme(name)
    for text in ("text", "muted", "faint", "link"):
        for surface in ("bg", "card", "card-2"):
            assert contrast(c[text], c[surface]) >= 4.5, f"{name}: {text} on {surface} is {contrast(c[text], c[surface]):.2f}:1"
    for text in ("rail-text", "rail-muted"):
        assert contrast(c[text], c["rail"]) >= 4.5, f"{name}: {text} on rail"


@pytest.mark.parametrize("name", ["light", "dark"])
def test_ink_on_coloured_panels(name):
    c = theme(name)
    assert contrast(c["accent-ink"], c["accent"]) >= 4.5      # lime buttons and "next step" panel
    assert contrast(c["lavender-ink"], c["lavender"]) >= 4.5  # SLA panel
    assert contrast(c["coral-ink"], c["coral"]) >= 4.5        # urgent / high priority chip
