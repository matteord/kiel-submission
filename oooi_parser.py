import re
from datetime import date, datetime, timedelta, timezone
from bs4 import BeautifulSoup

EVENT_MAP = {
    'Gate Departure': 'out',
    'Takeoff': 'off',
    'Landing': 'on',
    'Gate Arrival': 'in',
}

# Unambiguous abbreviations used on the example page. Expand only when verified.
TZ_OFFSETS = {
    'UTC': 0, 'GMT': 0,
    'EST': -5, 'EDT': -4,
    'PST': -8, 'PDT': -7,
    'MST': -7, 'MDT': -6,
    'CET': 1, 'CEST': 2,
    'BST': 1, 'EET': 2, 'EEST': 3,
}
CLOCK_RE = re.compile(r'\b(\d{1,2}):(\d{2})\s*(AM|PM)\s+([A-Z]{2,5})\b')
DAY_RE = re.compile(r'\(\s*([+-]\d+)\s*\)')


def parse_page_clock(text, job, event):
    match = CLOCK_RE.search(text.replace('\xa0', ' '))
    if not match:
        raise ValueError(f'{event}: unrecognized time: {text!r}')
    hours, minutes, ampm, abbrev = match.groups()
    if abbrev not in TZ_OFFSETS:
        raise ValueError(f'{event}: unknown or ambiguous timezone {abbrev!r}')
    hour = int(hours)
    minute = int(minutes)
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise ValueError(f'{event}: invalid clock time')
    hour = (hour % 12) + (12 if ampm == 'PM' else 0)
    zone = timezone(timedelta(hours=TZ_OFFSETS[abbrev]))

    anchor_key = 'scheduled_out' if event in ('out', 'off') else 'scheduled_in'
    anchor_raw = job.get(anchor_key)
    if not anchor_raw:
        raise ValueError(f'{event}: job missing {anchor_key} date anchor')
    anchor = datetime.fromisoformat(str(anchor_raw).replace('Z', '+00:00'))
    if anchor.tzinfo is None or anchor.utcoffset() is None:
        raise ValueError(f'{event}: {anchor_key} has no timezone')
    anchor = anchor.astimezone(timezone.utc)

    # (+1) on this site denotes the day after the flight's local departure date.
    day_match = DAY_RE.search(text)
    if day_match:
        base_day = date.fromisoformat(str(job['flight_date']))
        possible_days = [base_day + timedelta(days=int(day_match.group(1)))]
    else:
        base_day = anchor.astimezone(zone).date()
        possible_days = [base_day + timedelta(days=d) for d in range(-2, 3)]

    candidates = [
        datetime.combine(d, datetime.min.time().replace(hour=hour, minute=minute), tzinfo=zone)
        .astimezone(timezone.utc)
        for d in possible_days
    ]
    instant = min(candidates, key=lambda stamp: abs(stamp - anchor))
    if abs(instant - anchor) > timedelta(hours=36):
        raise ValueError(f'{event}: parsed time too far from {anchor_key}')
    return instant.isoformat()


def extract_flight_table(html, job):
    soup = BeautifulSoup(html, 'html.parser')
    container = soup.select_one('div.flightPageDataTableContainer')
    if container is None:
        raise ValueError('Flight OOOI table not found in returned HTML')

    actual, scheduled = {}, {}
    for child in container.select('div.flightPageDataTimesChild'):
        heading = child.select_one('.flightPageDataActualTimeHeading')
        label = heading.get_text(' ', strip=True) if heading else ''
        event = EVENT_MAP.get(label)
        if not event:
            continue
        if event in actual or event in scheduled:
            raise ValueError(f'Duplicate {label} event')
        for css, target in (
            ('.flightPageDataActualTimeText', actual),
            ('.flightPageDataAncillaryText', scheduled),
        ):
            element = child.select_one(css)
            if not element:
                continue
            raw = element.get_text(' ', strip=True)
            # Ignore blank placeholders (for a flight not yet landed).
            if not CLOCK_RE.search(raw):
                continue
            target[('actual_' if target is actual else 'scheduled_') + event] = parse_page_clock(raw, job, event)

    if not actual:
        raise ValueError('No actual OOOI times found in flight table')
    stamps = [actual.get('actual_' + suffix) for suffix in ('out', 'off', 'on', 'in')]
    previous = None
    for stamp in stamps:
        if stamp is None:
            continue
        parsed = datetime.fromisoformat(stamp)
        if previous is not None and parsed < previous:
            raise ValueError('OUT/OFF/ON/IN not chronologically ordered')
        previous = parsed
    return actual, scheduled
