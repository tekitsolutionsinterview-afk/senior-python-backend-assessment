# Senior Python Backend Assessment Platform

## Important: do not open `admin.html` directly

If the browser shows **"Could not generate link: Failed to fetch"**, the most common cause is that the HTML page was opened directly (`file://...`) or the FastAPI backend is not running.

### Windows — easiest method
1. Install Python 3.10+.
2. Double-click **`run_windows.bat`**.
3. Keep the black terminal window open.
4. The browser should open:
   **http://127.0.0.1:8000/**
5. Enter the candidate name and click **Generate Link**.

The Generate Link button calls `/api/admin/create`, so the FastAPI server must be running.

### Manual startup
```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Then open:
`http://127.0.0.1:8000/`

## Candidate flow
Admin generates a candidate record and receives:
- unique assessment URL
- Login ID
- Password

Candidate opens the assessment URL, enters the supplied credentials, grants camera access, enters fullscreen, and starts the 45-minute assessment.

## Assessment
- 45 minutes
- 100 points
- difficult Senior Python Backend questions
- live Python coding
- camera check-in
- fullscreen / visibility / blur event logging
- section-wise score and weak-area feedback in admin

## Production hardening
For internet-facing deployment, add HTTPS, admin authentication, expiring/signed candidate credentials, PostgreSQL, server-side timing, isolated code execution, stronger proctoring, audit logs, and privacy/consent controls.
