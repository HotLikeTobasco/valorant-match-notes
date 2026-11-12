from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
                pipeline TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0, checkpoint REAL NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}', report TEXT, evidence TEXT,
                error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chunks (
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                start REAL NOT NULL, end REAL NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(session_id, start));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET state='queued',error=NULL WHERE state='processing'"
            )

    def enqueue(self, path, fingerprint, pipeline):
        now = time.time()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE path=?", (str(path),)
            ).fetchone()
            if (
                row
                and row["fingerprint"] == fingerprint
                and row["pipeline"] == pipeline
            ):
                return row["id"]
            if row:
                # Edited media invalidates manual evidence; config changes preserve it.
                evidence = (
                    row["evidence"] if row["fingerprint"] == fingerprint else None
                )
                db.execute("DELETE FROM chunks WHERE session_id=?", (row["id"],))
                db.execute(
                    """UPDATE sessions SET fingerprint=?,pipeline=?,state='queued',progress=0,
                    checkpoint=0,report=NULL,metadata='{}',evidence=?,error=NULL,updated=? WHERE id=?""",
                    (fingerprint, pipeline, evidence, now, row["id"]),
                )
                return row["id"]
            return db.execute(
                "INSERT INTO sessions(path,fingerprint,pipeline,created,updated) VALUES(?,?,?,?,?)",
                (str(path), fingerprint, pipeline, now, now),
            ).lastrowid

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM sessions WHERE state='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE sessions SET state='processing',updated=? WHERE id=?",
                    (time.time(), row["id"]),
                )
                return dict(row)

    def get(self, sid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            return dict(row) if row else None

    def list(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM sessions ORDER BY created DESC,id DESC"
                )
            ]

    def update(self, sid, **fields):
        allowed = {
            "state",
            "progress",
            "checkpoint",
            "metadata",
            "report",
            "evidence",
            "error",
        }
        if not fields.keys() <= allowed:
            raise ValueError("unsupported database update")
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET "
                + ",".join(f"{k}=?" for k in fields)
                + ",updated=? WHERE id=?",
                (*fields.values(), time.time(), sid),
            )

    def checkpoint(self, sid, start, end, duration, payload):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO chunks VALUES(?,?,?,?)",
                (sid, start, end, json.dumps(payload)),
            )
            db.execute(
                "UPDATE sessions SET checkpoint=?,progress=?,updated=? WHERE id=?",
                (end, min(0.95, end / duration * 0.95), time.time(), sid),
            )

    def chunks(self, sid):
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM chunks WHERE session_id=? ORDER BY start",
                    (sid,),
                )
            ]

    def retry(self, sid, reset=False):
        with self.connect() as db:
            row = db.execute("SELECT state FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row or row[0] in {"queued", "processing"}:
                return False
            if reset:
                db.execute("DELETE FROM chunks WHERE session_id=?", (sid,))
                db.execute(
                    "UPDATE sessions SET checkpoint=0,progress=0 WHERE id=?", (sid,)
                )
            db.execute(
                "UPDATE sessions SET state='queued',error=NULL,report=NULL WHERE id=?",
                (sid,),
            )
            return True

    def save_evidence(self, sid, evidence):
        # Atomic guard prevents an active worker overwriting a concurrent review.
        with self.connect() as db:
            result = db.execute(
                "UPDATE sessions SET evidence=?,state='queued',report=NULL,updated=? WHERE id=? AND state NOT IN ('queued','processing')",
                (json.dumps(evidence), time.time(), sid),
            )
            return result.rowcount == 1
