import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict


class ReviewLogger:
    """Writes structured log entries to reviews/{slug}/search_log.md."""

    def __init__(self, review_dir: Path):
        self._log_path = review_dir / "search_log.md"
        if not self._log_path.exists():
            self._log_path.write_text("# Search Log\n\n")

    def log(
        self,
        event_type: str,
        stage: str,
        *,
        query: Optional[str] = None,
        database: Optional[str] = None,
        hits: Optional[int] = None,
        token_count: Optional[int] = None,
        notes: Optional[str] = None,
        extra: Optional[Dict] = None,
    ) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts = [f"**[{ts}]** `{event_type}` | stage={stage}"]
        if database:
            parts.append(f"db={database}")
        if query:
            parts.append(f"query=`{query}`")
        if hits is not None:
            parts.append(f"hits={hits}")
        if token_count is not None:
            parts.append(f"tokens={token_count}")
        if notes:
            parts.append(notes)
        if extra:
            parts.append(json.dumps(extra))

        line = " | ".join(parts) + "\n"
        with self._log_path.open("a") as f:
            f.write(line)
