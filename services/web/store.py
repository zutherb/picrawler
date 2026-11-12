"""SQLite storage for the object log (sightings and events)."""
import csv
import io
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL,          -- sighting | event
    label     TEXT NOT NULL,          -- object label, or event kind (stop, watchdog, rejected)
    start     REAL NOT NULL,
    end       REAL,
    max_score REAL,
    max_count INTEGER,
    mode      TEXT,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS log_start ON log (start);
"""
COLUMNS = ("id", "kind", "label", "start", "end", "max_score", "max_count", "mode", "detail")
QUERY_LIMIT = 1000


class LogStore:

    def __init__(self, path, retention_days=7, max_entries=10000, clock=time.time):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self.retention_days = retention_days
        self.max_entries = max_entries
        self._clock = clock

    def add_sighting(self, sighting):
        cur = self._db.execute(
            "INSERT INTO log (kind, label, start, end, max_score, max_count, mode) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sighting", sighting.label, sighting.start, sighting.end, sighting.max_score,
             sighting.max_count, sighting.mode))
        self._db.commit()
        sighting.row_id = cur.lastrowid
        return sighting.row_id

    def finish_sighting(self, sighting):
        if sighting.row_id is None:
            self.add_sighting(sighting)
            return
        self._db.execute("UPDATE log SET end = ?, max_score = ?, max_count = ? WHERE id = ?",
                         (sighting.end, sighting.max_score, sighting.max_count, sighting.row_id))
        self._db.commit()

    def add_event(self, label, detail, ts):
        cur = self._db.execute("INSERT INTO log (kind, label, start, detail) VALUES ('event', ?, ?, ?)",
                               (label, ts, detail))
        self._db.commit()
        return cur.lastrowid

    def query(self, label=None, since=None, until=None, min_score=None, kind=None, limit=QUERY_LIMIT):
        sql, args = "SELECT %s FROM log WHERE 1=1" % ", ".join(COLUMNS), []
        if label:
            sql += " AND label = ?"
            args.append(label)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if since is not None:
            sql += " AND start >= ?"
            args.append(since)
        if until is not None:
            sql += " AND start <= ?"
            args.append(until)
        if min_score is not None:
            sql += " AND (kind != 'sighting' OR max_score >= ?)"
            args.append(min_score)
        sql += " ORDER BY start DESC LIMIT ?"
        args.append(max(1, min(int(limit), QUERY_LIMIT)))
        return [dict(zip(COLUMNS, row)) for row in self._db.execute(sql, args)]

    def labels(self):
        return [row[0] for row in self._db.execute("SELECT DISTINCT label FROM log ORDER BY label")]

    def to_csv(self, rows):
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return out.getvalue()

    def prune(self):
        cutoff = self._clock() - self.retention_days * 86400
        self._db.execute("DELETE FROM log WHERE start < ?", (cutoff,))
        self._db.execute("DELETE FROM log WHERE id NOT IN (SELECT id FROM log ORDER BY start DESC LIMIT ?)",
                         (self.max_entries,))
        self._db.commit()

    def close(self):
        self._db.close()
