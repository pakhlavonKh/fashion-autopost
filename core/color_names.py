"""Russian names for the colour printed on a store page.

Captions repeat the shade the site states, but the sites write it in their
own language: a French Zara page says «Marron», a Spanish one «Marrón», a
Turkish one «Kahverengi». This maps those to one Russian word. A shade that
is not in the table is left exactly as the page wrote it — the caption never
guesses a colour.
"""

from __future__ import annotations

import re
import unicodedata

def _normalize(name: str) -> str:
    """Lowercase, accent-free, punctuation-free form used as the table key."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", stripped).split())


# Base shades, keyed by the accent-free lowercase name the stores use.
_COLORS: dict[str, str] = {}


def _add(russian: str, *names: str) -> None:
    for name in names:
        _COLORS[_normalize(name)] = russian


# English, French, Spanish, Italian, German, Turkish — the storefronts in use.
_add("черный", "black", "noir", "negro", "nero", "schwarz", "siyah")
_add("белый", "white", "blanc", "blanco", "bianco", "weiss", "weiß", "beyaz")
_add("молочный", "off white", "off-white", "blanc casse", "crudo", "bianco sporco",
     "gebrochenes weiss", "kirik beyaz")
_add("кремовый", "cream", "creme", "crema", "krem", "panna")
_add("экрю", "ecru", "ekru")
_add("слоновая кость", "ivory", "ivoire", "marfil", "avorio", "elfenbein", "fildisi")
_add("бежевый", "beige", "bej")
_add("песочный", "sand", "sable", "arena", "sabbia", "kum", "oatmeal", "avena")
_add("кэмел", "camel", "cammello", "kamel", "deve tuyu")
_add("коричневый", "brown", "marron", "braun", "marrone", "kahverengi")
_add("шоколадный", "chocolate", "chocolat", "cioccolato", "schokolade", "cikolata")
_add("карамельный", "caramel", "caramelo", "karamel")
_add("тауп", "taupe", "topo", "tortora")
_add("терракотовый", "terracotta", "terracota", "terrakotta", "kiremit")
_add("серый", "grey", "gray", "gris", "grigio", "grau", "gri")
_add("антрацитовый", "anthracite", "antracita", "antracite", "anthrazit", "antrasit")
_add("графитовый", "charcoal", "graphite", "grafito", "grafit")
_add("темно-синий", "navy", "navy blue", "bleu marine", "marino", "azul marino",
     "blu navy", "marineblau", "lacivert")
_add("синий", "blue", "bleu", "azul", "blu", "blau", "mavi")
_add("голубой", "sky blue", "bleu ciel", "celeste", "azzurro", "himmelblau",
     "gok mavisi", "bebe mavi")
_add("джинсовый", "denim", "denim blue", "bleu jean", "vaquero", "jeansblau", "kot")
_add("бирюзовый", "turquoise", "turquesa", "turchese", "turkis", "türkis", "turkuaz")
_add("зеленый", "green", "vert", "verde", "grun", "grün", "yesil")
_add("хаки", "khaki", "kaki", "caqui", "cachi", "haki")
_add("оливковый", "olive", "oliva", "oliv", "zeytin")
_add("мятный", "mint", "menthe", "menta", "minze", "nane")
_add("изумрудный", "emerald", "emeraude", "esmeralda", "smeraldo", "smaragd", "zumrut")
_add("красный", "red", "rouge", "rojo", "rosso", "rot", "kirmizi")
_add("бордовый", "burgundy", "bordeaux", "burdeos", "bordo", "wine", "vino", "weinrot")
_add("розовый", "pink", "rose", "rosa", "pembe")
_add("пудровый", "nude", "powder pink", "rose poudre", "rosa palo", "pudra")
_add("фуксия", "fuchsia", "fucsia", "fusya", "fuschia")
_add("коралловый", "coral", "corail", "coral", "corallo", "koralle", "mercan")
_add("оранжевый", "orange", "naranja", "arancione", "turuncu")
_add("желтый", "yellow", "jaune", "amarillo", "giallo", "gelb", "sari")
_add("горчичный", "mustard", "moutarde", "mostaza", "senape", "senf", "hardal")
_add("золотистый", "gold", "golden", "dore", "dorado", "oro", "altin")
_add("серебристый", "silver", "argente", "plateado", "argento", "silber", "gumus")
_add("лиловый", "lilac", "lilas", "lila", "lilla", "flieder")
_add("лавандовый", "lavender", "lavande", "lavanda", "lavendel")
_add("фиолетовый", "purple", "violet", "morado", "viola", "violett", "mor")
_add("сиреневый", "mauve", "malva")
_add("разноцветный", "multicolour", "multicolor", "multicolore", "mehrfarbig", "cok renkli")

# «Light grey», «gris clair», «koyu mavi» — a shade of a base colour.
_LIGHT = ("light", "pale", "clair", "claro", "chiaro", "hell", "acik")
_DARK = ("dark", "deep", "fonce", "oscuro", "scuro", "dunkel", "koyu")


def russian_color(name: str) -> str:
    """The Russian name for this shade, or the store's own wording when unknown."""
    cleaned = " ".join((name or "").split()).strip(" .,;")
    if not cleaned:
        return ""
    if re.search(r"[А-Яа-яЁё]", cleaned):
        return cleaned.casefold()

    key = _normalize(cleaned)
    direct = _COLORS.get(key)
    if direct:
        return direct

    for modifiers, prefix in ((_LIGHT, "светло-"), (_DARK, "темно-")):
        base = _strip_modifier(key, modifiers)
        if base is None:
            continue
        shade = _COLORS.get(base)
        if shade:
            return prefix + shade
    return cleaned


def _strip_modifier(key: str, modifiers: tuple[str, ...]) -> str | None:
    """Drop a «light»/«dark» word from either end and return the base shade."""
    words = key.split()
    if len(words) < 2:
        return None
    if words[0] in modifiers:
        return " ".join(words[1:])
    if words[-1] in modifiers:
        return " ".join(words[:-1])
    return None
