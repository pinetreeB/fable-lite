from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

from core.ledger_schema import JsonObject, JsonValue
from core.scorecard import SessionIdentity


@dataclass(frozen=True, slots=True)
class ObservationReplay:
    observations: dict[str, JsonObject]
    complete: bool


def load_observations(root: Path) -> ObservationReplay:
    observations: dict[str, JsonObject] = {}
    complete = True
    try:
        paths = tuple((root / ".fable-lite" / "agents").glob("*.jsonl"))
    except OSError:
        return ObservationReplay(observations, False)
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            complete = False
            continue
        for line in lines:
            complete = _observe_line(observations, line) and complete
    return ObservationReplay(observations, complete)


def _observe_line(observations: dict[str, JsonObject], line: str) -> bool:
    try:
        raw: JsonValue = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(raw, dict):
        return False
    values = tuple(raw.get(field) for field in ("host", "session_id", "agent"))
    timestamp = raw.get("timestamp") or raw.get("occurred_at")
    if not all(isinstance(value, str) and value for value in values):
        return True
    if not isinstance(timestamp, str):
        return False
    try:
        occurred_at = datetime.fromisoformat(timestamp)
    except ValueError:
        return False
    if occurred_at.tzinfo is None:
        return False
    occurred_at = occurred_at.astimezone(UTC)
    identity = SessionIdentity(str(values[0]), str(values[1]), str(values[2]))
    key = identity.agent_key
    row = observations.setdefault(
        key,
        {
            "host": identity.host,
            "session_id": identity.session_id,
            "agent": identity.agent,
            "first_at": occurred_at.isoformat(),
            "last_at": occurred_at.isoformat(),
            "verification_ok": 0,
            "verification_fail": 0,
        },
    )
    row["first_at"] = min(str(row["first_at"]), occurred_at.isoformat())
    row["last_at"] = max(str(row["last_at"]), occurred_at.isoformat())
    if raw.get("event") != "verification" or not isinstance(raw.get("success"), bool):
        return True
    field = "verification_ok" if raw["success"] is True else "verification_fail"
    row[field] = _integer(row.get(field)) + 1
    return True


def _integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
