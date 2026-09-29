"""European catalog URLs for brands the admin can add by name alone."""

import re

from adapters.europe_markets import is_european_store_url


def normalize_brand_name(name: str) -> str:
    """Turn 'H&M' or 'Massimo Dutti' into a stable slug."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


# Spain (or UK, for ASOS) women/new-in pages. Turkey and US storefronts are absent.
_BRANDS: dict[str, tuple[str, str]] = {
    "zara": ("zara", "https://www.zara.com/es/es/mujer-nuevo-l1180.html"),
    "mango": ("mango", "https://shop.mango.com/es/es/c/mujer/new-now/56b5c5ed"),
    "stradivarius": ("stradivarius", "https://www.stradivarius.com/es/mujer/nuevo-n1906"),
    "stradi": ("stradivarius", "https://www.stradivarius.com/es/mujer/nuevo-n1906"),
    "bershka": ("bershka", "https://www.bershka.com/es/mujer/novedades-n1261.html"),
    "pullandbear": ("pullandbear", "https://www.pullandbear.com/es/mujer/novedades-n1483"),
    "pull-bear": ("pullandbear", "https://www.pullandbear.com/es/mujer/novedades-n1483"),
    "pull-and-bear": ("pullandbear", "https://www.pullandbear.com/es/mujer/novedades-n1483"),
    "massimodutti": ("massimodutti", "https://www.massimodutti.com/es/mujer/novedades-n1474"),
    "massimo-dutti": ("massimodutti", "https://www.massimodutti.com/es/mujer/novedades-n1474"),
    "massimo": ("massimodutti", "https://www.massimodutti.com/es/mujer/novedades-n1474"),
    "oysho": ("oysho", "https://www.oysho.com/es/mujer/novedades-n1272"),
    "lefties": ("lefties", "https://www.lefties.com/es/mujer/novedades-n1264.html"),
    "hm": ("hm", "https://www2.hm.com/es_es/mujer/novedades/ver-todo.html"),
    "h-m": ("hm", "https://www2.hm.com/es_es/mujer/novedades/ver-todo.html"),
    "handm": ("hm", "https://www2.hm.com/es_es/mujer/novedades/ver-todo.html"),
    "h-and-m": ("hm", "https://www2.hm.com/es_es/mujer/novedades/ver-todo.html"),
    "reserved": ("reserved", "https://www.reserved.com/es/es/"),
    "sinsay": ("sinsay", "https://www.sinsay.com/es/es/"),
    "cropp": ("cropp", "https://www.cropp.com/es/es/"),
    "house": ("house", "https://www.housebrand.com/es/es/"),
    "housebrand": ("house", "https://www.housebrand.com/es/es/"),
    "house-brand": ("house", "https://www.housebrand.com/es/es/"),
    "mohito": ("mohito", "https://www.mohito.com/es/es/"),
    "calzedonia": ("calzedonia", "https://www.calzedonia.com/es/"),
    "uniqlo": ("uniqlo", "https://www.uniqlo.com/es/es/women"),
    "asos": ("asos", "https://www.asos.com/es/mujer/"),
    "cos": ("cos", "https://www.cos.com/es-es/women/new-arrivals.html"),
}


def resolve_european_brand(name: str) -> tuple[str, str] | None:
    """Return the canonical slug and European catalog URL for a known brand."""
    entry = _BRANDS.get(normalize_brand_name(name))
    if not entry:
        return None
    slug, url = entry
    if not is_european_store_url(url):
        return None
    return slug, url
