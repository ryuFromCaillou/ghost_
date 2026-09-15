"""Shared durable-state defaults, resolved at import time without filesystem IO."""
from pathlib import Path
from typing import Final

INTEGRATIONS_ROOT: Final[Path] = Path.home() / 'ghost/integrations'
STATE_ROOT: Final[Path] = INTEGRATIONS_ROOT / 'state'
TODOIST_EXTERNAL_STATE_ROOT: Final[Path] = STATE_ROOT / 'external/todoist'
STRATEGIC_CONTEXT_PATH: Final[Path] = STATE_ROOT / 'strategic-context.json'
TODOIST_HISTORY_DB_PATH: Final[Path] = STATE_ROOT / 'todoist-history.sqlite3'
