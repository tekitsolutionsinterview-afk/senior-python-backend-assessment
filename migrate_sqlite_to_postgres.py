"""One-time migration of local assessment.db into Render PostgreSQL.

Usage on Windows PowerShell:
  $env:DATABASE_URL = "<Render external database URL>"
  python migrate_sqlite_to_postgres.py

The script is intentionally separate from the web app so candidate data is never
committed to GitHub.
"""
import os, sqlite3, json, sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

BASE = Path(__file__).parent
SQLITE_DB = BASE / "assessment.db"
URL = os.getenv("DATABASE_URL", "").strip()
if not URL:
    raise SystemExit("DATABASE_URL is required")
if not SQLITE_DB.exists():
    raise SystemExit(f"Missing {SQLITE_DB}")

src = sqlite3.connect(SQLITE_DB)
src.row_factory = sqlite3.Row
rows = src.execute("SELECT * FROM sessions ORDER BY id").fetchall()
src.close()

with psycopg.connect(URL, row_factory=dict_row) as dst:
    dst.execute("""
    CREATE TABLE IF NOT EXISTS sessions(
      id BIGSERIAL PRIMARY KEY, token TEXT UNIQUE NOT NULL, candidate TEXT NOT NULL,
      started_at TEXT, submitted_at TEXT, score DOUBLE PRECISION DEFAULT 0,
      section_scores TEXT DEFAULT '{}', feedback TEXT DEFAULT '', answers TEXT DEFAULT '{}',
      sql TEXT DEFAULT '', code TEXT DEFAULT '', events TEXT DEFAULT '[]',
      time_taken_seconds INTEGER DEFAULT 0, login_id TEXT DEFAULT '',
      password_hash TEXT DEFAULT '', authenticated_at TEXT DEFAULT ''
    )
    """)
    for r in rows:
        dst.execute("""INSERT INTO sessions
          (token,candidate,started_at,submitted_at,score,section_scores,feedback,answers,sql,code,events,time_taken_seconds,login_id,password_hash,authenticated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (token) DO UPDATE SET
            candidate=EXCLUDED.candidate, started_at=EXCLUDED.started_at,
            submitted_at=EXCLUDED.submitted_at, score=EXCLUDED.score,
            section_scores=EXCLUDED.section_scores, feedback=EXCLUDED.feedback,
            answers=EXCLUDED.answers, sql=EXCLUDED.sql, code=EXCLUDED.code,
            events=EXCLUDED.events, time_taken_seconds=EXCLUDED.time_taken_seconds,
            login_id=EXCLUDED.login_id, password_hash=EXCLUDED.password_hash,
            authenticated_at=EXCLUDED.authenticated_at
        """, (
            r["token"], r["candidate"], r["started_at"], r["submitted_at"], r["score"] or 0,
            r["section_scores"] or "{}", r["feedback"] or "", r["answers"] or "{}",
            r["sql"] or "", r["code"] or "", r["events"] or "[]", r["time_taken_seconds"] or 0,
            r["login_id"] or "", r["password_hash"] or "", r["authenticated_at"] or ""
        ))
    dst.commit()

print(f"Migrated {len(rows)} local records to PostgreSQL.")
