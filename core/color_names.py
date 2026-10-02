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
_add("морская волна", "lagoon", "laguna", "teal")
_add("петроль", "petrol", "petroleo", "petrolio", "petroleum")
_add("коньячный", "cognac", "conac", "konyak")
_add("шалфейный", "sage", "salvia", "salbei", "ada cayi")
_add("терракотовый", "rust", "rost", "roggia", "oxido", "pas")
_add("охра", "ochre", "ocher", "ocre", "oker")
_add("табачный", "tobacco", "tabac", "tabaco", "tabak")
_add("ванильный", "vanilla", "vanille", "vainilla", "vanilya")
_add("сливочный", "butter", "beurre", "mantequilla", "tereyagi", "buttermilk")
_add("лаймовый", "lime", "lima")
_add("медный", "copper", "cuivre", "cobre", "rame", "kupfer", "bakir")
_add("бронзовый", "bronze", "bronzo", "bronce", "bronz")
_add("индиго", "indigo")
_add("шампань", "champagne", "champagner", "sampanya")
_add("кирпичный", "brick", "brique", "ladrillo", "tugla")
_add("сливовый", "plum", "prune", "ciruela", "prugna", "pflaume", "erik")
_add("вишневый", "cherry", "cerise", "cereza", "ciliegia", "kirsche", "visne")
_add("зеленый", "green", "vert", "verde", "grun", "grün", "yesil")
_add("хаки", "khaki", "kaki", "caqui", "cachi", "haki")
_add("оливковый", "olive", "oliva", "oliv", "zeytin")
_add("мятный", "mint", "menthe", "menta", "minze", "nane")
_add("изумрудный", "emerald", "emeraude", "esmeralda", "smeraldo", "smaragd", "zumrut")
_add("красный", "red", "rouge", "rojo", "rosso", "rot", "kirmizi")
_add("бордовый", "burgundy", "bordeaux", "burdeos", "bordo", "wine", "vino", "weinrot", "maroon")
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
_add("персиковый", "peach", "peche", "melocoton", "pesca", "pfirsich", "seftali")
_add("абрикосовый", "apricot", "abricot", "albaricoque", "albicocca", "aprikose", "kayisi")
_add("баклажановый", "aubergine", "eggplant", "berenjena", "melanzana", "patlican")
_add("фисташковый", "pistachio", "pistache", "pistacchio", "fistik")
_add("малиновый", "raspberry", "framboise", "frambuesa", "lampone", "himbeere", "ahududu")
_add("стальной", "steel", "acier", "acero", "stahl", "celik")
_add("жемчужный", "pearl", "perle", "perla", "inci")
_add("капучино", "cappuccino", "kapucino")
_add("коричный", "cinnamon", "cannelle", "canela", "zimt", "tarcin")
_add("лососевый", "salmon", "saumon", "salmone", "lachs", "somon")
_add("янтарный", "amber", "ambre", "ambra", "bernstein", "kehribar")
_add("маджента", "magenta")
_add("разноцветный", "multicolour", "multicolor", "multicolore", "mehrfarbig", "cok renkli")

# «Light grey», «gris clair», «koyu mavi» — a shade of a base colour.
_LIGHT = ("light", "pale", "clair", "claro", "chiaro", "hell", "acik")
_DARK = ("dark", "deep", "fonce", "oscuro", "scuro", "dunkel", "koyu")

_MODIFIERS: dict[str, str] = {
    "light": "светло-",
    "pale": "бледно-",
    "clair": "светло-",
    "claro": "светло-",
    "chiaro": "светло-",
    "hell": "светло-",
    "acik": "светло-",
    "dark": "темно-",
    "deep": "темно-",
    "fonce": "темно-",
    "oscuro": "темно-",
    "scuro": "темно-",
    "dunkel": "темно-",
    "koyu": "темно-",
    "bright": "ярко-",
    "vivid": "ярко-",
    "dusty": "пыльно-",
    "smoky": "дымчато-",
    "smoke": "дымчато-",
    "soft": "нежно-",
    "warm": "тепло-",
    "cool": "холодно-",
    "burnt": "жжено-",
    "muted": "приглушенно-",
}


def _to_compound_prefix(color_ru: str) -> str:
    """Turn a Russian color adjective or noun into a compound prefix (e.g. песочный -> песочно-)."""
    color_ru = color_ru.strip().lower()
    if color_ru.endswith("ий"):
        return color_ru[:-2] + "е-"
    if color_ru.endswith("ый") or color_ru.endswith("ой"):
        return color_ru[:-2] + "о-"
    if color_ru.endswith("ая") or color_ru.endswith("яя"):
        return color_ru[:-2] + "о-"
    return color_ru + "-"


def _decompose_compound(key: str) -> str | None:
    """Decompose a multi-word or hyphenated shade into Russian (e.g. 'sand brown' -> 'песочно-коричневый')."""
    words = key.split()
    if len(words) != 2:
        return None
    w1, w2 = words[0], words[1]

    # Check modifier prefixes
    if w1 in _MODIFIERS:
        shade = _COLORS.get(w2)
        if shade:
            return _MODIFIERS[w1] + shade
    if w2 in _MODIFIERS:
        shade = _COLORS.get(w1)
        if shade:
            return _MODIFIERS[w2] + shade

    # Compound of two shades (e.g. 'sand' + 'brown' or 'olive' + 'green')
    ru1 = _COLORS.get(w1)
    ru2 = _COLORS.get(w2)
    if ru1 and ru2:
        prefix = _to_compound_prefix(ru1)
        return prefix + ru2

    return None


import json
import logging
from pathlib import Path

_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "color_translations.json"
_RUNTIME_CACHE: dict[str, str] = {}
_CACHE_LOADED = False


def _load_cache() -> dict[str, str]:
    global _RUNTIME_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return _RUNTIME_CACHE
    _CACHE_LOADED = True
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _RUNTIME_CACHE = {_normalize(k): str(v).strip().lower() for k, v in data.items()}
        except Exception as exc:
            logging.getLogger(__name__).warning("Failed to load color translation cache: %s", exc)
    return _RUNTIME_CACHE


def save_color_translation(raw_name: str, russian_name: str) -> None:
    """Persist an AI-translated color to local cache on disk."""
    key = _normalize(raw_name)
    val = russian_name.strip().lower()
    if not key or not val:
        return
    cache = _load_cache()
    cache[key] = val
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if _CACHE_FILE.exists():
            try:
                existing = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing[key] = val
        _CACHE_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to save color translation cache: %s", exc)


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

    compound = _decompose_compound(key)
    if compound:
        return compound

    cached = _load_cache().get(key)
    if cached:
        return cached

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
