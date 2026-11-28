"""Aura's runtime state: a small SQLite database in Aura's data folder (state.sqlite).

What it keeps, so a restart loses nothing silently:
- turns: every chat turn, approval continuation and Slack turn, with its lifecycle state;
- approvals: each approval Aura asked for, bound to its turn, conversation, tools and argument
  digest, and whether it was answered (so a retry can never run an action twice);
- records: Developer builds and vault tasks, saved before work starts and after every transition;
- events: bounded feeds of status lines (a build's live progress);
- meta: small values (the current conversation of each Aura, the last failure).

What it never keeps: secrets, tokens, or full tool payloads (file contents, diffs, mail). Records
hold metadata only; anything long is cut with an explicit marker.

WAL mode (readers never block the writer), a busy timeout, schema versioning with forward-only
migrations, and state transitions that are atomic compare-and-set updates.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
FEED_KEEP = 300  # status lines kept per feed
RECORDS_KEEP = 100  # finished records kept per kind
TEXT_CAP = 2_000  # characters of any one stored text field

MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
        """CREATE TABLE turns (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL, conversation TEXT NOT NULL,
            state TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0, tools INTEGER NOT NULL DEFAULT 0,
            chars INTEGER NOT NULL DEFAULT 0, error TEXT, started TEXT NOT NULL, finished TEXT)""",
        """CREATE INDEX turns_by_state ON turns (state)""",
        """CREATE TABLE approvals (
            id TEXT PRIMARY KEY, turn_id TEXT, conversation TEXT NOT NULL, interrupt_id TEXT NOT NULL,
            digest TEXT NOT NULL, tools TEXT NOT NULL, signature TEXT NOT NULL, state TEXT NOT NULL,
            reason TEXT, created TEXT NOT NULL, decided TEXT)""",
        """CREATE INDEX approvals_by_interrupt ON approvals (interrupt_id)""",
        """CREATE TABLE records (
            kind TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, updated TEXT NOT NULL,
            PRIMARY KEY (kind, id))""",
        """CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, ref TEXT NOT NULL, ts TEXT NOT NULL,
            text TEXT NOT NULL)""",
        """CREATE INDEX events_by_ref ON events (scope, ref, seq)""",
    ],
    # The Unified Command Stream (docs/unified-command-stream.md): where a card's turn began, and
    # where its reply goes (a Slack channel and thread, as JSON), so the rule "Slack answers only
    # Slack's cards" and the reply's destination both survive a restart.
    2: [
        """ALTER TABLE approvals ADD COLUMN origin TEXT NOT NULL DEFAULT 'mac'""",
        """ALTER TABLE approvals ADD COLUMN reply_to TEXT""",
    ],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cap(text: str | None, limit: int = TEXT_CAP) -> str | None:
    """Shorten with an explicit marker, never silently."""
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + f"… [cut: {len(text) - limit} more characters]"


class StateError(RuntimeError):
    pass


class StateStore:
    """One connection, serialized by a lock: Aura's server threads share it. `path` None keeps the
    state in memory (tests, and a Developer built without a store)."""

    def __init__(self, path: Path | None):
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False,
                                   isolation_level=None, timeout=10)  # fmt: skip
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 10000")
        if path is not None:
            mode = self._db.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise StateError(f"couldn't switch {path.name} to WAL mode (got {mode})")
            self._db.execute("PRAGMA synchronous = NORMAL")  # durable at every checkpoint in WAL mode
        self._migrate()

    # ── plumbing ────────────────────────────────────────────────────────────────────────────

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic transaction (BEGIN IMMEDIATE: it takes the write lock up front)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _query(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchall()

    @property
    def version(self) -> int:
        return int(self._query("PRAGMA user_version")[0][0])

    def _migrate(self) -> None:
        current = self.version
        if current > SCHEMA_VERSION:
            raise StateError(f"state.sqlite is from a newer Aura (schema {current}; this Aura knows {SCHEMA_VERSION})")
        for version in range(current + 1, SCHEMA_VERSION + 1):
            with self.tx() as db:
                for statement in MIGRATIONS[version]:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version = {version}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── meta ────────────────────────────────────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self.tx() as db:
            if value is None:
                db.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                           (key, value))  # fmt: skip

    # ── turns ───────────────────────────────────────────────────────────────────────────────

    def begin_turn(self, turn_id: str, kind: str, origin: str, conversation: str) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO turns (id, kind, origin, conversation, state, started) VALUES (?, ?, ?, ?, 'running', ?)",
                       (turn_id, kind, origin, conversation, now()))  # fmt: skip

    def request_cancel(self, turn_id: str) -> bool:
        with self.tx() as db:
            return db.execute("UPDATE turns SET cancel_requested = 1 WHERE id = ? AND state = 'running'",
                              (turn_id,)).rowcount == 1  # fmt: skip

    def finish_turn(self, turn_id: str, state: str, tools: int = 0, chars: int = 0, error: str | None = None) -> bool:
        """running → state, once (a second finish is a no-op and returns False)."""
        with self.tx() as db:
            return db.execute("UPDATE turns SET state = ?, tools = ?, chars = ?, error = ?, finished = ? "
                              "WHERE id = ? AND state = 'running'",
                              (state, tools, chars, cap(error, 500), now(), turn_id)).rowcount == 1  # fmt: skip

    def turn(self, turn_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM turns WHERE id = ?", (turn_id,))
        return dict(rows[0]) if rows else None

    def interrupt_unfinished_turns(self) -> int:
        """At startup: a turn still 'running' was cut off by the restart."""
        with self.tx() as db:
            n = db.execute("UPDATE turns SET state = 'interrupted', finished = ?, error = 'Aura restarted during this turn.' "
                           "WHERE state = 'running'", (now(),)).rowcount  # fmt: skip
            # Bounded: keep the latest turns only.
            db.execute("DELETE FROM turns WHERE id NOT IN (SELECT id FROM turns ORDER BY started DESC LIMIT 500)")
            return n

    # ── approvals ───────────────────────────────────────────────────────────────────────────

    def add_approval(self, approval_id: str, turn_id: str | None, conversation: str, interrupt_id: str, digest: str,
                     tools: list[str], signature: str, origin: str = "mac",
                     reply_to: dict[str, Any] | None = None) -> None:  # fmt: skip
        with self.tx() as db:
            # An earlier approval of this conversation that was never answered is superseded.
            db.execute("UPDATE approvals SET state = 'stale', reason = 'superseded', decided = ? "
                       "WHERE conversation = ? AND state = 'pending'", (now(), conversation))  # fmt: skip
            db.execute("INSERT INTO approvals (id, turn_id, conversation, interrupt_id, digest, tools, signature, state, "
                       "created, origin, reply_to) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                       (approval_id, turn_id, conversation, interrupt_id, digest, json.dumps(tools), signature, now(),
                        origin, json.dumps(reply_to) if reply_to else None))  # fmt: skip

    @staticmethod
    def _approval_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["reply_to"] = json.loads(record["reply_to"]) if record.get("reply_to") else None
        return record

    def approval(self, approval_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return self._approval_row(rows[0]) if rows else None

    def approval_for(self, interrupt_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM approvals WHERE interrupt_id = ? ORDER BY created DESC LIMIT 1", (interrupt_id,))
        return self._approval_row(rows[0]) if rows else None

    def claim_approval(self, approval_id: str) -> bool:
        """pending → submitted, atomically: exactly one answer wins; a retry gets False."""
        with self.tx() as db:
            return db.execute("UPDATE approvals SET state = 'submitted', decided = ? WHERE id = ? AND state = 'pending'",
                              (now(), approval_id)).rowcount == 1  # fmt: skip

    def settle_approval(self, approval_id: str, state: str, reason: str | None = None) -> None:
        """submitted → done (the continuation finished), or any open state → stale/unrecoverable."""
        with self.tx() as db:
            db.execute("UPDATE approvals SET state = ?, reason = COALESCE(?, reason), decided = COALESCE(decided, ?) "
                       "WHERE id = ? AND state IN ('pending', 'submitted')", (state, reason, now(), approval_id))  # fmt: skip

    def stale_approvals(self, conversation: str, reason: str) -> int:
        with self.tx() as db:
            return db.execute("UPDATE approvals SET state = 'stale', reason = ?, decided = ? "
                              "WHERE conversation = ? AND state = 'pending'", (reason, now(), conversation)).rowcount  # fmt: skip

    # ── records (builds, vault tasks) ───────────────────────────────────────────────────────

    def put_record(self, kind: str, record_id: str, state: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        with self.tx() as db:
            db.execute("INSERT INTO records (kind, id, state, data, updated) VALUES (?, ?, ?, ?, ?) "
                       "ON CONFLICT(kind, id) DO UPDATE SET state = excluded.state, data = excluded.data, "
                       "updated = excluded.updated", (kind, record_id, state, payload, now()))  # fmt: skip

    def transition(self, kind: str, record_id: str, from_states: Iterable[str], to_state: str) -> bool:
        """Compare-and-set: move a record to `to_state` only from one of `from_states`."""
        allowed = list(from_states)
        marks = ",".join("?" * len(allowed))
        with self.tx() as db:
            return db.execute(f"UPDATE records SET state = ?, updated = ? WHERE kind = ? AND id = ? AND state IN ({marks})",
                              (to_state, now(), kind, record_id, *allowed)).rowcount == 1  # fmt: skip

    def replace_record(self, kind: str, record_id: str, state: str, data: dict[str, Any], version: int) -> bool:
        """Compare-and-set on a whole record: write `data` (whose "version" is `version` + 1) only if
        the stored record is still at `version`. False if it changed meanwhile, or doesn't exist."""
        payload = json.dumps({**data, "version": version + 1}, ensure_ascii=False, default=str)
        with self.tx() as db:
            return db.execute("UPDATE records SET state = ?, data = ?, updated = ? WHERE kind = ? AND id = ? "
                              "AND json_extract(data, '$.version') = ?",
                              (state, payload, now(), kind, record_id, version)).rowcount == 1  # fmt: skip

    def record(self, kind: str, record_id: str) -> tuple[str, dict[str, Any]] | None:
        rows = self._query("SELECT state, data FROM records WHERE kind = ? AND id = ?", (kind, record_id))
        return (rows[0]["state"], json.loads(rows[0]["data"])) if rows else None

    def records(self, kind: str, states: Iterable[str] | None = None) -> list[tuple[str, str, dict[str, Any]]]:
        """(id, state, data), oldest first."""
        sql, args = "SELECT id, state, data FROM records WHERE kind = ?", [kind]
        if states is not None:
            wanted = list(states)
            sql += f" AND state IN ({','.join('?' * len(wanted))})"
            args += wanted
        return [(r["id"], r["state"], json.loads(r["data"])) for r in self._query(sql + " ORDER BY updated, id", args)]

    def delete_record(self, kind: str, record_id: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM records WHERE kind = ? AND id = ?", (kind, record_id))
            db.execute("DELETE FROM events WHERE scope = ? AND ref = ?", (kind, record_id))

    def prune_records(self, kind: str, keep_states: Iterable[str], keep: int = RECORDS_KEEP) -> int:
        """Drop the oldest records of a kind beyond `keep`, never those in `keep_states`."""
        protected = list(keep_states)
        marks = ",".join("?" * len(protected)) or "''"
        with self.tx() as db:
            doomed = [r[0] for r in db.execute(
                f"SELECT id FROM records WHERE kind = ? AND state NOT IN ({marks}) ORDER BY updated DESC LIMIT -1 OFFSET ?",
                (kind, *protected, keep)).fetchall()]  # fmt: skip
            for record_id in doomed:
                db.execute("DELETE FROM records WHERE kind = ? AND id = ?", (kind, record_id))
                db.execute("DELETE FROM events WHERE scope = ? AND ref = ?", (kind, record_id))
            return len(doomed)

    # ── bounded event feeds ─────────────────────────────────────────────────────────────────

    def add_event(self, scope: str, ref: str, text: str, keep: int = FEED_KEEP) -> None:
        with self.tx() as db:
            db.execute("INSERT INTO events (scope, ref, ts, text) VALUES (?, ?, ?, ?)", (scope, ref, now(), cap(text, 500)))
            db.execute("DELETE FROM events WHERE scope = ? AND ref = ? AND seq <= "
                       "(SELECT seq FROM events WHERE scope = ? AND ref = ? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
                       (scope, ref, scope, ref, keep))  # fmt: skip

    def events(self, scope: str, ref: str) -> list[dict[str, str]]:
        return [{"t": r["ts"], "text": r["text"]}
                for r in self._query("SELECT ts, text FROM events WHERE scope = ? AND ref = ? ORDER BY seq", (scope, ref))]  # fmt: skip
