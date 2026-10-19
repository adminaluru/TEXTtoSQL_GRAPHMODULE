"""Turn a date condition from a question into an exact period (D-007).

Rules (decision D-007, 2026-10-15):

- Business time zone is US Central (America/Chicago). Stored timestamps are
  already in that local time, so no conversion is applied; only "today"
  (for relative periods) is taken from the Central-time clock.
- A stated end date is included in full: "1 to 30 April" covers all of
  30 April. Periods compile as  start <= value < day after end.

Only unambiguous forms are accepted. Anything else is refused with the
supported forms, never guessed: weeks (Monday or Sunday start?), rolling
windows ("last 30 days": is today included?) and months without a year.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from planning.errors import UnsupportedPredicateError

BUSINESS_TIME_ZONE = ZoneInfo("America/Chicago")

SUPPORTED_FORMS = (
    "I can't tell which dates “{value}” means. Please use a specific day "
    "(2026-04-20 or 20 April 2026), month (April 2026), quarter (Q2 2026), year "
    "(2026), a range (2026-04-01 to 2026-04-30), before/after/since a date, or "
    "today, yesterday, this/last month, this/last quarter or this/last year."
)
REVERSED_RANGE = "The period “{value}” ends before it starts."

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9


@dataclass(frozen=True)
class Period:
    """Inclusive calendar days; None means open-ended."""
    start: date | None
    end: date | None

    @property
    def start_at(self) -> datetime | None:
        return datetime.combine(self.start, time.min) if self.start else None

    @property
    def end_before(self) -> datetime | None:
        # Exclusive bound: midnight after the last included day.
        return datetime.combine(self.end + timedelta(days=1), time.min) if self.end else None

    def describe(self) -> str:
        if self.start and self.end:
            span = (self.start.isoformat() if self.start == self.end
                    else f"{self.start.isoformat()} to {self.end.isoformat()}")
        elif self.start:
            span = f"from {self.start.isoformat()} onwards"
        else:
            span = f"up to and including {self.end.isoformat()}"
        return f"{span} (US Central time, end date included)"


def business_today(now: datetime | None = None) -> date:
    """Calendar date in US Central time (an aware `now` is converted)."""
    moment = now.astimezone(BUSINESS_TIME_ZONE) if now else datetime.now(BUSINESS_TIME_ZONE)
    return moment.date()


def parse_period(text: str, today: date) -> Period:
    value = " ".join(str(text).strip().lower().replace(",", " ").split())
    try:
        period = _parse(value, today)
    except ValueError:
        period = None
    if period is None:
        raise UnsupportedPredicateError(SUPPORTED_FORMS.format(value=str(text).strip()))
    if period.start and period.end and period.start > period.end:
        raise UnsupportedPredicateError(REVERSED_RANGE.format(value=str(text).strip()))
    return period


def _parse(value: str, today: date) -> Period | None:
    for pattern in (r"^between (.+) and (.+)$", r"^from (.+) (?:to|until|through) (.+)$",
                    r"^(.+) (?:to|until|through) (.+)$", r"^(.+) - (.+)$"):
        m = re.match(pattern, value)
        if m:
            first, last = _single(m.group(1), today), _single(m.group(2), today)
            return Period(first.start, last.end) if first and last else None
    m = re.match(r"^(before|after|since|from|until|through|up to|on or before|on or after) (.+)$", value)
    if m:
        word, inner = m.group(1), _single(m.group(2), today)
        if inner is None:
            return None
        if word == "before":
            return Period(None, inner.start - timedelta(days=1))
        if word == "after":
            return Period(inner.end + timedelta(days=1), None)
        if word in ("since", "from", "on or after"):
            return Period(inner.start, None)
        return Period(None, inner.end)            # until / through / up to / on or before
    m = re.match(r"^(?:in|on|during) (.+)$", value)
    return _single(m.group(1) if m else value, today)


def _single(value: str, today: date) -> Period | None:
    value = value.strip()
    relative = _relative(value, today)
    if relative:
        return relative
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", value):
        day = date(int(m[1]), int(m[2]), int(m[3]))
        return Period(day, day)
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})", value):
        return _month(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d{4})", value):
        return Period(date(int(m[1]), 1, 1), date(int(m[1]), 12, 31))
    if m := re.fullmatch(r"q([1-4]) (\d{4})|(\d{4}) q([1-4])", value):
        quarter, year = (int(m[1]), int(m[2])) if m[1] else (int(m[4]), int(m[3]))
        return _quarter(year, quarter)
    if m := re.fullmatch(r"([a-z]+) (\d{4})", value):
        return _month(int(m[2]), MONTHS[m[1]]) if m[1] in MONTHS else None
    if m := re.fullmatch(r"(\d{1,2}) ([a-z]+) (\d{4})", value):
        return _day(int(m[3]), m[2], int(m[1]))
    if m := re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{4})", value):
        return _day(int(m[3]), m[1], int(m[2]))
    return None


def _relative(value: str, today: date) -> Period | None:
    if value == "today":
        return Period(today, today)
    if value == "yesterday":
        day = today - timedelta(days=1)
        return Period(day, day)
    if value in ("this month", "last month"):
        year, month = today.year, today.month
        if value == "last month":
            year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        return _month(year, month)
    if value in ("this quarter", "last quarter"):
        year, quarter = today.year, (today.month - 1) // 3 + 1
        if value == "last quarter":
            year, quarter = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
        return _quarter(year, quarter)
    if value in ("this year", "last year"):
        year = today.year - (value == "last year")
        return Period(date(year, 1, 1), date(year, 12, 31))
    return None


def _month(year: int, month: int) -> Period:
    return Period(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))


def _quarter(year: int, quarter: int) -> Period:
    first = 3 * (quarter - 1) + 1
    return Period(date(year, first, 1), _month(year, first + 2).end)


def _day(year: int, month_name: str, day: int) -> Period | None:
    if month_name not in MONTHS:
        return None
    value = date(year, MONTHS[month_name], day)
    return Period(value, value)
