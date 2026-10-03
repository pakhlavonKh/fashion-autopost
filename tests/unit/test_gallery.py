"""Carousel order: original looks first, product angles last, no duplicate photos."""

from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image, ImageDraw

from core.gallery import arrange_carousel, is_product_angle, ordered_photos
from core.image_downloader import ImageDownloader, unique_images


def test_product_angles_close_the_carousel_and_duplicates_collapse() -> None:
    looks = [f"https://media.mango.com/is/image/punto/10-99-{index:03d}" for index in range(1, 9)]
    urls = [
        *looks,
        "https://media.mango.com/is/image/punto/10-99-001?imwidth=400",
        "https://media.mango.com/is/image/punto/10-99-90",
        "https://media.mango.com/is/image/punto/10-99-91",
        "https://media.mango.com/is/image/punto/10-99-92",
        "https://media.mango.com/is/image/punto/10-99-020",
        "https://static.zara.net/photos/a/w/563/coat_1_1_1.jpg",
        "https://static.zara.net/photos/a/w/2048/coat_1_1_1.jpg",
    ]
    ordered = arrange_carousel(urls, max_photos=10)

    assert ordered[0] == "https://media.mango.com/is/image/punto/10-99-001?imwidth=400"
    assert ordered[1:7] == looks[1:7]
    assert ordered[-3:] == [
        "https://media.mango.com/is/image/punto/10-99-90",
        "https://media.mango.com/is/image/punto/10-99-91",
        "https://media.mango.com/is/image/punto/10-99-92",
    ]
    assert all("020" not in url for url in ordered)
    assert sum("10-99-001" in url for url in ordered) == 1

    zara = arrange_carousel([
        "https://static.zara.net/photos/a/w/563/coat_1_1_1.jpg",
        "https://static.zara.net/photos/a/w/2048/coat_1_1_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_2_1_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_3_1_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_3_2_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_3_3_1.jpg",
        "https://static.zara.net/photos/a/w/750/coat_6_1_1.jpg",
    ])
    assert [url.split("/")[-1] for url in zara] == [
        "coat_1_1_1.jpg",
        "coat_2_1_1.jpg",
        "coat_3_1_1.jpg",
        "coat_3_2_1.jpg",
        "coat_3_3_1.jpg",
    ]
    assert "/w/2048/" in zara[0]


def test_mango_front_then_detail_close_ups_close_the_carousel() -> None:
    base = "https://media.mango.com/is/image/punto/37007813-05-"
    page_order = ["002", "001", "003", "081", "084", "082", "023", "030", "900"]
    ordered = arrange_carousel([base + code for code in page_order], max_photos=10)
    assert [url.rsplit("-", 1)[-1] for url in ordered] == [
        "002", "001", "003", "081", "084", "082", "900", "023", "030",
    ]
    full = arrange_carousel(
        [base + f"{index:03d}" for index in range(1, 10)] + [base + "023", base + "030", base + "900"],
        max_photos=10,
    )
    assert len(full) == 10
    assert [url.rsplit("-", 1)[-1] for url in full[-3:]] == ["900", "023", "030"]


def test_pale_product_still_life_is_not_dropped_as_a_swatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        still = folder / "still.jpg"
        flat = folder / "flat.jpg"
        Image.new("RGB", (733, 1024), (236, 234, 230)).save(still)
        Image.new("RGB", (733, 1024), (150, 140, 130)).save(flat)
        urls = {
            "https://media.mango.com/is/image/punto/37007813-05-900": still,
            "https://media.mango.com/is/image/punto/37007813-05-004": flat,
        }
        downloader = ImageDownloader(dest_dir=folder)
        with patch.object(downloader, "download", side_effect=lambda url, external_id="": urls[url]):
            kept = downloader.download_all(list(urls), external_id="p")
    assert kept == [still]
    assert is_product_angle("https://media.mango.com/is/image/punto/37007813-05-900")
    assert is_product_angle("https://static.zara.net/photos/a/w/750/coat_3_2_1.jpg")
    assert not is_product_angle("https://media.mango.com/is/image/punto/37007813-05-004")


def test_zara_plain_shots_are_front_back_and_close() -> None:
    html = """
    {"kind":"full","path":"/assets/public/abc/item-p","name":"item-p"}
    {"kind":"other","path":"/assets/public/abc/item-a1","name":"item-a1"}
    {"kind":"plain","path":"/assets/public/abc/item-e0","name":"item-e0"}
    {"kind":"plain","path":"/assets/public/abc/item-e1","name":"item-e1"}
    {"kind":"plain","path":"/assets/public/abc/item-e2","name":"item-e2"}
    {"kind":"colorcut","path":"/assets/public/abc/item-c","name":"item-c"}
    """
    ordered = ordered_photos(html, "zara", "https://www.zara.com/es/item.html")
    assert [url.rsplit("/", 1)[-1] for url in ordered] == [
        "item-p.jpg",
        "item-a1.jpg",
        "item-e0.jpg",
        "item-e1.jpg",
        "item-e2.jpg",
    ]


def test_one_post_does_not_mix_other_mango_products() -> None:
    page = "https://shop.mango.com/it/it/p/donna/scarpe/stivaletti/botin-tacco/27011111/70/01"
    own = [
        "https://media.mango.com/is/image/punto/27011111-70-001",
        "https://media.mango.com/is/image/punto/27011111-70-002",
        "https://media.mango.com/is/image/punto/27011111-70-900",
    ]
    others = [
        "https://media.mango.com/is/image/punto/27022222-30-001",
        "https://media.mango.com/is/image/punto/27033333-99-001",
        "https://media.mango.com/is/image/punto/27011111-05-001",
    ]
    html = " ".join(own + others)
    ordered = ordered_photos(html, "mango", page, own[:1] + others)
    assert ordered
    assert all("27011111-70-" in url for url in ordered)
    assert not any(token in " ".join(ordered) for token in ("27022222", "27033333", "27011111-05"))


def test_identical_files_are_not_posted_twice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        first = folder / "front.jpg"
        second = folder / "front-again.jpg"
        other = folder / "back.jpg"
        Image.new("RGB", (40, 60), (10, 20, 30)).save(first)
        Image.new("RGB", (80, 120), (10, 20, 30)).save(second)
        Image.new("RGB", (40, 60), (200, 10, 10)).save(other)
        kept = unique_images([first, second, other])
        assert kept == [first, other]


def test_the_same_shot_at_two_exposures_is_posted_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        shot = Image.new("RGB", (200, 300), (60, 60, 60))
        ImageDraw.Draw(shot).rectangle((40, 60, 160, 240), fill=(190, 185, 180))
        front = folder / "front.jpg"
        relit = folder / "front-relit.jpg"
        shot.save(front, quality=92)
        shot.point(lambda level: min(255, level + 28)).save(relit, quality=80)

        # Same two tones, different composition: a real second angle, not a copy.
        back_shot = Image.new("RGB", (200, 300), (190, 185, 180))
        ImageDraw.Draw(back_shot).rectangle((0, 120, 200, 300), fill=(60, 60, 60))
        back = folder / "back.jpg"
        back_shot.save(back, quality=92)

        assert unique_images([front, relit, back]) == [front, back]


def test_shopify_extracts_only_own_product_images_and_ignores_recommendations() -> None:
    html = """
    <div class="predictive-search">
      <div class="product-item__image">
        <img src="//www.linzi.com/cdn/shop/files/UPGRADEBROWNSUEDE.jpg" />
      </div>
    </div>
    <div class="product-media-container" data-product-media-list>
      <div class="product__photo" data-image-src="//www.linzi.com/cdn/shop/files/SCHEDULEMOCHA_1.jpg?v=1"></div>
      <div class="product__photo" data-image-src="//www.linzi.com/cdn/shop/files/SCHEDULEMOCHA_2.jpg?v=1"></div>
      <div class="product__photo" data-image-src="//www.linzi.com/cdn/shop/files/SCHEDULEMOCHA_3.jpg?v=1"></div>
    </div>
    <section class="recommendations">
      <img src="//www.linzi.com/cdn/shop/files/SELECTEDBLACKPU.jpg" />
    </section>
    """
    page_url = "https://www.linzi.com/en-de/products/schedule-mocha"
    ordered = ordered_photos(html, "linzi", page_url)
    assert len(ordered) == 3
    assert all("SCHEDULEMOCHA" in u for u in ordered)
    assert not any("UPGRADE" in u or "SELECTED" in u for u in ordered)


def test_keep_single_product_filters_by_slug_and_anchor_url() -> None:
    from core.gallery import keep_single_product

    urls = [
        "https://www.linzi.com/cdn/shop/files/UPGRADEBROWNSUEDE.jpg",
        "https://www.linzi.com/cdn/shop/files/SCHEDULEMOCHA_1.jpg",
        "https://www.linzi.com/cdn/shop/files/SCHEDULEMOCHA_2.jpg",
        "https://www.linzi.com/cdn/shop/files/SELECTEDBLACKPU.jpg",
    ]
    # Filter by page slug
    filtered_by_slug = keep_single_product(urls, page_url="https://www.linzi.com/en-de/products/schedule-mocha")
    assert len(filtered_by_slug) == 2
    assert all("SCHEDULEMOCHA" in u for u in filtered_by_slug)

    # Filter by anchor url
    anchor = "https://www.linzi.com/cdn/shop/files/SCHEDULEMOCHA_1.jpg?v=123"
    filtered_by_anchor = keep_single_product(urls, anchor_url=anchor)
    assert len(filtered_by_anchor) == 2
    assert all("SCHEDULEMOCHA" in u for u in filtered_by_anchor)


def test_zara_modern_still_life_and_watermarks() -> None:
    e1 = "https://static.zara.net/assets/public/125a/fcc7/b4b1466ca273/634afa32db16/02756113622-e1.jpg"
    e2 = "https://static.zara.net/assets/public/f4f6/51a3/264f429db2bc/0af9980d9db6/02756113622-e2.jpg"
    e3 = "https://static.zara.net/assets/public/b1a8/807c/d56c4498a5f9/5af454189d60/02756113622-e3.jpg"
    watermark = "https://static.zara.net/contents/cm/watermarks/looks-ctx/simple-large@en_GB_0.jpg"

    assert is_product_angle(e1)
    assert is_product_angle(e2)
    assert is_product_angle(e3)
    assert not is_product_angle(watermark)

    html = f"""
    {{"kind":"full","path":"/contents/cm/watermarks/looks-ctx/simple-large@en_GB_0.jpg","name":"simple-large@en_GB_0"}}
    {{"kind":"full","path":"/assets/public/70fc/bdc3/627d4b87b2c2/cfb0c369e050/02756113622-p.jpg","name":"02756113622-p"}}
    {{"kind":"other","path":"/assets/public/4da6/0ee3/3a9141ecad62/76e97b6c53af/02756113622-a1.jpg","name":"02756113622-a1"}}
    {{"kind":"plain","path":"/assets/public/125a/fcc7/b4b1466ca273/634afa32db16/02756113622-e1.jpg","name":"02756113622-e1"}}
    {{"kind":"plain","path":"/assets/public/f4f6/51a3/264f429db2bc/0af9980d9db6/02756113622-e2.jpg","name":"02756113622-e2"}}
    {{"kind":"plain","path":"/assets/public/b1a8/807c/d56c4498a5f9/5af454189d60/02756113622-e3.jpg","name":"02756113622-e3"}}
    """
    ordered = ordered_photos(html, "zara", "https://www.zara.com/de/en/item-p02756113.html")
    assert not any("watermarks" in u for u in ordered)
    assert any("02756113622-p" in u for u in ordered)
    assert any("02756113622-e1" in u for u in ordered)
    assert any("02756113622-e2" in u for u in ordered)
    assert any("02756113622-e3" in u for u in ordered)


def test_zara_colorway_isolation_drops_other_color_photos() -> None:
    from core.gallery import keep_single_product, ordered_photos

    pink_photos = [
        "https://static.zara.net/assets/public/70fc/bdc3/627d4b87b2c2/cfb0c369e050/02756113622-p.jpg",
        "https://static.zara.net/assets/public/4da6/0ee3/3a9141ecad62/76e97b6c53af/02756113622-a1.jpg",
        "https://static.zara.net/assets/public/125a/fcc7/b4b1466ca273/634afa32db16/02756113622-e1.jpg",
        "https://static.zara.net/assets/public/f4f6/51a3/264f429db2bc/0af9980d9db6/02756113622-e2.jpg",
    ]
    white_photos = [
        "https://static.zara.net/assets/public/99aa/bbcc/334455667788/112233445566/02756113250-p.jpg",
    ]
    all_photos = pink_photos + white_photos

    # 1. Filtered with anchor URL
    anchor = pink_photos[0]
    filtered_anchor = keep_single_product(all_photos, anchor_url=anchor)
    assert len(filtered_anchor) == 4
    assert not any("02756113250" in u for u in filtered_anchor)
    assert all("02756113622" in u for u in filtered_anchor)

    # 2. Filtered with page URL only (no anchor provided)
    page_url = "https://www.zara.com/de/en/voluminous-sleeve-jumper-p02756113.html?v1=556206021"
    filtered_page = keep_single_product(all_photos, page_url=page_url)
    assert len(filtered_page) == 4
    assert not any("02756113250" in u for u in filtered_page)
    assert all("02756113622" in u for u in filtered_page)

    # 3. Via ordered_photos with HTML containing both colorways
    html = f"""
    {{"kind":"full","path":"/assets/public/70fc/bdc3/cfb0c369e050/02756113622-p.jpg","name":"02756113622-p"}}
    {{"kind":"plain","path":"/assets/public/125a/fcc7/634afa32db16/02756113622-e1.jpg","name":"02756113622-e1"}}
    {{"kind":"full","path":"/assets/public/99aa/bbcc/112233445566/02756113250-p.jpg","name":"02756113250-p"}}
    """
    ordered = ordered_photos(html, "zara", page_url, fallback=[pink_photos[0]])
    assert len(ordered) == 2
    assert not any("02756113250" in u for u in ordered)
    assert all("02756113622" in u for u in ordered)


def _zara_two_color_page() -> str:
    """Trimmed Zara page: white (250) listed first with 3 photos, red (632) with 4."""
    def media(code: str, names: list[str]) -> str:
        items = []
        for name, kind in names:
            items.append(
                f'{{"datatype":"xmedia","type":"image","kind":"{kind}",'
                f'"path":"/assets/public/aa/bb/04174878{code}-{name}","name":"04174878{code}-{name}"}}'
            )
        return ",".join(items)

    white = media("250", [("p", "full"), ("a1", "other"), ("e1", "plain")])
    red = media("632", [("p", "full"), ("a1", "other"), ("a2", "other"), ("e1", "plain")])
    return (
        '{"detail":{"reference":"04174378-I2026","colors":['
        f'{{"id":"250","hexCode":"#F4F6FA","productId":590144886,"name":"Blanc","xmedia":[{white}]}},'
        f'{{"id":"632","hexCode":"#D50030","productId":590144883,"name":"Rouge","xmedia":[{red}]}}'
        ']}}'
    )


def test_zara_keeps_the_page_selected_color_not_the_one_with_most_photos() -> None:
    page = "https://www.zara.com/fr/fr/t-shirt-interlock-manches-courtes-p04174378.html"
    ordered = ordered_photos(_zara_two_color_page(), "zara", page)
    assert len(ordered) == 3
    assert all("04174878250-" in url for url in ordered)


def test_zara_v1_in_the_link_picks_that_color() -> None:
    page = "https://www.zara.com/fr/fr/t-shirt-interlock-manches-courtes-p04174378.html?v1=590144883"
    ordered = ordered_photos(_zara_two_color_page(), "zara", page)
    assert len(ordered) == 4
    assert all("04174878632-" in url for url in ordered)


def test_zara_color_code_on_the_page_wins_over_list_order() -> None:
    page = "https://www.zara.com/fr/fr/t-shirt-interlock-manches-courtes-p04174378.html"
    html = _zara_two_color_page() + '{"colorCode":"632"}'
    ordered = ordered_photos(html, "zara", page)
    assert all("04174878632-" in url for url in ordered)

