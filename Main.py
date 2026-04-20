"""
Activist Tracker — Backend
Proxies SEC EDGAR filings API and serves data to the frontend.
Run with: uvicorn main:app --reload --port 8000
"""

import httpx
import smtplib
import json
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ──────────────────────────────────────────────────────────────────
EDGAR_SEARCH  = "https://efts.sec.gov/LATEST/search-index"
EDGAR_HEADERS = {
    # SEC fair-access policy requires a real User-Agent with contact info
    "User-Agent": "ActivistTracker/1.0 (research tool; contact: your@email.com)",
    "Accept": "application/json",
}

# Email config — set these in a .env file (see README)
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")          # your Gmail address
SMTP_PASS     = os.getenv("SMTP_PASS", "")          # Gmail app password
ALERT_TO      = os.getenv("ALERT_TO", "")           # recipient email

# Filing types that signal activist activity
ACTIVIST_FORMS = "SC+13D,SC+13D%2FA,DFAN14A"

# ── SCHEDULER ───────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the daily alert job at 7am
    scheduler.add_job(
        send_daily_alert,
        "cron",
        hour=7,
        minute=0,
        id="daily_alert",
        replace_existing=True,
    )
    scheduler.start()
    print("✓ Scheduler started — daily alert at 7:00 AM")
    yield
    scheduler.shutdown()

app = FastAPI(title="Activist Tracker API", lifespan=lifespan)

# Allow the frontend HTML (served from file:// or any localhost port) to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "message": "Activist Tracker API running"}


@app.get("/api/filings")
async def get_filings(
    forms: str = Query(default=ACTIVIST_FORMS, description="Comma-separated form types"),
    date_from: str = Query(default=None, description="Start date YYYY-MM-DD"),
    date_to: str   = Query(default=None, description="End date YYYY-MM-DD"),
    page: int      = Query(default=0, ge=0, description="Page number (0-indexed)"),
    page_size: int = Query(default=40, le=100, description="Results per page"),
):
    """
    Proxy to SEC EDGAR full-text search.
    Returns activist filings (SC 13D, SC 13D/A, DFAN14A) for the given date range.
    """
    # Default date range: last 30 days
    if not date_to:
        date_to = datetime.today().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    params = {
        "q": "",
        "forms": forms.replace("+", " "),   # httpx will re-encode
        "dateRange": "custom",
        "startdt": date_from,
        "enddt": date_to,
        "from": page * page_size,
        "hits.hits._source": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                EDGAR_SEARCH,
                params=params,
                headers=EDGAR_HEADERS,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="EDGAR request timed out")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"EDGAR error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    hits      = data.get("hits", {}).get("hits", [])
    total     = data.get("hits", {}).get("total", {}).get("value", 0)

    # Normalize each filing into a clean shape
    filings = []
    for h in hits:
        s = h.get("_source", {})
        filings.append({
            "file_date":     s.get("file_date"),
            "form_type":     s.get("form_type"),
            "entity_name":   s.get("entity_name"),
            "display_names": s.get("display_names", []),
            "accession_no":  s.get("accession_no"),
            "filing_url":    build_filing_url(s.get("accession_no"), s.get("display_names", [])),
        })

    return {
        "total":    total,
        "page":     page,
        "per_page": page_size,
        "filings":  filings,
    }


@app.get("/api/filings/today")
async def get_todays_filings():
    """Convenience endpoint — just today's filings. Used by the daily alert job."""
    today = datetime.today().strftime("%Y-%m-%d")
    return await get_filings(
        forms=ACTIVIST_FORMS,
        date_from=today,
        date_to=today,
        page=0,
        page_size=100,
    )


@app.get("/api/alert/send")
async def trigger_alert_manually():
    """Manually trigger the daily alert email. Useful for testing."""
    result = await send_daily_alert()
    return result


# ── HELPERS ──────────────────────────────────────────────────────────────────

def build_filing_url(accession_no: str, display_names: list) -> str:
    """
    Build a direct link to the filing index page on EDGAR.
    Format: https://www.sec.gov/Archives/edgar/data/{CIK}/{ACCNO_NODASH}/{ACCNO}-index.htm
    """
    try:
        cik = display_names[0]["id"].lstrip("0") if display_names else ""
        acc_nodash = accession_no.replace("-", "")
        if cik and acc_nodash:
            return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{accession_no}-index.htm"
    except Exception:
        pass
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=SC+13D&dateb=&owner=include&count=10"


def format_email_body(filings: list, date: str) -> str:
    """Build plain-text email body from a list of filings."""
    if not filings:
        return f"No new activist filings on {date}.\n"

    lines = [
        f"ACTIVIST TRACKER — Daily Alert",
        f"Date: {date}",
        f"New filings: {len(filings)}",
        "=" * 60,
        "",
    ]

    for f in filings:
        filers = ", ".join(d.get("name", "") for d in f.get("display_names", []))
        lines += [
            f"  Company:  {f.get('entity_name', '—')}",
            f"  Type:     {f.get('form_type', '—')}",
            f"  Filer:    {filers or '—'}",
            f"  Filed:    {f.get('file_date', '—')}",
            f"  Link:     {f.get('filing_url', '—')}",
            "",
        ]

    lines += [
        "=" * 60,
        "Powered by SEC EDGAR public data.",
    ]
    return "\n".join(lines)


async def send_daily_alert() -> dict:
    """
    Fetches today's filings and emails a digest.
    Called by the scheduler at 7am and can be triggered manually via /api/alert/send.
    """
    if not SMTP_USER or not SMTP_PASS or not ALERT_TO:
        msg = "Email not configured — set SMTP_USER, SMTP_PASS, ALERT_TO in .env"
        print(f"⚠  {msg}")
        return {"status": "skipped", "reason": msg}

    today = datetime.today().strftime("%Y-%m-%d")

    try:
        result = await get_todays_filings()
        filings = result.get("filings", [])
    except Exception as e:
        return {"status": "error", "reason": f"Failed to fetch filings: {e}"}

    body = format_email_body(filings, today)
    subject = f"Activist Tracker — {len(filings)} filing{'s' if len(filings)!=1 else ''} today ({today})"

    try:
        msg = MIMEMultipart()
        msg["From"]    = SMTP_USER
        msg["To"]      = ALERT_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        print(f"✓ Daily alert sent — {len(filings)} filings — {today}")
        return {"status": "sent", "filings_count": len(filings), "date": today}

    except Exception as e:
        print(f"✗ Email failed: {e}")
        return {"status": "error", "reason": str(e)}
