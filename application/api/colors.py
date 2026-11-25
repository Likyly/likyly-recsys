"""Colours of catalog items, for the `closest_color` placement rule (placement_filters.py).

An item's colour is its `color` property (or the one a placement names in
business_rules.color_attribute): a hex code ("#2E7D32"), or a colour name in English, French,
Spanish, Italian or German ("vert", "verde", "burgundy", "navy blue"). A list takes its first
value. Two colours are compared as the eye sees them: CIE Lab, Delta E 76. An unknown colour is
None - the rule then does nothing for that item.
"""
import math
import re
import unicodedata
from typing import Any, Optional

RGB = tuple[int, int, int]

# One approximate shade per name. Several languages per entry; fashion and leather names included.
_SHADES: dict[str, tuple[str, ...]] = {
    "#000000": ("black", "noir", "negro", "nero", "schwarz", "ebony", "ebene", "jet"),
    "#FFFFFF": ("white", "blanc", "blanco", "bianco", "weiss", "weis"),
    "#F5F0E1": ("ivory", "ivoire", "marfil", "avorio", "elfenbein", "cream", "creme", "crema", "ecru", "off white", "off-white"),
    "#808080": ("grey", "gray", "gris", "grigio", "grau", "anthracite", "antracite", "charcoal"),
    "#C0C0C0": ("silver", "argent", "argente", "plata", "plateado", "argento", "silber", "metallic"),
    "#D4AF37": ("gold", "or", "dore", "oro", "dorado", "golden"),
    "#C62828": ("red", "rouge", "rojo", "rosso", "rot", "scarlet", "ecarlate", "cherry", "cerise"),
    "#800020": ("burgundy", "bordeaux", "burdeos", "bordo", "wine", "vin", "vino", "oxblood", "maroon", "granate"),
    "#E91E63": ("pink", "rose", "rosa", "fuchsia", "fucsia", "magenta"),
    "#F8BBD0": ("light pink", "rose pale", "blush", "nude rose", "poudre", "powder"),
    "#FF7F50": ("coral", "corail", "coral red"),
    "#EF6C00": ("orange", "naranja", "arancione", "arancio", "rust", "rouille"),
    "#FDD835": ("yellow", "jaune", "amarillo", "giallo", "gelb", "mustard", "moutarde", "mostaza", "senape", "ocre", "ochre"),
    "#2E7D32": ("green", "vert", "verde", "grun", "emerald", "emeraude", "esmeralda", "smeraldo"),
    "#556B2F": ("olive", "olivo", "oliva", "khaki", "kaki", "caqui", "militaire", "military", "army"),
    "#98FB98": ("light green", "vert clair", "verde claro", "mint", "menthe", "sage", "sauge"),
    "#1565C0": ("blue", "bleu", "azul", "blu", "blau", "royal blue", "bleu roi", "cobalt"),
    "#87CEEB": ("light blue", "bleu clair", "azul claro", "celeste", "sky", "ciel", "baby blue"),
    "#1F2A44": ("navy", "navy blue", "marine", "bleu marine", "azul marino", "blu navy", "dunkelblau"),
    "#1560BD": ("denim", "jean", "jeans", "indigo"),
    "#008080": ("teal", "turquoise", "turquesa", "turchese", "petrol", "petrole", "canard"),
    "#6A1B9A": ("purple", "violet", "violeta", "viola", "lila", "lilac", "mauve", "plum", "prune", "aubergine"),
    "#6D4C41": ("brown", "marron", "brun", "brown leather", "cafe", "coffee", "braun", "marrone", "chocolate", "chocolat", "cioccolato", "moka", "mocha"),
    "#A0522D": ("cognac", "tan", "caramel", "caramelo", "camel", "fauve", "cuero", "cuoio", "tabac", "tobacco", "terracotta", "terracota"),
    "#D2B48C": ("beige", "sand", "sable", "arena", "sabbia", "nude", "taupe", "stone", "pierre", "greige", "natural", "naturel", "natural leather"),
}
_NAMES: dict[str, str] = {name: hex_code for hex_code, names in _SHADES.items() for name in names}
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s_\-/]+", " ", text.lower()).strip()


def _hex_to_rgb(code: str) -> RGB:
    code = code.lstrip("#")
    if len(code) == 3:
        code = "".join(c * 2 for c in code)
    return int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16)


def parse_color(value: Any) -> Optional[RGB]:
    """An RGB triple from a hex code or a colour name, else None."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if _HEX.match(raw):
        return _hex_to_rgb(raw)
    folded = _fold(raw)
    if folded in _NAMES:
        return _hex_to_rgb(_NAMES[folded])
    # "dark green leather", "verde oscuro": the longest known name inside it.
    words = folded.split(" ")
    for size in range(min(3, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            name = " ".join(words[start:start + size])
            if name in _NAMES:
                return _hex_to_rgb(_NAMES[name])
    return None


def _to_lab(rgb: RGB) -> tuple[float, float, float]:
    def linear(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(a: RGB, b: RGB) -> float:
    """Delta E 76: 0 for the same colour, about 2 for a barely visible difference, 100+ apart."""
    la, lb = _to_lab(a), _to_lab(b)
    return math.dist(la, lb)
