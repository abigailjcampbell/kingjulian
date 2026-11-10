"""IANA timezone helpers for Account.timezone."""
import re
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from django.core.exceptions import ValidationError

DEFAULT = "UTC"
# Offer only canonical region/city names; the tz database also ships legacy aliases (US/Eastern,
# EST5EDT, Etc/GMT+5, posix/...) that would only confuse a picker.
_REGION = re.compile(r"(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/")


@lru_cache(maxsize=1)
def timezone_choices() -> list[tuple[str, str]]:
    names = sorted(z for z in available_timezones() if _REGION.match(z))
    return [(DEFAULT, "UTC")] + [(n, n.replace("_", " ")) for n in names]


def _load(name) -> ZoneInfo:
    # ZoneInfoNotFoundError is a KeyError; bad shapes raise ValueError/TypeError/OSError.
    return ZoneInfo(name)


def load_zone(name) -> ZoneInfo:
    """The zone for `name`, or UTC if the stored value is missing or no longer loads (never raises)."""
    try:
        return _load(name)
    except (KeyError, ValueError, TypeError, OSError):
        return ZoneInfo(DEFAULT)


def validate_timezone(name):
    try:
        _load(name)
    except (KeyError, ValueError, TypeError, OSError):
        raise ValidationError(f"'{name}' is not a known timezone (use a name like America/Chicago).")
