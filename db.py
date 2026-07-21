"""
db.py — logs every analysis to a local SQLite file (analyses.db).

Why SQLite for now: zero install, real SQL, perfect for a prototype's audit
trail and for the demo ("here is every case the system has seen"). Swapping to
Supabase/Neon Postgres for deployment is a connection-string change later.
"""
import sqlite3
import json
import datetime

DB = "analyses.db"


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            input_type TEXT,
            content TEXT,
            scam_intent INTEGER,
            risk_level TEXT,
            explanation TEXT,
            verification_step TEXT,
            signals TEXT,
            latency REAL
        )
    """)
    con.commit()
    con.close()


def log_analysis(input_type, content, result, latency):
    con = sqlite3.connect(DB)
    con.execute(
        """INSERT INTO analyses
           (ts, input_type, content, scam_intent, risk_level,
            explanation, verification_step, signals, latency)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.datetime.now().isoformat(timespec="seconds"),
            input_type,
            content,
            1 if result.get("scam_intent") else 0,
            result.get("risk_level"),
            result.get("explanation"),
            result.get("verification_step"),
            json.dumps(result.get("signals_detected", [])),
            latency,
        ),
    )
    con.commit()
    con.close()


def recent(limit=20):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM analyses ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]