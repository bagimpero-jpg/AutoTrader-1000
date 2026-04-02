from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATE: dict[str, Any] = {
    "open_positions": [],
    "pending_orders": [],
    "last_update": "",
    "daily_pnl": 0.0,
    "session_data": {},
}

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATE_FILE = STATE_DIR / "bot_state.json"


class StateManager:
    """Persistent JSON state manager with atomic writes and crash-recovery support."""

    def __init__(self, state_path: Path = STATE_FILE) -> None:
        self._path = state_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core I/O
    # ------------------------------------------------------------------

    def save_state(self, data: dict[str, Any]) -> None:
        """Atomically write state to disk (write tmp then rename)."""
        try:
            data["last_update"] = datetime.now(timezone.utc).isoformat()

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, default=str)
                os.replace(tmp_path, str(self._path))
            except BaseException:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

            logger.debug("State saved to %s", self._path)

        except Exception:
            logger.exception("Failed to save state")
            raise

    def load_state(self) -> dict[str, Any]:
        """Load state from disk; return defaults on missing or corrupt file."""
        try:
            if not self._path.exists():
                logger.info("State file not found, returning defaults")
                return {**DEFAULT_STATE}

            with open(self._path, "r", encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)

            # Ensure every expected key exists
            for key, default_value in DEFAULT_STATE.items():
                data.setdefault(key, default_value if not isinstance(default_value, (list, dict)) else type(default_value)())

            logger.debug("State loaded from %s", self._path)
            return data

        except (json.JSONDecodeError, OSError):
            logger.exception("Corrupt or unreadable state file, returning defaults")
            return {**DEFAULT_STATE}

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    def update_position(self, ticket: int, data: dict[str, Any]) -> None:
        """Update (or insert) a position entry identified by ticket."""
        state = self.load_state()
        positions: list[dict[str, Any]] = state["open_positions"]

        for pos in positions:
            if pos.get("ticket") == ticket:
                pos.update(data)
                break
        else:
            data["ticket"] = ticket
            positions.append(data)

        self.save_state(state)
        logger.info("Position %d updated in state", ticket)

    def remove_position(self, ticket: int) -> None:
        """Remove a closed position from state."""
        state = self.load_state()
        before = len(state["open_positions"])
        state["open_positions"] = [
            p for p in state["open_positions"] if p.get("ticket") != ticket
        ]
        removed = before - len(state["open_positions"])

        self.save_state(state)
        if removed:
            logger.info("Position %d removed from state", ticket)
        else:
            logger.warning("Position %d not found in state for removal", ticket)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def get_recovery_data(self) -> dict[str, Any]:
        """Return positions that need reconciliation after a restart."""
        state = self.load_state()
        return {
            "open_positions": state.get("open_positions", []),
            "pending_orders": state.get("pending_orders", []),
            "last_update": state.get("last_update", ""),
        }
