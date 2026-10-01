"""The style profile must come from the posts standing in the Telegram channel."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

from core.channel_history import (
    ChannelPost,
    channel_username,
    fetch_channel_posts,
    parse_post_caption,
)

CAPTION = """Прямой жакет с поясом-85$
Размеры от XS до XL.
Цвет: темно-синий. Свободный крой.

Европейское качество
Обращаться: @nigora_7
Тел:+998998484044
Отзывы: @otzivi_fashbou
Наш Instagram:
https://www.instagram.com/fashionnestboutique
"""


def _page(posts: list[tuple[int, str]]) -> str:
    """Markup of the public channel page, with the caption div nested as Telegram nests it."""
    blocks = []
    for post_id, caption in posts:
        body = caption.replace("\n", "<br/>").replace("$", "&#036;")
        blocks.append(
            f'<div class="tgme_widget_message" data-post="fashionalleyb/{post_id}">'
            f'<a href="https://t.me/fashionalleyb/{post_id}?single"></a>'
            f'<div class="tgme_widget_message_text js-message_text" dir="auto">'
            f'<div class="tgme_widget_message_text js-message_text" dir="auto">{body}</div></div>'
            f"</div>"
        )
    return "<html><body>" + "".join(blocks) + "</body></html>"


def test_caption_gives_the_garment_name_description_and_store_price() -> None:
    """The caption carries the selling price, so the markup comes back off."""
    post = parse_post_caption(CAPTION, markup=Decimal("40"))

    assert post is not None
    assert post.title == "Прямой жакет с поясом"
    assert post.price_original == Decimal("45")
    assert "Размеры от XS до XL." in post.description
    assert "Цвет: темно-синий" in post.description
    assert "nigora" not in post.description
    assert "instagram" not in post.description.lower()


def test_caption_without_a_price_still_describes_the_style() -> None:
    post = parse_post_caption("Трикотажный кейп\nРазмеры XS-S, M-L. Цвет: серый.")

    assert post is not None
    assert post.title == "Трикотажный кейп"
    assert post.price_original is None
    assert "серый" in post.description


def test_captions_that_say_nothing_about_a_garment_are_skipped() -> None:
    assert parse_post_caption("64$\nЦена со скидкой!") is None
    assert parse_post_caption("Обращаться: @nigora_7") is None
    assert parse_post_caption("") is None


def test_channel_username_reads_the_public_handle() -> None:
    assert channel_username("-1004363309099, @fashionalleyb") == "fashionalleyb"
    assert channel_username("https://t.me/fashionalleyb") == "fashionalleyb"
    assert channel_username("-1001246015920") is None
    assert channel_username(None) is None


def _client_returning(pages: list[str]):
    def fake_client(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client
        responses = []
        for page in pages:
            response = MagicMock()
            response.status_code = 200
            response.text = page
            responses.append(response)
        client.get.side_effect = responses
        return client

    return fake_client


def test_posts_are_read_from_the_channel_page() -> None:
    page = _page([(205933, CAPTION), (205925, "Сабашка-68$\nРазмеры от XS до XL. Цвет: бежевый.")])

    with patch("core.channel_history.httpx.Client", side_effect=_client_returning([page])):
        posts = fetch_channel_posts("@fashionalleyb", limit=2, markup=Decimal("40"))

    assert [p.title for p in posts] == ["Прямой жакет с поясом", "Сабашка"]
    assert posts[1].price_original == Decimal("28")


def test_paging_walks_back_until_the_limit_is_reached() -> None:
    first = _page([(300, "Пальто-120$\nРазмеры от XS до XL.")])
    second = _page([(200, "Джемпер-70$\nРазмеры от XS до XL.")])

    with patch("core.channel_history.httpx.Client", side_effect=_client_returning([first, second])) as client:
        posts = fetch_channel_posts("@fashionalleyb", limit=2, markup=Decimal("40"))

    assert [p.title for p in posts] == ["Пальто", "Джемпер"]
    assert client.call_count == 1


def test_a_channel_that_cannot_be_read_yields_nothing() -> None:
    def refusing_client(*_args, **_kwargs):
        client = MagicMock()
        client.__enter__.return_value = client
        response = MagicMock()
        response.status_code = 404
        response.text = ""
        client.get.return_value = response
        return client

    with patch("core.channel_history.httpx.Client", side_effect=refusing_client):
        assert fetch_channel_posts("@missing_channel") == []

    assert fetch_channel_posts("-1004363309099") == []


def test_profile_prefers_the_channel_over_the_stored_history() -> None:
    """Posts made before this bot existed must shape the selection too."""
    from core.similarity import analyze_channel_history

    repo = MagicMock()
    repo.get_recent_published_products.return_value = []
    channel_posts = [
        ChannelPost(title="Трикотажный кейп", description="Цвет: серый", price_original=Decimal("45")),
        ChannelPost(title="Прямой жакет с поясом", description="Цвет: синий", price_original=Decimal("55")),
    ]

    with patch("core.similarity.fetch_channel_posts", return_value=channel_posts):
        profile = analyze_channel_history(repo, channel="@fashionalleyb", markup=Decimal("40"))

    assert profile.total_sample == 2
    assert "жакет" in profile.top_keywords
    assert profile.median_price == 55.0
    repo.get_recent_published_products.assert_not_called()


def test_stored_history_is_used_when_the_channel_is_silent() -> None:
    from core.similarity import analyze_channel_history

    record = MagicMock()
    record.category = "jackets"
    record.source = "mango"
    record.price_original = Decimal("50")
    record.title = "Жакет с баской"
    record.description = "Цвет: черный"
    repo = MagicMock()
    repo.get_recent_published_products.return_value = [record]

    with patch("core.similarity.fetch_channel_posts", return_value=[]):
        profile = analyze_channel_history(repo, channel="@fashionalleyb")

    assert profile.total_sample == 1
    assert "жакет" in profile.top_keywords


def test_candidates_matching_the_channel_style_rank_first() -> None:
    from adapters.base import RawProduct
    from core.similarity import rank_products_by_channel_similarity

    def product(external_id: str, title: str, price: str) -> RawProduct:
        return RawProduct(
            external_id=external_id,
            source="mango",
            title=title,
            price=Decimal(price),
            currency="EUR",
            photo_url="https://example.com/a.jpg",
            product_url="https://example.com/p/a",
            in_stock=True,
        )

    repo = MagicMock()
    repo.get_paused_brands.return_value = set()
    channel_posts = [
        ChannelPost(title="Трикотажный кейп", description="Цвет: серый", price_original=Decimal("45")),
        ChannelPost(title="Трикотажный джемпер", description="Цвет: серый", price_original=Decimal("45")),
    ]
    candidates = [
        product("far", "Rubber rain boots", "44"),
        product("near", "Трикотажный джемпер оверсайз", "45"),
    ]

    with patch("core.similarity.fetch_channel_posts", return_value=channel_posts):
        ranked = rank_products_by_channel_similarity(
            candidates,
            repo,
            channel="@fashionalleyb",
            markup=Decimal("40"),
        )

    assert [p.external_id for p, _ in ranked] == ["near", "far"]
    assert ranked[0][1] > ranked[1][1]
