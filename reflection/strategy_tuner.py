from __future__ import annotations

import json
import logging
import os
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _BASE_DIR / "config" / "settings.yaml"
_PARAM_HISTORY_PATH = _BASE_DIR / "config" / "param_history.json"

# Safety limits — these cannot be exceeded by any adjustment.
_SAFETY_LIMITS: dict[str, dict[str, float]] = {
    "min_rr": {"min": 1.5},
    "risk_percent": {"max": 1.5},
    "max_daily_loss_percent": {"max": 10.0},
    "max_total_loss_percent": {"max": 20.0},
}


class StrategyTuner:
    """Loads, validates, adjusts, and checkpoints trading strategy parameters."""

    def __init__(
        self,
        settings_path: str | Path | None = None,
        history_path: str | Path | None = None,
    ) -> None:
        self._settings_path = Path(settings_path) if settings_path else _SETTINGS_PATH
        self._history_path = Path(history_path) if history_path else _PARAM_HISTORY_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_current_params(self) -> dict:
        """Load the current parameter set from config/settings.yaml."""
        try:
            import yaml
            with open(self._settings_path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            logger.exception("Failed to load settings from %s", self._settings_path)
            return {}

    def apply_adjustments(self, proposals: dict) -> dict:
        """Apply proposed adjustments with safety-limit enforcement and create a checkpoint."""
        try:
            current = self.load_current_params()
            old_params = deepcopy(current)
            checkpoint_id = self._save_checkpoint(old_params)

            trading = current.setdefault("trading", {})
            smc = current.setdefault("smc", {})

            for key, value in proposals.items():
                resolved = self._resolve_value(key, value, trading, smc)
                if resolved is None:
                    continue
                target_section, param_key, new_val = resolved
                new_val = self._enforce_safety(param_key, new_val)
                target_section[param_key] = new_val

            self._save_settings(current)
            logger.info("Applied adjustments (checkpoint %s)", checkpoint_id)
            return {"checkpoint_id": checkpoint_id, "params": current}
        except Exception:
            logger.exception("apply_adjustments failed")
            return {}

    def create_backtest_comparison(
        self,
        old_params: dict,
        new_params: dict,
        trade_history: list[dict],
    ) -> dict:
        """Build a side-by-side comparison dict for old vs new parameters."""
        try:
            changed_keys: list[str] = []
            self._diff_dicts(old_params, new_params, prefix="", out=changed_keys)

            old_trades = len(trade_history)
            wins = sum(1 for t in trade_history if (t.get("result_pips") or 0) > 0)
            total_pnl = sum(t.get("result_pips") or 0 for t in trade_history)

            return {
                "old_params_snapshot": old_params,
                "new_params_snapshot": new_params,
                "changed_keys": changed_keys,
                "historical_trades_evaluated": old_trades,
                "historical_win_rate": (wins / old_trades * 100) if old_trades else 0,
                "historical_total_pnl_pips": total_pnl,
                "note": (
                    "This comparison is indicative only. "
                    "Forward-test the new params before committing."
                ),
            }
        except Exception:
            logger.exception("create_backtest_comparison failed")
            return {}

    def rollback(self, checkpoint_id: str) -> dict:
        """Revert settings to a previously saved checkpoint."""
        try:
            history = self._load_history()
            checkpoint = None
            for entry in history:
                if entry.get("checkpoint_id") == checkpoint_id:
                    checkpoint = entry
                    break
            if checkpoint is None:
                logger.warning("Checkpoint %s not found", checkpoint_id)
                return {}

            params = checkpoint["params"]
            self._save_settings(params)
            logger.info("Rolled back to checkpoint %s", checkpoint_id)
            return params
        except Exception:
            logger.exception("rollback failed")
            return {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_value(
        self, key: str, value: Any, trading: dict, smc: dict,
    ) -> tuple[dict, str, Any] | None:
        """Map a proposal key/value to a (section, param_key, new_value) tuple."""
        try:
            if key in trading:
                return (trading, key, self._parse_numeric(value, trading.get(key)))
            if key in smc:
                return (smc, key, self._parse_numeric(value, smc.get(key)))
            # Special meta-keys produced by SelfReflection are stored as-is.
            if key in ("avoid_setups", "prefer_setups", "reduce_session_exposure"):
                return (trading, key, value)
            return None
        except Exception:
            return None

    @staticmethod
    def _parse_numeric(value: Any, current: Any) -> Any:
        """Best-effort extraction of a numeric value from proposal strings."""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            for token in value.split():
                try:
                    return float(token)
                except ValueError:
                    continue
        return current

    @staticmethod
    def _enforce_safety(key: str, value: Any) -> Any:
        if key not in _SAFETY_LIMITS or not isinstance(value, (int, float)):
            return value
        limits = _SAFETY_LIMITS[key]
        if "min" in limits and value < limits["min"]:
            logger.warning("Safety limit: %s clamped from %s to min %s", key, value, limits["min"])
            return limits["min"]
        if "max" in limits and value > limits["max"]:
            logger.warning("Safety limit: %s clamped from %s to max %s", key, value, limits["max"])
            return limits["max"]
        return value

    def _save_checkpoint(self, params: dict) -> str:
        checkpoint_id = uuid.uuid4().hex[:12]
        history = self._load_history()
        history.append({
            "checkpoint_id": checkpoint_id,
            "timestamp": datetime.utcnow().isoformat(),
            "params": params,
        })
        self._save_history(history)
        return checkpoint_id

    def _load_history(self) -> list[dict]:
        try:
            if self._history_path.exists():
                with open(self._history_path, "r") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
        except Exception:
            logger.exception("Failed to load param history")
        return []

    def _save_history(self, history: list[dict]) -> None:
        try:
            os.makedirs(self._history_path.parent, exist_ok=True)
            with open(self._history_path, "w") as f:
                json.dump(history, f, indent=2)
        except Exception:
            logger.exception("Failed to save param history")

    def _save_settings(self, params: dict) -> None:
        try:
            import yaml
            os.makedirs(self._settings_path.parent, exist_ok=True)
            with open(self._settings_path, "w") as f:
                yaml.dump(params, f, default_flow_style=False, sort_keys=False)
        except Exception:
            logger.exception("Failed to save settings")

    def _diff_dicts(
        self, old: dict, new: dict, prefix: str, out: list[str],
    ) -> None:
        all_keys = set(old) | set(new)
        for k in sorted(all_keys):
            full_key = f"{prefix}.{k}" if prefix else k
            old_v = old.get(k)
            new_v = new.get(k)
            if isinstance(old_v, dict) and isinstance(new_v, dict):
                self._diff_dicts(old_v, new_v, full_key, out)
            elif old_v != new_v:
                out.append(f"{full_key}: {old_v!r} -> {new_v!r}")
