import tempfile
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
import os
import sqlite3
import secrets
import json
import hashlib
import logging
import hmac
import base64
import re
from datetime import datetime, timezone

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Local SQLite-only environments still work.
    psycopg = None
    dict_row = None

BASE = Path(__file__).parent
SQLITE_DB = BASE / "assessment.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "").strip()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("assessment")

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.units import mm

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


def using_postgres():
    return bool(DATABASE_URL)


def db():
    """Return a DB connection. Render uses PostgreSQL; local development uses SQLite."""
    if using_postgres():
        if psycopg is None:
            raise RuntimeError("DATABASE_URL is set but psycopg is not installed")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    c = sqlite3.connect(SQLITE_DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def execute(c, sql, params=()):
    """Execute SQL with the appropriate placeholder style."""
    if using_postgres():
        return c.execute(sql.replace("?", "%s"), params)
    return c.execute(sql, params)


def _admin_secret():
    # Keep the signing secret outside source control. ADMIN_PASSWORD is also required.
    if not ADMIN_PASSWORD:
        return None
    return (ADMIN_SECRET or ADMIN_PASSWORD).encode()

def _make_admin_token():
    secret = _admin_secret()
    if not secret:
        return None
    payload = f"admin:{int(datetime.now(timezone.utc).timestamp())}".encode()
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(payload + b"." + sig).decode().rstrip("=")

def _is_admin(request):
    token = request.cookies.get("admin_session")
    secret = _admin_secret()
    if not token or not secret:
        return False
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, sig = raw.rsplit(b".", 1)
        expected = hmac.new(secret, payload, hashlib.sha256).hexdigest().encode()
        if not hmac.compare_digest(sig, expected):
            return False
        ts = int(payload.decode().split(":", 1)[1])
        return int(datetime.now(timezone.utc).timestamp()) - ts <= 8 * 60 * 60
    except Exception:
        return False

def require_admin(request):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin access is not configured. Set ADMIN_PASSWORD in the server environment.")
    if not _is_admin(request):
        raise HTTPException(401, "Admin authentication required")



def init():
    c = db()
    try:
        if using_postgres():
            c.execute("""
            CREATE TABLE IF NOT EXISTS sessions(
              id BIGSERIAL PRIMARY KEY,
              token TEXT UNIQUE NOT NULL,
              candidate TEXT NOT NULL,
              started_at TEXT,
              exam_started_at TEXT,
              submitted_at TEXT,
              score DOUBLE PRECISION DEFAULT 0,
              section_scores TEXT DEFAULT '{}',
              coding_tests TEXT DEFAULT '{}',
              feedback TEXT DEFAULT '',
              answers TEXT DEFAULT '{}',
              sql TEXT DEFAULT '',
              code TEXT DEFAULT '',
              events TEXT DEFAULT '[]',
              time_taken_seconds INTEGER DEFAULT 0,
              login_id TEXT DEFAULT '',
              password_hash TEXT DEFAULT '',
              authenticated_at TEXT DEFAULT ''
            )
            """)
            # Migrate an existing Render database created by earlier versions.
            c.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS exam_started_at TEXT")
            c.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS coding_tests TEXT DEFAULT '{}'" )
        else:
            c.execute("""CREATE TABLE IF NOT EXISTS sessions(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              token TEXT UNIQUE, candidate TEXT,
              started_at TEXT, exam_started_at TEXT, submitted_at TEXT, score REAL DEFAULT 0,
              section_scores TEXT DEFAULT '{}', coding_tests TEXT DEFAULT '{}', feedback TEXT DEFAULT '',
              answers TEXT DEFAULT '{}', sql TEXT DEFAULT '', code TEXT DEFAULT '',
              events TEXT DEFAULT '[]', time_taken_seconds INTEGER DEFAULT 0,
              login_id TEXT DEFAULT '', password_hash TEXT DEFAULT '', authenticated_at TEXT DEFAULT ''
            )""")
            cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()}
            for name, ddl in [
                ("exam_started_at", "TEXT DEFAULT ''"),
                ("coding_tests", "TEXT DEFAULT '{}'"),
                ("section_scores", "TEXT DEFAULT '{}'"),
                ("feedback", "TEXT DEFAULT ''"),
                ("time_taken_seconds", "INTEGER DEFAULT 0"),
                ("login_id", "TEXT DEFAULT ''"),
                ("password_hash", "TEXT DEFAULT ''"),
                ("authenticated_at", "TEXT DEFAULT ''"),
            ]:
                if name not in cols:
                    c.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
        c.commit()
    finally:
        c.close()


init()


@app.get("/health")
def health():
    return {"status": "ok", "database": "postgres" if using_postgres() else "sqlite"}


class CreateSession(BaseModel):
    candidate: str = Field(min_length=1, max_length=200)


class Login(BaseModel):
    token: str
    login_id: str
    password: str



# Deterministic server-side scoring rubric. The browser-provided score is ignored.
# Each question is awarded points for demonstrating the required technical concepts.
QUESTION_RUBRICS = {
    "q1": (4, [
        ["0, 1, 2, 3, 4", "0,1,2,3,4"],
        ["late binding", "closure"],
        ["default argument", "i=i", "lambda i"],
    ]),
    "q2": (4, [
        ["__getattribute__"], ["__getattr__"],
        ["recursion", "recursive", "recursively"],
        ["object.__getattribute__", "super().__getattribute__"],
    ]),
    "q3": (4, [
        ["blocking", "blocks event loop", "event loop"],
        ["requests.get", "httpx", "aiohttp"],
        ["run_in_threadpool", "asyncio.to_thread", "threadpool", "background"],
        ["cpu", "process pool", "multiprocessing", "worker"],
    ]),
    "q4": (4, [
        ["race condition", "race"], ["lock", "mutex", "threading.lock"],
        ["atomic", "critical section"], ["inventory", "oversell", "overselling"],
    ]),
    "q5": (4, [
        ["memory leak", "leak"], ["cache", "unbounded"],
        ["retained", "reference"], ["tracemalloc", "objgraph", "heap", "profiler"],
    ]),
    "q6": (3, [["idempotency-key", "idempotency key"], ["unique", "unique constraint"], ["transaction", "atomic"], ["same response", "stored response", "payment status"]]),
    "q7": (4, [["redis"], ["distributed", "in-process"], ["atomic", "lua", "incr", "script"], ["ttl", "expire", "sliding window", "token bucket"]]),
    "q8": (4, [["retry storm", "retry storm"], ["exponential backoff", "backoff"], ["timeout"], ["circuit breaker", "circuit-breaker"], ["jitter", "retry budget"]]),
    "q9": (4, [["authentication", "authorization", "authz"], ["path traversal", "basename", "sanitize"], ["malware", "virus", "antivirus", "clamav"], ["object storage", "s3", "blob"], ["100 mb", "size limit", "content type", "mime"]]),
    "q10": (4, [["n+1", "n + 1"], ["select_related", "prefetch_related", "eager"], ["pagination", "cursor"], ["index", "indexes"]]),
    "q11": (4, [["transaction", "atomic"], ["outbox"], ["idempot", "idempotency"], ["worker", "retry", "queue"]]),
    "q12": (4, [["pool", "connection pool"], ["leak", "not closing", "unreleased"], ["long transaction", "slow query", "lock"], ["max connections", "pool size", "database connections"]]),
    "q13": (3, [["sync", "synchronous"], ["blocking", "event loop"], ["async driver", "async orm", "threadpool", "run_in_threadpool"]]),
    "q14": (5, [["dense_rank", "rank() over", "dense_rank() over"], ["partition by", "department_id"], ["order by", "salary"], ["3", "top 3"], ["desc"]]),
    "q15": (5, [["login"], ["purchase"], ["24", "24 hour", "interval"], ["not exists", "left join", "anti join"]]),
    "q16": (5, [["explain", "explain analyze"], ["index", "composite"], ["customer_id", "status"], ["created_at"], ["order by"]]),
    "q17": (5, [["offset", "commit offset"], ["duplicate", "duplication", "at-least-once"], ["idempotent", "idempotency"], ["unique", "dedup", "processed"]]),
    "q18": (5, [["redis"], ["ttl", "expire"], ["owner", "token"], ["renew", "heartbeat"], ["fencing", "fencing token"]]),
    "q19": (5, [["stampede", "thundering herd"], ["lock", "single flight"], ["ttl", "jitter", "random"], ["stale", "stale-while-revalidate"], ["cache"]]),
    "q20": (10, [["api"], ["queue"], ["worker"], ["retry", "dead letter", "dlq"], ["idempot", "duplicate"], ["heartbeat", "visibility timeout"], ["priority"], ["scheduled"], ["cancellation", "cancel"], ["monitor", "metrics", "scaling"]]),
}

def _norm(v):
    return " ".join(str(v or "").lower().replace("_", " ").split())

def _question_score(text, max_points, groups):
    t = _norm(text)
    if not t:
        return 0
    hits = sum(1 for group in groups if any(_norm(term) in t for term in group))
    return round(max_points * hits / len(groups), 2)

def _coding_score(code, coding_tests=None):
    """Score the coding question from executed functional tests plus code analysis.
    Functional tests are worth 8 points; static requirements are worth 2 points.
    No generic keyword/completeness points are awarded.
    """
    tests = coding_tests if isinstance(coding_tests, dict) else {}
    functional = ["api", "basic_get_set", "lru_eviction", "update_mru", "ttl_expiry", "zero_negative_ttl", "capacity_one", "stress_100k"]
    passed = sum(1 for n in functional if tests.get(n) is True)
    static = sum(1 for n in ("static_thread_safety", "static_o1_lru") if tests.get(n) is True)
    return round(min(10.0, passed * 1.0 + static * 1.0), 2)

def analyze_code(code):
    """Static diagnostics for the TTL+LRU cache question. Does not contribute points."""
    result = {"has_ttlcache": False, "has_lock": False, "has_lru_structure": False, "has_time": False, "issues": []}
    try:
        import ast
        tree = ast.parse(code or "")
        result["has_ttlcache"] = any(isinstance(n, ast.ClassDef) and n.name == "TTLCache" for n in ast.walk(tree))
        text = (code or "").lower()
        result["has_lock"] = any(x in text for x in ("threading.lock", "threading.rlock", "rlock(", "lock("))
        result["has_lru_structure"] = any(x in text for x in ("ordereddict", "ordered_dict", "move_to_end", "prev", "next"))
        result["has_time"] = any(x in text for x in ("time.monotonic", "time.time", "monotonic("))
        if not result["has_ttlcache"]: result["issues"].append("TTLCache class/API not detected")
        if not result["has_lock"]: result["issues"].append("No obvious thread-synchronization primitive detected")
        if not result["has_lru_structure"]: result["issues"].append("No obvious O(1) LRU structure detected")
        if not result["has_time"]: result["issues"].append("No obvious time source detected for TTL expiry")
    except Exception as exc:
        result["issues"].append(f"Code could not be statically parsed: {exc}")
    return result

def calculate_score(answers, sql_text, code_text, coding_tests=None):
    scores = {"python": 0.0, "api": 0.0, "framework": 0.0, "sql": 0.0, "distributed": 0.0, "design": 0.0, "coding": 0.0}
    section_for = {
        **{f"q{i}": "python" for i in range(1,6)},
        **{f"q{i}": "api" for i in range(6,10)},
        **{f"q{i}": "framework" for i in range(10,14)},
        **{f"q{i}": "sql" for i in range(14,17)},
        **{f"q{i}": "distributed" for i in range(17,20)},
        "q20": "design",
    }
    for q, (mx, groups) in QUESTION_RUBRICS.items():
        text = sql_text if q == "q14" else answers.get(q, "")
        # Unattempted questions receive 0 and are reported separately.
        scores[section_for[q]] += _question_score(text, mx, groups)
    scores["coding"] = _coding_score(code_text, coding_tests)
    return {k: round(v, 2) for k, v in scores.items()}

SECT_MAX = {"python":20,"api":15,"framework":15,"sql":15,"distributed":15,"design":10,"coding":10}
SECTION_NAMES = {"python":"Python internals & concurrency","api":"API architecture & security","framework":"Django/FastAPI","sql":"Advanced SQL & databases","distributed":"Distributed systems","design":"System design","coding":"Live coding"}

def buildFeedback(sec, total):
    weak = sorted(((k,v) for k,v in sec.items() if v < SECT_MAX[k]*0.6), key=lambda z:z[1])
    level = "Strong Hire" if total >= 90 else "Hire" if total >= 80 else "Consider / Technical Discussion" if total >= 70 else "Weak" if total >= 60 else "Reject"
    if weak:
        msg = "Main areas needing improvement: " + ", ".join(f"{SECTION_NAMES[k]} ({v}/{SECT_MAX[k]})" for k,v in weak) + "."
    else:
        msg = "No major weak section was detected by the automated rubric; manually review answer correctness and code."
    return {"level": level, "message": f"Overall: {level}. {msg}", "weak": [k for k,_ in weak]}

class Submit(BaseModel):
    token: str
    candidate: str = ""
    answers: dict = {}
    sql: str = ""
    code: str = ""
    events: list = []
    score: float = 0
    section_scores: dict = {}
    feedback: str = ""
    coding_tests: dict = {}
    time_taken_seconds: int = 0


@app.post("/api/admin/login")
def admin_login(request: Request, x: dict):
    password = str(x.get("password", ""))
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin access is not configured. Set ADMIN_PASSWORD in the server environment.")
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        raise HTTPException(401, "Invalid admin password")
    token = _make_admin_token()
    from fastapi.responses import JSONResponse
    response = JSONResponse({"ok": True})
    response.set_cookie("admin_session", token, httponly=True, secure=(request.url.scheme == "https"), samesite="strict", max_age=8*60*60, path="/")
    return response

@app.post("/api/admin/logout")
def admin_logout():
    from fastapi.responses import JSONResponse
    response = JSONResponse({"ok": True})
    response.delete_cookie("admin_session", path="/")
    return response

@app.get("/api/admin/auth")
def admin_auth(request: Request):
    if not ADMIN_PASSWORD:
        raise HTTPException(503, "Admin access is not configured")
    return {"authenticated": _is_admin(request)}

@app.post("/api/admin/create")
def create_session(request: Request, x: CreateSession):
    require_admin(request)
    token = secrets.token_urlsafe(22)
    login_id = "CAND-" + secrets.token_hex(4).upper()
    raw_password = secrets.token_urlsafe(8)
    password_hash = hashlib.sha256(raw_password.encode()).hexdigest()
    c = db()
    try:
        execute(c, """INSERT INTO sessions(token,candidate,started_at,login_id,password_hash)
                     VALUES(?,?,?,?,?)""",
                (token, x.candidate.strip(), datetime.now(timezone.utc).isoformat(),
                 login_id, password_hash))
        c.commit()
    except Exception as exc:
        c.rollback()
        log.exception("Could not create candidate")
        raise HTTPException(500, f"Could not create candidate: {exc}")
    finally:
        c.close()
    return {"token": token, "link": f"/assessment/{token}",
            "login_id": login_id, "password": raw_password}


@app.post("/api/candidate/login")
def candidate_login(x: Login):
    c = db()
    try:
        r = execute(c, """SELECT token,candidate,submitted_at,password_hash
                         FROM sessions WHERE token=? AND login_id=?""",
                     (x.token, x.login_id.strip())).fetchone()
        if not r or r["password_hash"] != hashlib.sha256(x.password.encode()).hexdigest():
            raise HTTPException(401, "Invalid login ID or password")
        if r["submitted_at"]:
            raise HTTPException(409, "Assessment already submitted")
        execute(c, "UPDATE sessions SET authenticated_at=? WHERE token=?",
                (datetime.now(timezone.utc).isoformat(), x.token))
        c.commit()
        return {"ok": True, "candidate": r["candidate"]}
    finally:
        c.close()


@app.post("/api/candidate/start/{token}")
def candidate_start(token: str):
    c = db()
    try:
        r = execute(c, "SELECT token,submitted_at,exam_started_at FROM sessions WHERE token=?", (token,)).fetchone()
        if not r:
            raise HTTPException(404, "Assessment link not found")
        if r["submitted_at"]:
            raise HTTPException(409, "Assessment already submitted")
        if r["exam_started_at"]:
            return {"ok": True, "exam_started_at": r["exam_started_at"]}
        started = datetime.now(timezone.utc).isoformat()
        execute(c, "UPDATE sessions SET exam_started_at=? WHERE token=?", (started, token))
        c.commit()
        return {"ok": True, "exam_started_at": started}
    finally:
        c.close()


@app.get("/api/admin/sessions")
def sessions(request: Request):
    require_admin(request)
    c = db()
    try:
        rows = execute(c, """SELECT token,candidate,started_at,exam_started_at,submitted_at,score,
                            section_scores,coding_tests,feedback,events,time_taken_seconds,login_id
                            FROM sessions ORDER BY id DESC""").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


@app.get("/api/admin/session/{token}")
def session_detail(request: Request, token: str):
    require_admin(request)
    c = db()
    try:
        r = execute(c, """SELECT token,candidate,started_at,exam_started_at,submitted_at,score,
                         section_scores,coding_tests,feedback,answers,sql,code,events,time_taken_seconds
                         FROM sessions WHERE token=?""", (token,)).fetchone()
        if not r:
            raise HTTPException(404, "Assessment not found")
        return dict(r)
    finally:
        c.close()


@app.get("/api/session/{token}")
def session(token: str):
    c = db()
    try:
        r = execute(c, "SELECT token,candidate,submitted_at FROM sessions WHERE token=?", (token,)).fetchone()
        if not r:
            raise HTTPException(404, "Assessment link not found")
        return dict(r)
    finally:
        c.close()


@app.post("/api/autosave")
def autosave(x: Submit):
    c = db()
    try:
        r = execute(c, "SELECT token,submitted_at,exam_started_at FROM sessions WHERE token=?", (x.token,)).fetchone()
        if not r:
            raise HTTPException(404, "Invalid assessment link")
        if r["submitted_at"]:
            raise HTTPException(409, "Assessment already submitted")
        if not r["exam_started_at"]:
            raise HTTPException(400, "Assessment has not been started")
        execute(c, "UPDATE sessions SET answers=?,sql=?,code=?,events=? WHERE token=?",
                (json.dumps(x.answers), x.sql, x.code, json.dumps(x.events), x.token))
        c.commit()
        return {"ok": True}
    finally:
        c.close()


@app.post("/api/submit")
@app.post("/api/submit/")
def submit(x: Submit):
    """Persist the completed assessment. The trailing-slash alias avoids client/proxy path mismatches."""
    c = db()
    try:
        r = execute(c, "SELECT token,submitted_at,exam_started_at FROM sessions WHERE token=?", (x.token,)).fetchone()
        if not r:
            raise HTTPException(404, "Invalid assessment link")
        if r["submitted_at"]:
            raise HTTPException(409, "Assessment already submitted")
        if not r["exam_started_at"]:
            raise HTTPException(400, "Assessment has not been started")
        try:
            exam_started = datetime.fromisoformat(r["exam_started_at"].replace("Z", "+00:00"))
            elapsed = max(0, int((datetime.now(timezone.utc) - exam_started).total_seconds()))
        except Exception:
            elapsed = int(x.time_taken_seconds or 0)
        if elapsed > 2710:
            # The browser normally auto-submits at 45:00. A small 10-second network grace
            # prevents a valid expiry submission from being rejected.
            raise HTTPException(409, "Assessment time has expired")
        time_taken = min(2700, max(0, elapsed))

        # Never trust the browser-provided score. Recalculate on the server.
        # Functional test results come from the browser's isolated Pyodide test harness;
        # static code requirements are independently analysed on the server.
        coding_tests = dict(x.coding_tests or {})
        diagnostics = analyze_code(x.code)
        coding_tests["static_thread_safety"] = bool(diagnostics.get("has_lock"))
        coding_tests["static_o1_lru"] = bool(diagnostics.get("has_lru_structure"))
        calculated_sections = calculate_score(x.answers, x.sql, x.code, coding_tests)
        total = round(sum(calculated_sections.values()), 2)
        fb = buildFeedback(calculated_sections, total)
        feedback_json = json.dumps({
            **fb,
            "scoring": "Automated technical rubric; administrator should manually review open-ended answers and code before final hiring decision."
        })
        submitted_at = datetime.now(timezone.utc).isoformat()
        execute(c, """UPDATE sessions SET candidate=?,submitted_at=?,score=?,
                     section_scores=?,coding_tests=?,feedback=?,answers=?,sql=?,code=?,events=?,
                     time_taken_seconds=? WHERE token=?""",
                (x.candidate, submitted_at, total,
                 json.dumps(calculated_sections), json.dumps(coding_tests), feedback_json, json.dumps(x.answers),
                 x.sql, x.code, json.dumps(x.events), time_taken, x.token))
        c.commit()
        log.info("Assessment submitted token=%s score=%s sections=%s", x.token, total, calculated_sections)
        return {"ok": True, "submitted_at": submitted_at, "score": total, "section_scores": calculated_sections}
    except HTTPException:
        c.rollback()
        raise
    except Exception as exc:
        c.rollback()
        log.exception("Assessment submission failed")
        raise HTTPException(500, f"Could not save assessment: {exc}")
    finally:
        c.close()



@app.get("/api/admin/session/{token}/report.pdf")
def session_report_pdf(request: Request, token: str):
    require_admin(request)
    """Generate a hiring feedback PDF with score, rating, strengths and technical gaps."""
    c = db()
    try:
        r = execute(c, "SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    finally:
        c.close()
    if not r:
        raise HTTPException(status_code=404, detail="Session not found")
    row = dict(r)

    def col(name):
        return row.get(name)

    candidate = col("candidate") or "Candidate"
    score = float(col("score") or 0)
    answers_raw = col("answers") or "{}"
    section_raw = col("section_scores") or "{}"
    feedback_raw = col("feedback") or ""
    coding_tests_raw = col("coding_tests") or "{}"

    try:
        answers = json.loads(answers_raw)
    except Exception:
        answers = {}
    try:
        section_scores = json.loads(section_raw)
    except Exception:
        section_scores = {}
    try:
        feedback = json.loads(feedback_raw) if feedback_raw else {}
    except Exception:
        feedback = {}
    try:
        coding_tests = json.loads(coding_tests_raw) if coding_tests_raw else {}
    except Exception:
        coding_tests = {}

    if score >= 90:
        rating, rating_color, summary = "OUTSTANDING", colors.HexColor("#15803D"), "Excellent technical performance."
    elif score >= 70:
        rating, rating_color, summary = "GOOD", colors.HexColor("#CA8A04"), "Good technical performance with some areas for improvement."
    else:
        rating, rating_color, summary = "BAD", colors.HexColor("#DC2626"), "Technical fundamentals require significant improvement."

    # Keyword-based technical gap detection aligned with the assessment rubric.
    gap_rules = [
        ("Python Internals & Concurrency", ["closure", "late binding", "descriptor", "__getattribute__", "__getattr__", "async", "blocking", "race", "lock", "thread"]),
        ("API Architecture & Security", ["idempot", "rate limit", "retry", "upload", "validation", "authentication", "authorization", "security"]),
        ("Django/FastAPI", ["n+1", "select_related", "prefetch_related", "dependency injection", "middleware", "async orm"]),
        ("Advanced SQL / Database", ["transaction", "outbox", "window function", "partition", "index", "query plan", "pool", "deadlock"]),
        ("Distributed Systems", ["kafka", "consumer", "redis", "fencing", "cache stampede", "circuit breaker", "backpressure"]),
        ("System Design", ["queue", "worker", "horizontal", "scaling", "partition", "observability", "load balancer", "database"]),
        ("Live Coding", ["ttl", "lru", "thread-safe", "lock", "evict", "cache"]),
    ]

    # Determine weak sections from section scores as a fraction of each section max.
    weak_sections = []
    for sec, val in section_scores.items():
        try:
            v = float(val)
            maxv = SECT_MAX.get(sec, 0)
            if maxv and v < maxv * 0.60:
                weak_sections.append((sec, v))
        except Exception:
            pass

    all_text = json.dumps(answers, ensure_ascii=False).lower()
    technical_gaps = []
    for sec, terms in gap_rules:
        if any(t in all_text for t in terms):
            # Only list as a gap if section is weak, or if no section score exists.
            weak = any(SECTION_NAMES.get(ws, ws).lower() == sec.lower() for ws, _ in weak_sections)
            if weak or not section_scores:
                technical_gaps.append(sec)

    if not technical_gaps and weak_sections:
        technical_gaps = [SECTION_NAMES.get(x[0], x[0]) for x in weak_sections]
    if not technical_gaps:
        technical_gaps = ["No major technical gap detected by the automated rubric. Manual review is recommended."]

    question_points = {
        **{f"q{i}": ("Python Internals & Concurrency", 4) for i in range(1,6)},
        **{f"q{i}": ("API Architecture & Security", 4) for i in range(6,10)},
        **{f"q{i}": ("Django/FastAPI", 4) for i in range(10,14)},
        "q14": ("Advanced SQL / Database", 5), "q15": ("Advanced SQL / Database", 5), "q16": ("Advanced SQL / Database", 5),
        "q17": ("Distributed Systems", 5), "q18": ("Distributed Systems", 5), "q19": ("Distributed Systems", 5),
        "q20": ("Senior System Design", 10),
    }
    attempted = [q for q in question_points if str(answers.get(q, "")).strip()]
    unattempted = [q for q in question_points if q not in attempted]

    strengths = []
    for sec, val in section_scores.items():
        try:
            maxv = SECT_MAX.get(sec, 0)
            if maxv and float(val) >= maxv * 0.80:
                strengths.append(SECTION_NAMES.get(sec, sec))
        except Exception:
            pass
    if not strengths:
        strengths = ["Overall assessment completed; review section-level answers for detailed strengths."]

    filename = f"candidate_report_{re.sub(r'[^A-Za-z0-9_-]+','_',candidate).strip('_') or 'candidate'}.pdf"
    path = Path(tempfile.gettempdir()) / filename

    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReportTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=20, spaceAfter=8)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], alignment=TA_CENTER, fontSize=10, textColor=colors.grey)
    section = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    badge = ParagraphStyle("Badge", parent=styles["Heading1"], alignment=TA_CENTER, textColor=colors.white, fontSize=16)

    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=16*mm, bottomMargin=16*mm)
    story = [
        Paragraph("Python Backend Developer — Assessment Report", title),
        Paragraph(f"<b>Candidate:</b> {candidate}", sub),
        Spacer(1, 10),
    ]

    score_table = Table([
        [Paragraph("<b>FINAL SCORE</b>", body), Paragraph("<b>RATING</b>", body)],
        [Paragraph(f"<font size='24'><b>{score:.1f}%</b></font>", body), Paragraph(f"<font color='{rating_color.hexval()}' size='16'><b>{rating}</b></font>", body)],
        [Paragraph(summary, body), ""]
    ], colWidths=[80*mm, 80*mm])
    score_table.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),1,colors.lightgrey),
        ("GRID",(0,0),(-1,1),0.5,colors.lightgrey),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#F3F4F6")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("SPAN",(0,2),(1,2)),
        ("BACKGROUND",(0,1),(0,1),colors.HexColor("#DCFCE7") if score>=90 else colors.HexColor("#FEF9C3") if score>=70 else colors.HexColor("#FEE2E2")),
    ]))
    story += [score_table, Spacer(1, 12)]

    story.append(Paragraph("Rating Scale", section))
    scale = Table([
        ["Score", "Assessment"],
        ["90% – 100%", "OUTSTANDING"],
        ["70% – 89.9%", "GOOD"],
        ["0% – 69.9%", "BAD"],
    ], colWidths=[55*mm, 105*mm])
    scale.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E5E7EB")),
        ("GRID",(0,0),(-1,-1),0.5,colors.lightgrey),
        ("BACKGROUND",(0,1),(-1,1),colors.HexColor("#DCFCE7")),
        ("BACKGROUND",(0,2),(-1,2),colors.HexColor("#FEF9C3")),
        ("BACKGROUND",(0,3),(-1,3),colors.HexColor("#FEE2E2")),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
    ]))
    story += [scale]

    story.append(Paragraph("Section Scores", section))
    if section_scores:
        data = [["Section", "Score"]]
        for sec, val in section_scores.items():
            try:
                vv = float(val); maxv = SECT_MAX.get(sec, 0)
                pct = (vv / maxv * 100) if maxv else 0
                data.append([SECTION_NAMES.get(sec, sec), f"{vv:.2f}/{maxv} ({pct:.1f}%)"])
            except Exception:
                data.append([SECTION_NAMES.get(sec, sec), str(val)])
        t = Table(data, colWidths=[125*mm, 35*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E5E7EB")),
            ("GRID",(0,0),(-1,-1),0.5,colors.lightgrey),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ALIGN",(1,1),(-1,-1),"CENTER"),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("Section scores are not available.", body))

    story.append(Paragraph("Question Attempt Status", section))
    qdata = [["Question", "Status", "Score"]]
    # Reconstruct per-question automated scores for transparency.
    for q, (qsec, qmax) in question_points.items():
        raw = answers.get(q, "")
        if not str(raw).strip():
            qdata.append([q.upper(), "NOT ATTEMPTED", "0"]); continue
        if q == "q14":
            qscore = _question_score(raw, qmax, QUESTION_RUBRICS[q][1])
        else:
            qscore = _question_score(raw, qmax, QUESTION_RUBRICS[q][1])
        qdata.append([q.upper(), "ATTEMPTED", f"{qscore:.2f}/{qmax}"])
    qdata.append(["CODING", "ATTEMPTED" if (col("code") or "").strip() else "NOT ATTEMPTED", f"{_coding_score(col('code') or '', coding_tests):.2f}/10"])
    qt = Table(qdata, colWidths=[30*mm, 55*mm, 45*mm])
    qt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E5E7EB")),("GRID",(0,0),(-1,-1),0.4,colors.lightgrey),("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("ALIGN",(1,1),(-1,-1),"CENTER")]))
    story.append(qt)

    story.append(Paragraph("Technical Strengths", section))
    for x in strengths:
        story.append(Paragraph("• " + x, body))

    story.append(Paragraph("Technical Areas Where the Candidate Is Lacking", section))
    for x in technical_gaps:
        story.append(Paragraph("• " + x, body))

    story.append(Paragraph("Coding Test Analysis", section))
    if coding_tests:
        functional_names = ["api", "basic_get_set", "lru_eviction", "update_mru", "ttl_expiry", "zero_negative_ttl", "capacity_one", "stress_100k"]
        passed = [k for k in functional_names if coding_tests.get(k) is True]
        failed = [k for k in functional_names if coding_tests.get(k) is not True]
        story.append(Paragraph(f"Functional tests passed: {len(passed)}/{len(functional_names)}.", body))
        if failed: story.append(Paragraph("Failed coding checks: " + ", ".join(failed), body))
        diagnostics = analyze_code(col("code") or "")
        if diagnostics.get("issues"): story.append(Paragraph("Static code concerns: " + "; ".join(diagnostics["issues"]), body))
    else:
        story.append(Paragraph("No coding test results were recorded.", body))

    story.append(Paragraph("Hiring Feedback", section))
    story.append(Paragraph(
        f"The candidate achieved <b>{score:.1f}%</b> and is rated <b>{rating}</b>. "
        "This report is generated from the automated assessment rubric. "
        "Use the detailed answers and live-coding response for final technical validation.",
        body
    ))

    doc.build(story)

    from fastapi.responses import FileResponse
    return FileResponse(str(path), media_type="application/pdf", filename=filename)


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def admin():
    return FileResponse(BASE / "static/admin.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"})


@app.get("/assessment/{token}")
def candidate():
    return FileResponse(BASE / "static/assessment.html")
