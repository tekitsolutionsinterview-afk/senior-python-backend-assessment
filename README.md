# Senior Python Backend Assessment — Fixed Render Version

This version fixes the live submission problem and adds persistent PostgreSQL support.

## What was fixed

- `/api/submit` is present and also accepts `/api/submit/`.
- Candidate submission errors are no longer silently ignored in the browser.
- Render can use PostgreSQL through `DATABASE_URL`.
- Local development still works with SQLite when `DATABASE_URL` is not set.
- Existing local `assessment.db` records can be migrated with `migrate_sqlite_to_postgres.py`.
- `assessment.db` is excluded from Git so candidate credentials/results are not published publicly.

## Deploy to your existing Render service

1. Replace the files in your GitHub repository with the files from this package.
2. Commit to the **main** branch.
3. Render should auto-deploy the new commit.
4. In Render, open the web service → **Environment**.
5. Add `DATABASE_URL` using the **Internal Database URL / connection string** for your Render PostgreSQL database. If Render offers “Add from database”, select `senior-python-backend-db` and its connection-string property.
6. Redeploy if Render does not automatically redeploy after the environment change.
7. Open `/health`. It should report `"database":"postgres"`.

## Restore the old local records

Keep your existing local `assessment.db` file. Do **not** upload it to GitHub.

After obtaining the Render PostgreSQL connection string, run:

```powershell
$env:DATABASE_URL = "<your Render PostgreSQL connection string>"
python migrate_sqlite_to_postgres.py
```

The migration is idempotent and uses the candidate `token` as the conflict key, so running it again does not duplicate records.

## Local run

Without `DATABASE_URL`, the application automatically uses `app/assessment.db`.

```powershell
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000/`.

## Important

Do not commit `assessment.db`, `.env`, passwords, or a PostgreSQL connection string to GitHub. Candidate assessment data should remain in the database.


## Candidate PDF Feedback
The Admin API now provides `/api/admin/session/{token}/report.pdf`, generating a PDF containing the final score, color-coded rating, section scores, technical strengths, and technical gaps.
