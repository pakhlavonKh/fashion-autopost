"""Parse an admin's requested publication time.

Times are interpreted in the publishing timezone (the same one used by the
regular schedule). A clock time that already passed today is moved to tomorrow.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import zoneinfo


IMMEDIATE_WORDS = {"сейчас", "сейчас же", "сразу", "немедленно", "now", "asap"}
_GRACE = timedelta(minutes=3)


@dataclass(frozen=True)
class PublishTime:
    """Resolved publication moment in UTC, plus an optional note for the admin."""

    when: datetime
    note: str = ""


def parse_publish_time(text: str, now: datetime, timezone_name: str) -> PublishTime:
    """Parse a free-form time reply.

    Raises ValueError with a Russian explanation when the text is not a time
    or the explicit date is already in the past.
    """
    tz = _zone(timezone_name)
    current = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    cleaned = _normalize(text)
    if not cleaned:
        raise ValueError(_hint())

    if cleaned in IMMEDIATE_WORDS:
        return PublishTime(when=current.astimezone(zoneinfo.ZoneInfo("UTC")), note="сейчас")

    tomorrow = False
    if cleaned.startswith("завтра"):
        tomorrow = True
        cleaned = cleaned[len("завтра"):].strip()
        cleaned = re.sub(r"^(в|на|к)\s+", "", cleaned)
        if not cleaned:
            raise ValueError("Напишите время, например: завтра 18:30")

    dated = _parse_dated(cleaned, current, tz)
    if dated is not None:
        if tomorrow:
            raise ValueError("Укажите либо «завтра 18:30», либо дату и время, например 29.09 18:30")
        return _ensure_future(dated, current, allow_next_day=False)

    clock = _parse_clock(cleaned)
    if clock is None:
        raise ValueError(_hint())

    hour, minute = clock
    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if tomorrow:
        candidate = candidate + timedelta(days=1)
        return PublishTime(when=candidate.astimezone(zoneinfo.ZoneInfo("UTC")))
    return _ensure_future(candidate, current, allow_next_day=True)


def _zone(timezone_name: str) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(timezone_name)
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


def _normalize(text: str) -> str:
    cleaned = text.strip().lower().replace("ё", "е")
    cleaned = cleaned.replace("—", " ").replace("–", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^(в|на|к)\s+", "", cleaned)
    return cleaned.strip(" .,!?:;")


def _parse_clock(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    else:
        found = re.findall(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)", text)
        if len(found) != 1:
            return None
        hour, minute = int(found[0][0]), int(found[0][1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Время должно быть в пределах 00:00–23:59")
    return hour, minute


def _parse_dated(text: str, current: datetime, tz: zoneinfo.ZoneInfo) -> datetime | None:
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2})[:.](\d{2})", text)
    dotted = re.fullmatch(
        r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?[ t](\d{1,2})[:.](\d{2})",
        text,
    )
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        hour, minute = int(iso.group(4)), int(iso.group(5))
    elif dotted:
        day, month = int(dotted.group(1)), int(dotted.group(2))
        year_raw = dotted.group(3)
        hour, minute = int(dotted.group(4)), int(dotted.group(5))
        if year_raw is None:
            year = current.year
        else:
            year = int(year_raw)
            if year < 100:
                year += 2000
    else:
        return None

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Время должно быть в пределах 00:00–23:59")
    try:
        return datetime(year, month, day, hour, minute, tzinfo=tz)
    except ValueError as exc:
        raise ValueError("Такой даты не существует. Напишите, например, 29.09 18:30") from exc


def _ensure_future(candidate: datetime, current: datetime, allow_next_day: bool) -> PublishTime:
    utc = zoneinfo.ZoneInfo("UTC")
    if candidate > current:
        return PublishTime(when=candidate.astimezone(utc))
    if current - candidate <= _GRACE:
        return PublishTime(when=current.astimezone(utc))
    if allow_next_day:
        nxt = candidate + timedelta(days=1)
        return PublishTime(
            when=nxt.astimezone(utc),
            note="Сегодня это время уже прошло, поэтому пост выйдет завтра.",
        )
    raise ValueError("Это время уже прошло. Укажите будущее время.")


def _hint() -> str:
    return "Не понял время. Напишите, например: 18:30, завтра 18:30, 29.09 18:30 или «сейчас»."
