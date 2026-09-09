
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import sqlite3, secrets, json
import hashlib
from datetime import datetime, timezone

BASE = Path(__file__).parent
DB = BASE / "assessment.db"
BASE.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Senior Python Backend Assessment")

SECTIONS = {
    "python": (20, "Python Internals & Concurrency"),
    "api": (15, "API Architecture & Security"),
    "framework": (15, "Django / FastAPI"),
    "sql": (15, "Advanced SQL / Database"),
    "distributed": (15, "Distributed Systems"),
    "design": (10, "Senior System Design"),
    "coding": (10, "Live Coding"),
}

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT UNIQUE, candidate TEXT,
      started_at TEXT, submitted_at TEXT, score REAL DEFAULT 0,
      section_scores TEXT DEFAULT '{}', feedback TEXT DEFAULT '',
      answers TEXT DEFAULT '{}', sql TEXT DEFAULT '', code TEXT DEFAULT '',
      events TEXT DEFAULT '[]', time_taken_seconds INTEGER DEFAULT 0,
      login_id TEXT DEFAULT '', password_hash TEXT DEFAULT '', authenticated_at TEXT DEFAULT ''
    )""")
    # Lightweight migration for an existing database.
    cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
    for name, ddl in [
        ("section_scores", "TEXT DEFAULT '{}'"),
        ("feedback", "TEXT DEFAULT ''"),
        ("time_taken_seconds", "INTEGER DEFAULT 0"),
        ("login_id", "TEXT DEFAULT ''"),
        ("password_hash", "TEXT DEFAULT ''"),
        ("authenticated_at", "TEXT DEFAULT ''"),
    ]:
        if name not in cols:
            c.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
    c.commit(); c.close()

init()

class CreateSession(BaseModel):
    candidate: str

class Login(BaseModel):
    token: str
    login_id: str
    password: str

class Submit(BaseModel):
    token: str
    candidate: str
    answers: dict = {}
    sql: str = ""
    code: str = ""
    events: list = []
    score: float = 0
    section_scores: dict = {}
    feedback: str = ""
    time_taken_seconds: int = 0

@app.post("/api/admin/create")
def create_session(x: CreateSession):
    token = secrets.token_urlsafe(22)
    login_id = "CAND-" + secrets.token_hex(4).upper()
    raw_password = secrets.token_urlsafe(8)
    password_hash = hashlib.sha256(raw_password.encode()).hexdigest()
    c = db()
    try:
        c.execute("""INSERT INTO sessions(token,candidate,started_at,login_id,password_hash)
                     VALUES(?,?,?,?,?)""",
                  (token, x.candidate, datetime.now(timezone.utc).isoformat(),
                   login_id, password_hash))
        c.commit()
    except Exception as exc:
        c.rollback()
        raise HTTPException(500, f"Could not create candidate: {exc}")
    finally:
        c.close()
    return {"token": token, "link": f"/assessment/{token}",
            "login_id": login_id, "password": raw_password}

@app.post("/api/candidate/login")
def candidate_login(x: Login):
    c = db()
    r = c.execute("""SELECT token,candidate,submitted_at,password_hash
                     FROM sessions WHERE token=? AND login_id=?""",
                  (x.token, x.login_id.strip())).fetchone()
    if not r or r["password_hash"] != hashlib.sha256(x.password.encode()).hexdigest():
        c.close()
        raise HTTPException(401, "Invalid login ID or password")
    if r["submitted_at"]:
        c.close()
        raise HTTPException(409, "Assessment already submitted")
    c.execute("UPDATE sessions SET authenticated_at=? WHERE token=?",
              (datetime.now(timezone.utc).isoformat(), x.token))
    c.commit(); c.close()
    return {"ok": True, "candidate": r["candidate"]}

@app.get("/api/admin/sessions")
def sessions():
    c = db()
    rows = c.execute("""SELECT token,candidate,started_at,submitted_at,score,
                        section_scores,feedback,events,time_taken_seconds,login_id
                        FROM sessions ORDER BY id DESC""").fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.get("/api/admin/session/{token}")
def session_detail(token: str):
    c = db()
    r = c.execute("""SELECT token,candidate,started_at,submitted_at,score,
                     section_scores,feedback,answers,sql,code,events,time_taken_seconds
                     FROM sessions WHERE token=?""", (token,)).fetchone()
    c.close()
    if not r: raise HTTPException(404, "Assessment not found")
    return dict(r)

@app.get("/api/session/{token}")
def session(token: str):
    c = db()
    r = c.execute("SELECT token,candidate,submitted_at FROM sessions WHERE token=?", (token,)).fetchone()
    c.close()
    if not r: raise HTTPException(404, "Assessment link not found")
    return dict(r)

@app.post("/api/submit")
def submit(x: Submit):
    c = db()
    r = c.execute("SELECT * FROM sessions WHERE token=?", (x.token,)).fetchone()
    if not r: raise HTTPException(404, "Invalid assessment link")
    if r["submitted_at"]: raise HTTPException(409, "Assessment already submitted")
    c.execute("""UPDATE sessions SET candidate=?,submitted_at=?,score=?,
                 section_scores=?,feedback=?,answers=?,sql=?,code=?,events=?,
                 time_taken_seconds=? WHERE token=?""",
              (x.candidate, datetime.now(timezone.utc).isoformat(), x.score,
               json.dumps(x.section_scores), x.feedback, json.dumps(x.answers),
               x.sql, x.code, json.dumps(x.events), x.time_taken_seconds, x.token))
    c.commit(); c.close()
    return {"ok": True}

app.mount("/static", StaticFiles(directory=BASE/"static"), name="static")

@app.get("/")
def admin():
    return FileResponse(BASE/"static/admin.html")

@app.get("/assessment/{token}")
def candidate():
    return FileResponse(BASE/"static/assessment.html")
