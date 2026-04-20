Activist Tracker — Setup Guide
Two parts: a Python backend that talks to SEC EDGAR, and an HTML frontend you open in your browser.
---
1. Install dependencies
You need Python 3.9+. In your terminal:
```bash
cd activist_backend
pip install -r requirements.txt
```
---
2. Configure email alerts (optional)
Copy the example env file and fill in your details:
```bash
cp .env.example .env
```
Open `.env` and set:
```
SMTP_USER=your@gmail.com
SMTP_PASS=your_app_password   # NOT your Gmail password — see below
ALERT_TO=you@email.com
```
Getting a Gmail App Password:
Go to myaccount.google.com → Security
Enable 2-Step Verification if not already on
Search for "App passwords" → Create one for "Mail"
Paste that 16-character password into SMTP_PASS
Skip this step entirely if you just want the live feed without email alerts.
---
3. Start the backend
```bash
cd activist_backend
uvicorn main:app --reload --port 8000
```
You should see:
```
✓ Scheduler started — daily alert at 7:00 AM
INFO:     Uvicorn running on http://127.0.0.1:8000
```
Verify it's working: open http://localhost:8000 in your browser. You should see:
```json
{"status": "ok", "message": "Activist Tracker API running"}
```
---
4. Open the frontend
Open `activist_tracker.html` directly in your browser (no server needed for the frontend).
Click the Live EDGAR Feed tab, set your date range, and hit Fetch filings.
---
API endpoints
Endpoint	What it does
`GET /api/filings`	Fetch filings with filters (forms, date range, page)
`GET /api/filings/today`	Just today's filings — used by the daily alert
`GET /api/alert/send`	Manually trigger a test email alert
Example:
```
http://localhost:8000/api/filings?date_from=2026-04-01&date_to=2026-04-20&forms=SC+13D,SC+13D%2FA,DFAN14A
```
---
Daily email alert
If email is configured, the backend sends a digest every morning at 7:00 AM automatically while it's running.
To test the email immediately without waiting:
```
http://localhost:8000/api/alert/send
```
---
Keeping the backend running
For now the backend runs as long as your terminal is open. To keep it running permanently, use a process manager:
macOS/Linux (simple):
```bash
nohup uvicorn main:app --port 8000 &
```
Or with pm2 (recommended):
```bash
npm install -g pm2
pm2 start "uvicorn main:app --port 8000" --name activist-tracker
pm2 startup  # auto-start on reboot
```
---
V2 — coming next
200-word Claude brief on each filing (summarises the actual letter)
Filtering by known activist fund names
Watchlist sync — auto-flag when an EDGAR filing matches a name in your watchlist
