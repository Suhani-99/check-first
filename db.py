"""
db.py — persistence for every analysis and every follow-up turn.

One code path, two backends:
  - no DATABASE_URL set  -> SQLite file (local development)
  - DATABASE_URL set     -> Postgres (Supabase / Neon, used in deployment)

Why this matters: Render's free tier has an EPHEMERAL filesystem, so a SQLite
file is wiped on every restart and redeploy. The audit trail - and the demo
history - would silently vanish in production. SQLAlchemy lets the same code
serve both, so switching is a connection string, not a rewrite.

Sessions: an analysis and all its follow-up turns share a session_id, so a whole
conversation can be replayed as one thread.
"""
import os
import json
import uuid
import datetime

from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, String,
                        Text, Boolean, Float, DateTime, select, desc)

# Supabase/Neon hand out URLs starting postgres:// ; SQLAlchemy wants postgresql://
_url = os.environ.get("DATABASE_URL", "").strip()
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _url or "sqlite:///analyses.db"
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

# pool_pre_ping: free Postgres tiers drop idle connections; this reconnects
# transparently instead of failing the first request after a quiet spell.
_engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"sslmode": "require"} if IS_POSTGRES else {},
)

_meta = MetaData()

analyses = Table(
    "analyses", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(40), index=True),
    Column("ts", DateTime, default=datetime.datetime.utcnow),
    Column("input_type", String(20)),
    Column("mode", String(20)),                 # single | conversation
    Column("content", Text),                    # message or transcript
    Column("scam_intent", Boolean),
    Column("involves_action", Boolean),
    Column("risk_level", String(10)),
    Column("risk_label", String(120)),
    Column("explanation", Text),
    Column("verification_step", Text),
    Column("signals", Text),                    # JSON
    Column("verification_plan", Text),          # JSON
    Column("certification_stripped", Boolean, default=False),
    Column("latency", Float),
)

followups = Table(
    "followups", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(40), index=True),
    Column("ts", DateTime, default=datetime.datetime.utcnow),
    Column("turn", Integer),
    Column("user_message", Text),
    Column("reply", Text),
    Column("risk_direction", String(10)),
    Column("urgent", Boolean),
    Column("latency", Float),
)


def init_db():
    _meta.create_all(_engine)


def new_session() -> str:
    return uuid.uuid4().hex[:16]


def log_analysis(input_type, content, result, latency, session_id=None):
    session_id = session_id or new_session()
    with _engine.begin() as c:
        c.execute(analyses.insert().values(
            session_id=session_id,
            ts=datetime.datetime.utcnow(),
            input_type=input_type,
            mode=result.get("mode", "single"),
            content=content,
            scam_intent=bool(result.get("scam_intent")),
            involves_action=bool(result.get("involves_action", True)),
            risk_level=result.get("risk_level"),
            risk_label=result.get("risk_label"),
            explanation=result.get("explanation"),
            verification_step=result.get("verification_step"),
            signals=json.dumps(result.get("signals_detected", [])),
            verification_plan=json.dumps(result.get("verification_plan", {})),
            certification_stripped=bool(result.get("certification_stripped", False)),
            latency=latency,
        ))
    return session_id


def log_followup(session_id, turn, user_message, out, latency):
    with _engine.begin() as c:
        c.execute(followups.insert().values(
            session_id=session_id,
            ts=datetime.datetime.utcnow(),
            turn=turn,
            user_message=user_message,
            reply=out.get("reply"),
            risk_direction=out.get("risk_direction"),
            urgent=bool(out.get("urgent")),
            latency=latency,
        ))


def recent(limit=20):
    """Recent analyses, each with its follow-up thread attached."""
    with _engine.connect() as c:
        rows = c.execute(
            select(analyses).order_by(desc(analyses.c.id)).limit(limit)
        ).mappings().all()

        out = []
        for r in rows:
            d = dict(r)
            d["ts"] = d["ts"].isoformat(timespec="seconds") if d.get("ts") else None
            d["signals"] = json.loads(d.get("signals") or "[]")
            d["verification_plan"] = json.loads(d.get("verification_plan") or "{}")
            fups = c.execute(
                select(followups)
                .where(followups.c.session_id == d["session_id"])
                .order_by(followups.c.turn)
            ).mappings().all()
            d["followups"] = [
                {**dict(f), "ts": f["ts"].isoformat(timespec="seconds") if f["ts"] else None}
                for f in fups
            ]
            out.append(d)
        return out


def stats():
    """Aggregate counts - useful for the demo and the scoreboard."""
    with _engine.connect() as c:
        rows = c.execute(select(analyses)).mappings().all()
        n = len(rows)
        if not n:
            return {"backend": "postgres" if IS_POSTGRES else "sqlite", "total": 0}
        lat = [r["latency"] for r in rows if r["latency"] is not None]
        fups = c.execute(select(followups)).mappings().all()
        return {
            "backend": "postgres" if IS_POSTGRES else "sqlite",
            "total_analyses": n,
            "flagged_scam": sum(1 for r in rows if r["scam_intent"]),
            "by_input_type": {
                t: sum(1 for r in rows if r["input_type"] == t)
                for t in {r["input_type"] for r in rows}
            },
            "certifications_stripped": sum(1 for r in rows if r["certification_stripped"]),
            "followup_turns": len(fups),
            "avg_latency": round(sum(lat) / len(lat), 2) if lat else None,
        }