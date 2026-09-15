"""Small durable governing context, independent of operational tasks."""
from dataclasses import dataclass
import json
from pathlib import Path

if __package__:
    from .paths import STRATEGIC_CONTEXT_PATH
else:
    from paths import STRATEGIC_CONTEXT_PATH


class StrategicContextError(ValueError):
    """Strategic context is missing or malformed."""


@dataclass(frozen=True)
class StrategicContext:
    why: str
    direction: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.why, self.direction)):
            raise StrategicContextError('WHY and DIRECTION must be nonempty strings')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


def load_strategic_context(path=STRATEGIC_CONTEXT_PATH):
    """Read only; never migrate, repair, or fall back to a legacy registry."""
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_object)
        if not isinstance(payload, dict) or set(payload) != {'why', 'direction'}:
            raise ValueError('Expected exactly why and direction')
        return StrategicContext(**payload)
    except (OSError, ValueError, TypeError, RecursionError):
        raise StrategicContextError('Strategic context unavailable or invalid') from None
