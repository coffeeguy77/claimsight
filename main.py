"""Contents claim assessment platform — web application."""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import secrets
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import attachments
import billing
import ingest
import legal
import report_view
import sharing
import valuation
from models import (
    Invitation,
    PasswordReset,
    ReportShare,
    ReportShareView,
    CONDITION_ADJUSTMENT,
    Item,
    ItemEvent,
    Job,
    JobStatus,
    Organisation,
    Photo,
    SessionToken,
    User,
    ValuationStatus,
    get_session,
    init_db,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Claimsight")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Brand assets. Served with a long cache; the filenames are stable.
app.mount("/static", StaticFiles(directory="static"), name="static")
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

SESSION_COOKIE = "claimsight_session"
SESSION_DAYS = 14
VALUATION_WORKERS = int(os.environ.get("VALUATION_WORKERS", "4"))

_executor = ThreadPoolExecutor(max_workers=VALUATION_WORKERS)
_job_locks: dict[str, threading.Lock] = {}

# Items handed to a worker but not yet finished. Guards against a second
# "Value remaining" click re-queueing work that is already in flight.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()

# Rolling average of how long one item takes, used to estimate time remaining.
_timing = {"seconds": 0.0, "count": 0}
_timing_lock = threading.Lock()


def _record_duration(seconds: float) -> None:
    with _timing_lock:
        _timing["seconds"] += seconds
        _timing["count"] += 1


def _average_seconds() -> float:
    with _timing_lock:
        if not _timing["count"]:
            return 0.0
        return _timing["seconds"] / _timing["count"]


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    _release_orphaned_valuations()


def _release_orphaned_valuations() -> None:
    """Reset items left mid-valuation by a restart.

    Valuation runs in-process, so a redeploy or crash strands items in the
    `running` state where the re-run button would never pick them up again.
    """
    db = get_session()
    try:
        stranded = db.scalars(
            select(Item).where(Item.valuation_status == ValuationStatus.running)
        ).all()
        for item in stranded:
            item.valuation_status = ValuationStatus.pending
        if stranded:
            db.commit()
            print(f"Released {len(stranded)} orphaned valuation(s) back to pending.")
    finally:
        db.close()


# --------------------------------------------------------------------------- auth


def db_dependency():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def _as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite hands back naive datetimes; Postgres hands back aware ones."""
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def current_user(request: Request, db: Session) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.get(SessionToken, token)
    if not row:
        return None
    expires = _as_utc(row.expires_at)
    if expires and expires < dt.datetime.now(dt.timezone.utc):
        db.delete(row)
        db.commit()
        return None
    return db.get(User, row.user_id)


def require_user(request: Request, db: Session = Depends(db_dependency)) -> User:
    user = current_user(request, db)
    if not user or not user.is_active:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_editor(request: Request, db: Session = Depends(db_dependency)) -> User:
    """Anyone who may change claim data. Viewers may look and print only."""
    user = require_user(request, db)
    if not user.can_edit:
        raise HTTPException(403, "Your account has read-only access to this claim.")
    return user


def require_owner(request: Request, db: Session = Depends(db_dependency)) -> User:
    """Billing, team and anything that spends the firm's money."""
    user = require_user(request, db)
    if not user.is_owner:
        raise HTTPException(403, "Only the account holder can do that.")
    return user


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.exception_handler(HTTPException)
async def redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def record_event(
    db: Session,
    item: Item,
    kind: str,
    summary: str,
    old_value: str = "",
    new_value: str = "",
    user: User | None = None,
) -> None:
    """Append an audit entry. Every assessor action against a line lands here."""
    db.add(
        ItemEvent(
            item_id=item.id,
            job_id=item.job_id,
            user_id=user.id if user else None,
            user_name=(user.name or user.email) if user else "Claimsight AI",
            kind=kind,
            summary=summary[:2000],
            old_value=str(old_value)[:120],
            new_value=str(new_value)[:120],
        )
    )


def _parse_money(raw) -> float:
    """Accept '1,250', 'A$1250', '' and return a float."""
    try:
        return float(str(raw).replace("$", "").replace("A", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _money(value) -> str:
    return f"A${value:,.2f}" if isinstance(value, (int, float)) else "—"


def _back(job_id: str, return_to: str = "", item_id: str = "") -> str:
    """Return to the same filtered page and selection after an action.

    Forms post a hidden `return_to` carrying the current query string, so the
    assessor is not dropped back to page 1 of an unfiltered list mid-review.
    """
    query = (return_to or "").lstrip("?")
    # Only ever reflect our own query string back as a relative path.
    if "//" in query or "\\" in query:
        query = ""
    if item_id:
        parts = [p for p in query.split("&") if p and not p.startswith("item=")]
        parts.append(f"item={item_id}")
        query = "&".join(parts)
    return f"/jobs/{job_id}?{query}" if query else f"/jobs/{job_id}"


# Evidence uploaded straight onto a line lives in its own photo series so it
# can never collide with a number paired out of the assessor's report PDF.
UPLOAD_SERIES = "upload"


def _with_notice(url: str, message: str) -> str:
    """Carry a one-off message back to the page after a redirect."""
    if not message:
        return url
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}notice={quote_plus(message[:400])}"


def _next_upload_number(db: Session, job_id: str) -> int:
    top = db.scalar(
        select(func.max(Photo.number)).where(
            Photo.job_id == job_id, Photo.series == UPLOAD_SERIES
        )
    )
    return (top or 0) + 1


def _store_uploads(
    db: Session, job: Job, item: Item, files: list[UploadFile], user: User
) -> tuple[int, list[str]]:
    """Attach uploaded files to a line as evidence pages.

    Returns how many pages were stored and any per-file problems, so one bad
    file does not silently discard the rest of the batch.
    """
    stored, errors = 0, []
    number = _next_upload_number(db, job.id)

    for upload in files or []:
        if not upload or not (upload.filename or "").strip():
            continue
        try:
            blob = upload.file.read()
        finally:
            upload.file.close()
        try:
            pages = attachments.render(upload.filename, blob)
        except attachments.UnsupportedUpload as exc:
            errors.append(str(exc))
            continue

        for page in pages:
            db.add(
                Photo(
                    job_id=job.id,
                    item_id=item.id,
                    kind="upload",
                    filename=(upload.filename or "")[:255],
                    series=UPLOAD_SERIES,
                    number=number,
                    caption=page.label[:500],
                    content_type=page.content_type,
                    width=page.width,
                    height=page.height,
                    data=page.data,
                )
            )
            number += 1
            stored += 1

    if stored:
        names = ", ".join(
            sorted({(f.filename or "").strip() for f in files if f and f.filename})
        )
        record_event(
            db, item, "evidence",
            f"Uploaded {stored} evidence page{'' if stored == 1 else 's'}: {names}"[:2000],
            new_value=f"{stored} page{'' if stored == 1 else 's'}",
            user=user,
        )
    return stored, errors


def owned_job(db: Session, job_id: str, user: User) -> Job:
    job = db.get(Job, job_id)
    if not job or job.organisation_id != user.organisation_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# -------------------------------------------------------------------- auth routes


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(db_dependency)):
    user = current_user(request, db)
    if user:
        return RedirectResponse("/jobs", status_code=303)
    return templates.TemplateResponse(
        request,
        "landing.html",
        {"plans": billing.PLANS, "paid_plans": billing.PAID_PLANS,
         "overage": billing.OVERAGE_PRICE,
         "now_year": dt.datetime.now(dt.timezone.utc).year})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(db_dependency),
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or not pwd_context.verify(password, user.password_hash):
        return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Incorrect email or password."},
            status_code=401,
        )
    token = SessionToken(
        token=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=SESSION_DAYS),
    )
    db.add(token)
    db.commit()
    response = RedirectResponse("/jobs", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token.token,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("FORCE_HTTPS", "1") == "1",
        max_age=SESSION_DAYS * 86400,
    )
    return response


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(
        request,
        "signup.html",
        {"error": None})


@app.post("/signup")
def signup(
    request: Request,
    organisation: str = Form(...),
    name: str = Form(""),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(db_dependency),
):
    email = email.strip().lower()
    if len(password) < 8:
        return templates.TemplateResponse(
        request,
        "signup.html",
        {"error": "Password must be at least 8 characters."},
            status_code=400,
        )
    if db.scalar(select(User).where(User.email == email)):
        return templates.TemplateResponse(
        request,
        "signup.html",
        {"error": "An account with that email already exists."},
            status_code=400,
        )
    org = Organisation(name=organisation.strip())
    db.add(org)
    db.flush()
    user = User(
        organisation_id=org.id,
        email=email,
        name=name.strip(),
        password_hash=pwd_context.hash(password),
        role="owner",
    )
    db.add(user)
    billing.start_trial(org)
    db.commit()
    return login(request, email=email, password=password, db=db)


@app.get("/logout")
def logout(request: Request, db: Session = Depends(db_dependency)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        row = db.get(SessionToken, token)
        if row:
            db.delete(row)
            db.commit()
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# -------------------------------------------------------------------- job routes


@app.get("/jobs", response_class=HTMLResponse)
def list_jobs(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    jobs = db.scalars(
        select(Job)
        .where(Job.organisation_id == user.organisation_id)
        .order_by(Job.created_at.desc())
    ).all()
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"user": user, "jobs": jobs}
    )


@app.get("/jobs/new", response_class=HTMLResponse)
def new_job_form(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(
        request,
        "new_job.html",
        {"user": user})


@app.post("/jobs")
async def create_job(
    request: Request,
    reference: str = Form(""),
    claim_reference: str = Form(""),
    insured_name: str = Form(""),
    insurer: str = Form(""),
    site_address: str = Form(""),
    peril: str = Form("Water"),
    date_of_loss: str = Form(""),
    policy_excess: str = Form("0"),
    apply_depreciation: str = Form(""),
    inventory: UploadFile = File(...),
    photos_initial: UploadFile | None = File(None),
    photos_second: UploadFile | None = File(None),
    user: User = Depends(require_editor),
    db: Session = Depends(db_dependency),
):
    org = db.get(Organisation, user.organisation_id)
    billing.roll_period_if_due(org)
    blocked = billing.gate(org)
    if blocked:
        db.commit()
        return RedirectResponse(
            f"/settings/billing?notice={quote_plus(blocked)}", status_code=303
        )

    job = Job(
        organisation_id=user.organisation_id,
        created_by_id=user.id,
        reference=reference.strip(),
        claim_reference=claim_reference.strip(),
        insured_name=insured_name.strip(),
        insurer=insurer.strip(),
        site_address=site_address.strip(),
        peril=peril.strip(),
        date_of_loss=date_of_loss.strip(),
        policy_excess=_parse_money(policy_excess),
        apply_depreciation=apply_depreciation == "on",
        status=JobStatus.ingesting,
    )
    db.add(job)
    billing.count_claim(org)
    db.commit()

    tmpdir = tempfile.mkdtemp(prefix="claimsight_")
    try:
        xlsx_path = os.path.join(tmpdir, "inventory.xlsx")
        with open(xlsx_path, "wb") as fh:
            fh.write(await inventory.read())

        pdf_sources = []
        for upload, series in ((photos_initial, "initial"), (photos_second, "second")):
            if upload and upload.filename:
                path = os.path.join(tmpdir, f"{series}.pdf")
                with open(path, "wb") as fh:
                    fh.write(await upload.read())
                pdf_sources.append((path, series))

        _ingest_into_job(db, job, xlsx_path, pdf_sources)
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.failed
        job.status_detail = f"{type(exc).__name__}: {exc}"
        db.commit()
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)
    finally:
        for name in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, name))
        os.rmdir(tmpdir)

    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


def _ingest_into_job(db: Session, job: Job, xlsx_path: str, pdf_sources: list[tuple[str, str]]) -> None:
    data = ingest.build_job(xlsx_path, pdf_sources)

    for photo in data.photos:
        db.add(
            Photo(
                job_id=job.id,
                series=photo.series,
                number=photo.number,
                page=photo.page,
                caption=photo.caption,
                content_type=photo.content_type,
                width=photo.width,
                height=photo.height,
                data=photo.image,
            )
        )

    for order, item in enumerate(data.items):
        db.add(
            Item(
                job_id=job.id,
                sort_order=order,
                description=item.description,
                quantity=_infer_quantity(item.description),
                make=item.make,
                model=item.model,
                serial=item.serial,
                location=item.location,
                cause_of_damage=item.cause_of_damage,
                photo_series=item.series,
                photo_refs=",".join(str(n) for n in item.photo_numbers),
                assessor_note=item.assessor_note,
            )
        )

    job.status = JobStatus.ready
    job.status_detail = f"{len(data.items)} items, {len(data.photos)} photos ingested."
    if not job.site_address and data.site:
        job.site_address = data.site
    db.commit()


_QTY_RE = re.compile(r"^\s*(\d+)\s*(?:x|X)?\s+")


def _infer_quantity(description: str) -> int:
    """'2 x Extractor Fans' -> 2. Bulk descriptions like '188 x Magazines' stay as-is."""
    match = _QTY_RE.match(description)
    if match:
        value = int(match.group(1))
        if 1 <= value <= 500:
            return value
    return 1


FILTERS = ("all", "review", "high", "medium", "low", "overridden", "unvalued", "excluded")
PAGE_SIZES = (25, 50, 100, 250)


def _photo_index(db: Session, job_id: str) -> dict:
    """Photo metadata keyed by 'series:number', WITHOUT the image bytes.

    Loading the relationship pulls every blob into memory (megabytes per
    request); only the id is needed to build an <img> URL.
    """
    rows = db.execute(
        select(Photo.id, Photo.series, Photo.number, Photo.caption, Photo.kind, Photo.filename)
        .where(Photo.job_id == job_id)
    ).all()
    return {
        f"{series}:{number}": {
            "id": pid,
            "caption": caption,
            "number": number,
            "kind": kind or "report",
            "filename": filename or "",
        }
        for pid, series, number, caption, kind, filename in rows
    }


def _matches_filter(row, key: str) -> bool:
    if key == "review":
        return not row.excluded and (
            row.flagged
            or row.confidence == "low"
            or row.valuation_status == ValuationStatus.failed
            or not row.reviewed
        )
    if key in ("high", "medium", "low"):
        return row.confidence == key and not row.excluded
    if key == "overridden":
        return bool(row.manual_override) or row.valuation_status == ValuationStatus.overridden
    if key == "unvalued":
        return row.valuation_status in (
            ValuationStatus.pending, ValuationStatus.running, ValuationStatus.failed
        )
    if key == "excluded":
        return bool(row.excluded)
    return True


_SORTERS = {
    "order": lambda i: (i.sort_order or 0),
    "name": lambda i: (i.description or "").lower(),
    "value_desc": lambda i: -((i.replacement_value or 0) * (i.quantity or 1)),
    "value_asc": lambda i: ((i.replacement_value or 0) * (i.quantity or 1)),
    "confidence": lambda i: {"low": 0, "medium": 1, "high": 2}.get(i.confidence, -1),
}


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: str,
    request: Request,
    q: str = "",
    view: str = "all",
    sort: str = "order",
    category: str = "",
    location: str = "",
    page: int = 1,
    per_page: int = 25,
    item: str = "",
    tab: str = "overview",
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    photos = _photo_index(db, job.id)
    all_items = job.items

    view = view if view in FILTERS else "all"
    per_page = per_page if per_page in PAGE_SIZES else 25

    # Chip counts always reflect the whole claim, not the filtered view.
    counts = {key: sum(1 for i in all_items if _matches_filter(i, key)) for key in FILTERS}

    rows = [i for i in all_items if _matches_filter(i, view)]
    if category:
        rows = [i for i in rows if (i.category or "") == category]
    if location:
        rows = [i for i in rows if (i.location or "") == location]
    if q.strip():
        needle = q.strip().lower()
        rows = [
            i for i in rows
            if needle in " ".join([
                i.description or "", i.make or "", i.model or "", i.serial or "",
                i.identified_as or "", i.category or "", i.location or "",
                i.valuation_notes or "",
            ]).lower()
        ]

    rows.sort(key=_SORTERS.get(sort, _SORTERS["order"]))

    total_rows = len(rows)
    pages = max(1, (total_rows + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]

    selected = next((i for i in all_items if i.id == item), None)
    if selected is None and page_rows:
        selected = page_rows[0]

    events = []
    if selected is not None:
        events = db.scalars(
            select(ItemEvent)
            .where(ItemEvent.item_id == selected.id)
            .order_by(ItemEvent.created_at.desc())
            .limit(12)
        ).all()

    return templates.TemplateResponse(
        request,
        "job.html",
        {"user": user,
            "job": job,
            "photos": photos,
            "totals": job.totals,
            "rows": page_rows,
            "counts": counts,
            "selected": selected,
            "events": events,
            "categories": sorted({i.category for i in all_items if i.category}),
            "locations": sorted({i.location for i in all_items if i.location}),
            "q": q,
            "view": view,
            "sort": sort,
            "category": category,
            "location": location,
            "page": page,
            "pages": pages,
            "per_page": per_page,
            "per_page_options": PAGE_SIZES,
            "total_rows": total_rows,
            "range_start": start + 1 if total_rows else 0,
            "range_end": min(start + per_page, total_rows),
            "tab": tab,
            "conditions": list(CONDITION_ADJUSTMENT.keys()),
            "pending": sum(1 for i in all_items if i.valuation_status == ValuationStatus.pending),
            "running": sum(1 for i in all_items if i.valuation_status == ValuationStatus.running),
            "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "model": valuation.MODEL,
            "max_searches": valuation.MAX_SEARCHES,
        },
    )


@app.post("/jobs/{job_id}/settings")
def update_job_settings(
    job_id: str,
    apply_depreciation: str = Form(""),
    policy_excess: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Switch the settlement basis and excess without touching valuations."""
    job = owned_job(db, job_id, user)
    job.apply_depreciation = apply_depreciation == "on"
    if policy_excess.strip():
        try:
            job.policy_excess = float(policy_excess.replace("$", "").replace(",", ""))
        except ValueError:
            pass
    db.commit()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/photos/{photo_id}")
def photo_bytes(
    photo_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    photo = db.get(Photo, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found")
    job = db.get(Job, photo.job_id)
    if job.organisation_id != user.organisation_id:
        raise HTTPException(404, "Photo not found")
    return Response(
        photo.data,
        media_type=photo.content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ------------------------------------------------------------------- valuation


@app.post("/jobs/{job_id}/value")
def start_valuation(
    job_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        job.status_detail = "ANTHROPIC_API_KEY is not set on this deployment."
        db.commit()
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    with _inflight_lock:
        targets = [
            i.id
            for i in job.items
            if not i.excluded
            and not i.manual_override
            and i.valuation_status in (ValuationStatus.pending, ValuationStatus.failed)
            and i.id not in _inflight
        ]
        _inflight.update(targets)

    if not targets:
        job.status_detail = "Nothing left to value."
        db.commit()
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # Items stay pending (= queued) and are flipped to running by the worker
    # that actually picks them up, so the UI can distinguish the two.
    for item in job.items:
        if item.id in targets and item.valuation_status == ValuationStatus.failed:
            item.valuation_status = ValuationStatus.pending
    job.status = JobStatus.valuing
    job.status_detail = f"Queued {len(targets)} items for valuation."
    db.commit()

    _executor.submit(_run_valuations, job_id, targets)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


def _run_valuations(job_id: str, item_ids: list[str]) -> None:
    """Background worker: value each item, committing as it goes."""
    lock = _job_locks.setdefault(job_id, threading.Lock())
    with lock:
        inner = ThreadPoolExecutor(max_workers=VALUATION_WORKERS)
        futures = [inner.submit(_value_one, item_id) for item_id in item_ids]
        for future in futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        inner.shutdown(wait=True)

        db = get_session()
        try:
            job = db.get(Job, job_id)
            if job:
                outstanding = sum(
                    1
                    for i in job.items
                    if i.valuation_status in (ValuationStatus.pending, ValuationStatus.running)
                    and not i.excluded
                )
                failed = sum(1 for i in job.items if i.valuation_status == ValuationStatus.failed)
                job.status = JobStatus.valued if outstanding == 0 else JobStatus.ready
                job.status_detail = (
                    f"Valuation complete. {failed} item(s) need attention."
                    if failed
                    else "Valuation complete."
                )
                db.commit()
        finally:
            db.close()


def _value_one(item_id: str) -> None:
    started = time.monotonic()
    db = get_session()
    try:
        item = db.get(Item, item_id)
        if item is None:
            return
        # Claim it now so the UI shows this one as actively researching.
        item.valuation_status = ValuationStatus.running
        db.commit()

        captions = [
            p.caption
            for p in db.scalars(
                select(Photo).where(Photo.job_id == item.job_id, Photo.series == item.photo_series)
            ).all()
            if p.key in item.photo_key_list and p.caption
        ]
        result = valuation.value_item(item, captions)

        item.cost_usd = (item.cost_usd or 0.0) + (result.cost_usd or 0.0)
        item.search_count = (item.search_count or 0) + (result.searches or 0)
        item.valuation_model = result.model

        if result.error:
            item.valuation_status = ValuationStatus.failed
            item.error = result.error[:2000]
        else:
            item.valuation_status = ValuationStatus.complete
            item.error = ""
            item.identified_as = result.identified_as
            item.category = result.category
            item.replacement_value = result.replacement_value_aud
            item.indemnity_value = result.indemnity_value_aud
            item.depreciation_rate = result.depreciation_rate
            item.estimated_age_years = result.estimated_age_years
            item.effective_life_years = result.effective_life_years
            item.confidence = result.confidence
            item.valuation_notes = result.notes
            item.sources = "\n".join(result.sources or [])
        item.valued_at = dt.datetime.now(dt.timezone.utc)
        if not result.error:
            item.recompute_indemnity()
            record_event(
                db, item, "valuation",
                f"AI valuation: {result.identified_as[:120]}" if result.identified_as
                else "AI valuation completed",
                new_value=_money(item.replacement_value),
            )
        else:
            record_event(db, item, "valuation", "AI research failed", new_value="")
        db.commit()

        elapsed = time.monotonic() - started
        _record_duration(elapsed)
        # Surfaced in the Railway logs so a long run can be watched from outside.
        print(
            f"[valuation] {item.description[:44]!r} -> "
            f"{'FAILED' if result.error else f'AUD {item.replacement_value}'} "
            f"in {elapsed:.0f}s | {result.searches} searches | "
            f"US${result.cost_usd:.4f}",
            flush=True,
        )
    finally:
        with _inflight_lock:
            _inflight.discard(item_id)
        db.close()


@app.get("/jobs/{job_id}/progress")
def valuation_progress(
    job_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    counts = {status.value: 0 for status in ValuationStatus}
    rows = []
    for item in job.items:
        counts[item.valuation_status.value] += 1
        if item.valuation_status == ValuationStatus.running:
            rows.append(item.description[:60])

    total = sum(counts.values())
    done = counts["complete"] + counts["overridden"] + counts["failed"]
    outstanding = counts["pending"] + counts["running"]

    average = _average_seconds()
    eta = None
    if outstanding and average:
        # Items run VALUATION_WORKERS at a time.
        eta = int((outstanding / max(VALUATION_WORKERS, 1)) * average)

    return {
        "status": job.status.value,
        "detail": job.status_detail,
        "counts": counts,
        "totals": job.totals,
        "done": done,
        "total": total,
        "outstanding": outstanding,
        "percent": round(done / total * 100) if total else 0,
        "eta_seconds": eta,
        "average_seconds": round(average, 1),
        "researching": rows,
        "workers": VALUATION_WORKERS,
        "cost": job.totals["research_cost"],
        "cost_projected": round(
            job.totals["research_cost"] / done * total, 2
        ) if done else None,
    }


@app.post("/jobs/{job_id}/items")
def add_item(
    job_id: str,
    return_to: str = Form(""),
    description: str = Form(...),
    quantity: int = Form(1),
    make: str = Form(""),
    model: str = Form(""),
    serial: str = Form(""),
    location: str = Form(""),
    cause_of_damage: str = Form(""),
    assessor_note: str = Form(""),
    photo_series: str = Form(""),
    photo_refs: str = Form(""),
    value_now: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Add a line the assessor missed, optionally valuing it immediately."""
    job = owned_job(db, job_id, user)
    if not description.strip():
        raise HTTPException(400, "Description is required.")

    highest = max((i.sort_order or 0) for i in job.items) if job.items else 0
    item = Item(
        job_id=job.id,
        sort_order=highest + 1,
        description=description.strip(),
        quantity=max(1, quantity),
        make=make.strip(),
        model=model.strip(),
        serial=serial.strip(),
        location=location.strip() or "Storage shed",
        cause_of_damage=cause_of_damage.strip() or job.peril,
        assessor_note=assessor_note.strip(),
        photo_series=photo_series.strip(),
        photo_refs=",".join(re.findall(r"\d+", photo_refs)),
        valuation_status=ValuationStatus.pending,
    )
    db.add(item)
    db.flush()

    stored, errors = _store_uploads(db, job, item, files, user)
    db.commit()

    if value_now == "on" and os.environ.get("ANTHROPIC_API_KEY"):
        with _inflight_lock:
            _inflight.add(item.id)
        _executor.submit(_value_one, item.id)

    target = _back(job_id, return_to, item.id)
    if errors:
        target = _with_notice(target, " ".join(errors))
    elif stored:
        target = _with_notice(
            target, f"Item added with {stored} evidence page{'' if stored == 1 else 's'}."
        )
    return RedirectResponse(target, status_code=303)


@app.post("/items/{item_id}/edit")
def edit_item(
    item_id: str,
    return_to: str = Form(""),
    description: str = Form(...),
    quantity: int = Form(1),
    make: str = Form(""),
    model: str = Form(""),
    serial: str = Form(""),
    location: str = Form(""),
    cause_of_damage: str = Form(""),
    assessor_note: str = Form(""),
    photo_refs: str = Form(""),
    revalue: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Correct an item's identifying detail so the AI can research it properly."""
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)

    item.description = description.strip() or item.description
    item.quantity = max(1, quantity)
    item.make = make.strip()
    item.model = model.strip()
    item.serial = serial.strip()
    item.location = location.strip()
    item.cause_of_damage = cause_of_damage.strip()
    item.assessor_note = assessor_note.strip()
    if photo_refs.strip():
        item.photo_refs = ",".join(re.findall(r"\d+", photo_refs))
    db.commit()

    if revalue == "on" and os.environ.get("ANTHROPIC_API_KEY"):
        with _inflight_lock:
            if item_id not in _inflight:
                _inflight.add(item_id)
                item.valuation_status = ValuationStatus.pending
                item.manual_override = False
                db.commit()
                _executor.submit(_value_one, item_id)

    return RedirectResponse(_back(job.id, return_to, item.id), status_code=303)


@app.post("/items/{item_id}/delete")
def delete_item(
    item_id: str,
    return_to: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)
    db.delete(item)
    db.commit()
    return RedirectResponse(_back(job.id, return_to), status_code=303)


@app.post("/items/{item_id}/value")
def value_single_item(
    item_id: str,
    return_to: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Re-run valuation for one line without touching the rest of the job."""
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(400, "ANTHROPIC_API_KEY is not configured.")

    with _inflight_lock:
        if item_id in _inflight:
            return RedirectResponse(_back(job.id, return_to, item_id), status_code=303)
        _inflight.add(item_id)

    item.valuation_status = ValuationStatus.pending
    item.manual_override = False
    db.commit()
    _executor.submit(_value_one, item_id)
    return RedirectResponse(_back(job.id, return_to, item_id), status_code=303)


@app.post("/items/{item_id}")
def update_item(
    request: Request,
    item_id: str,
    return_to: str = Form(""),
    replacement_value: str = Form(""),
    quantity: int = Form(1),
    excluded: str = Form(""),
    valuation_notes: str = Form(""),
    condition: str = Form(""),
    depreciation_override: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)

    was_excluded = bool(item.excluded)
    previous_settlement = item.indemnity_value

    item.quantity = max(1, quantity)
    item.excluded = excluded == "on"
    if item.excluded != was_excluded:
        record_event(
            db, item, "exclude",
            "Excluded from claim" if item.excluded else "Reinstated to claim",
            user=user,
        )
    if valuation_notes:
        item.valuation_notes = valuation_notes

    if condition and condition != (item.condition or "average"):
        record_event(
            db, item, "condition", f"Condition changed to {condition.title()}",
            old_value=(item.condition or "average").title(), new_value=condition.title(),
            user=user,
        )
        item.condition = condition

    if depreciation_override.strip():
        try:
            pct = float(depreciation_override.replace("%", "").strip())
            rate = min(max(pct / 100.0, 0.0), 0.95)
            if item.depreciation_override != rate:
                record_event(
                    db, item, "depreciation", f"Depreciation set to {pct:.0f}%",
                    old_value=f"{(item.effective_depreciation * 100):.0f}%",
                    new_value=f"{pct:.0f}%", user=user,
                )
            item.depreciation_override = rate
        except ValueError:
            pass

    if replacement_value.strip():
        try:
            value = float(replacement_value.replace("$", "").replace(",", ""))
            if value != item.replacement_value:
                record_event(
                    db, item, "override", "Replacement cost overridden by assessor",
                    old_value=_money(item.replacement_value), new_value=_money(value),
                    user=user,
                )
            item.replacement_value = value
            item.manual_override = True
            item.valuation_status = ValuationStatus.overridden
        except ValueError:
            pass

    # Recompute from the current replacement cost, condition and override.
    item.recompute_indemnity()
    if previous_settlement != item.indemnity_value and item.indemnity_value is not None:
        record_event(
            db, item, "settlement", "Settlement value recalculated",
            old_value=_money(previous_settlement), new_value=_money(item.indemnity_value),
            user=user,
        )
    db.commit()
    return RedirectResponse(_back(job.id, return_to, item.id), status_code=303)


@app.post("/items/{item_id}/state")
def set_item_state(
    request: Request,
    item_id: str,
    action: str = Form(...),
    return_to: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Flag for review, clear a flag, or mark a line as reviewed/approved."""
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)

    if action == "flag":
        item.flagged = True
        record_event(db, item, "flag", "Flagged for review", user=user)
    elif action == "unflag":
        item.flagged = False
        record_event(db, item, "flag", "Review flag cleared", user=user)
    elif action == "review":
        item.reviewed = True
        item.flagged = False
        record_event(db, item, "review", "Reviewed and approved by assessor", user=user)
    elif action == "unreview":
        item.reviewed = False
        record_event(db, item, "review", "Approval withdrawn", user=user)
    elif action == "accept":
        # Accept the AI valuation as it stands.
        item.reviewed = True
        item.manual_override = False
        item.recompute_indemnity()
        record_event(
            db, item, "review", "AI valuation accepted",
            new_value=_money(item.replacement_value), user=user,
        )
    db.commit()
    return RedirectResponse(_back(job.id, return_to, item.id), status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    page = legal.PAGES["/privacy"]
    return templates.TemplateResponse(request, "legal.html",
                                      {"title": page["title"], "body": page["body"], "path": "/privacy"})


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    page = legal.PAGES["/terms"]
    return templates.TemplateResponse(request, "legal.html",
                                      {"title": page["title"], "body": page["body"], "path": "/terms"})


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    """Index the marketing page. Never index a published claim document."""
    root = _base_url(request)
    return (
        "User-agent: *\n"
        "Allow: /$\n"
        "Disallow: /r/\n"
        "Disallow: /jobs\n"
        "Disallow: /settings\n"
        "Allow: /privacy\n"
        "Allow: /terms\n"
        "Disallow: /invite/\n"
        "Disallow: /reset/\n"
        f"Sitemap: {root}/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
def sitemap(request: Request):
    root = _base_url(request)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{root}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>"
        f"<url><loc>{root}/privacy</loc><priority>0.3</priority></url>"
        f"<url><loc>{root}/terms</loc><priority>0.3</priority></url>"
        "</urlset>"
    )
    return Response(body, media_type="application/xml")


# ------------------------------------------------------------------ team seats


@app.get("/settings/team", response_class=HTMLResponse)
def team(request: Request, user: User = Depends(require_owner),
         db: Session = Depends(db_dependency)):
    org = db.get(Organisation, user.organisation_id)
    members = sorted(org.users, key=lambda u: (u.role != "owner", u.name or u.email))
    invites = [i for i in org.invitations if i.is_open]
    return templates.TemplateResponse(request, "team.html", {
        "user": user, "org": org, "members": members, "invites": invites,
        "base_url": _base_url(request), "active_section": "settings",
        "notice": request.query_params.get("notice", ""),
    })


@app.post("/team/invite")
def invite_member(request: Request, email: str = Form(...), role: str = Form("assessor"),
                  user: User = Depends(require_owner),
                  db: Session = Depends(db_dependency)):
    """Issue a seat as a copyable link.

    No mail service is configured on this deployment, so emailing the invite
    would fail silently. The account holder copies the link and sends it the
    way they already talk to their staff.
    """
    email = email.strip().lower()
    if role not in ("assessor", "viewer", "owner"):
        role = "assessor"
    if db.scalar(select(User).where(User.email == email)):
        return RedirectResponse(
            f"/settings/team?notice={quote_plus('That email already has an account.')}",
            status_code=303)

    invite = Invitation(
        organisation_id=user.organisation_id, email=email, role=role,
        invited_by=user.id,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=14),
    )
    db.add(invite)
    db.commit()
    return RedirectResponse(
        f"/settings/team?notice={quote_plus('Invitation ready — copy the link and send it to ' + email)}",
        status_code=303)


@app.post("/team/invite/{invite_id}/revoke")
def revoke_invite(invite_id: str, user: User = Depends(require_owner),
                  db: Session = Depends(db_dependency)):
    invite = db.get(Invitation, invite_id)
    if not invite or invite.organisation_id != user.organisation_id:
        raise HTTPException(404, "Invitation not found")
    db.delete(invite)
    db.commit()
    return RedirectResponse("/settings/team?notice=Invitation+revoked.", status_code=303)


@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_form(request: Request, token: str, db: Session = Depends(db_dependency)):
    invite = db.scalar(select(Invitation).where(Invitation.token == token))
    if not invite or not invite.is_open:
        return templates.TemplateResponse(
            request, "accept_invite.html",
            {"invite": None, "error": "This invitation has expired or has already been used."},
            status_code=404)
    return templates.TemplateResponse(request, "accept_invite.html",
                                      {"invite": invite, "error": None})


@app.post("/invite/{token}")
def accept_invite(request: Request, token: str, name: str = Form(""),
                  password: str = Form(...), db: Session = Depends(db_dependency)):
    invite = db.scalar(select(Invitation).where(Invitation.token == token))
    if not invite or not invite.is_open:
        raise HTTPException(404, "This invitation has expired or has already been used.")
    if len(password) < 8:
        return templates.TemplateResponse(
            request, "accept_invite.html",
            {"invite": invite, "error": "Password must be at least 8 characters."},
            status_code=400)
    if db.scalar(select(User).where(User.email == invite.email)):
        raise HTTPException(400, "That email already has an account.")

    member = User(organisation_id=invite.organisation_id, email=invite.email,
                  name=name.strip(), password_hash=pwd_context.hash(password),
                  role=invite.role)
    db.add(member)
    invite.accepted_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return login(request, email=invite.email, password=password, db=db)


@app.post("/team/{member_id}/role")
def change_role(member_id: str, role: str = Form(...),
                user: User = Depends(require_owner),
                db: Session = Depends(db_dependency)):
    member = db.get(User, member_id)
    if not member or member.organisation_id != user.organisation_id:
        raise HTTPException(404, "Member not found")
    if role not in ("owner", "assessor", "viewer"):
        raise HTTPException(400, "Unknown role")

    org = db.get(Organisation, user.organisation_id)
    owners = [u for u in org.users if u.role == "owner" and u.is_active]
    if member.role == "owner" and role != "owner" and len(owners) <= 1:
        return RedirectResponse(
            "/settings/team?notice=" + quote_plus(
                "An organisation must keep at least one account holder."),
            status_code=303)
    member.role = role
    db.commit()
    return RedirectResponse("/settings/team?notice=Role+updated.", status_code=303)


@app.post("/team/{member_id}/active")
def set_member_active(member_id: str, active: str = Form(""),
                      user: User = Depends(require_owner),
                      db: Session = Depends(db_dependency)):
    member = db.get(User, member_id)
    if not member or member.organisation_id != user.organisation_id:
        raise HTTPException(404, "Member not found")
    if member.id == user.id:
        return RedirectResponse(
            "/settings/team?notice=" + quote_plus("You cannot deactivate yourself."),
            status_code=303)
    member.is_active = active == "on"
    if not member.is_active:
        for row in db.scalars(select(SessionToken).where(SessionToken.user_id == member.id)):
            db.delete(row)
    db.commit()
    return RedirectResponse("/settings/team?notice=Access+updated.", status_code=303)


@app.post("/team/{member_id}/reset-link")
def issue_reset(request: Request, member_id: str, user: User = Depends(require_owner),
                db: Session = Depends(db_dependency)):
    """Owner-issued password reset.

    A self-service 'forgot password' flow needs email delivery, which this
    deployment does not have. Until it does, the account holder issues a
    single-use link and passes it on.
    """
    member = db.get(User, member_id)
    if not member or member.organisation_id != user.organisation_id:
        raise HTTPException(404, "Member not found")
    reset = PasswordReset(
        user_id=member.id,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24),
    )
    db.add(reset)
    db.commit()
    link = f"{_base_url(request)}/reset/{reset.token}"
    return RedirectResponse(
        "/settings/team?notice=" + quote_plus(
            f"Single-use reset link for {member.email}, valid 24 hours: {link}"),
        status_code=303)


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str, db: Session = Depends(db_dependency)):
    reset = db.get(PasswordReset, token)
    valid = bool(reset and not reset.used_at and _as_utc(reset.expires_at)
                 and _as_utc(reset.expires_at) > dt.datetime.now(dt.timezone.utc))
    return templates.TemplateResponse(request, "reset.html", {
        "token": token, "valid": valid, "error": None,
    }, status_code=200 if valid else 404)


@app.post("/reset/{token}")
def do_reset(request: Request, token: str, password: str = Form(...),
             db: Session = Depends(db_dependency)):
    reset = db.get(PasswordReset, token)
    if not reset or reset.used_at or not _as_utc(reset.expires_at) or \
            _as_utc(reset.expires_at) < dt.datetime.now(dt.timezone.utc):
        raise HTTPException(404, "This reset link has expired.")
    if len(password) < 8:
        return templates.TemplateResponse(request, "reset.html", {
            "token": token, "valid": True,
            "error": "Password must be at least 8 characters."}, status_code=400)

    member = db.get(User, reset.user_id)
    member.password_hash = pwd_context.hash(password)
    reset.used_at = dt.datetime.now(dt.timezone.utc)
    # Any session opened with the old password is no longer trusted.
    for row in db.scalars(select(SessionToken).where(SessionToken.user_id == member.id)):
        db.delete(row)
    db.commit()
    return login(request, email=member.email, password=password, db=db)


# --------------------------------------------------------------------- billing


@app.get("/settings/billing", response_class=HTMLResponse)
def billing_page(request: Request, user: User = Depends(require_owner),
                 db: Session = Depends(db_dependency)):
    org = db.get(Organisation, user.organisation_id)
    if billing.roll_period_if_due(org):
        db.commit()
    return templates.TemplateResponse(request, "billing.html", {
        "user": user, "org": org, "plans": billing.PLANS,
        "paid_plans": billing.PAID_PLANS, "overage": billing.OVERAGE_PRICE,
        "stripe_live": billing.configured(),
        "local_mode": billing.local_mode_allowed(),
        "gate": billing.gate(org),
        "notice": request.query_params.get("notice", ""),
        "checkout": request.query_params.get("checkout", ""),
        "active_section": "settings",
    })


@app.post("/billing/subscribe")
def subscribe(request: Request, plan: str = Form(...),
              user: User = Depends(require_owner),
              db: Session = Depends(db_dependency)):
    if plan not in billing.PAID_PLANS:
        raise HTTPException(400, "Unknown plan")
    org = db.get(Organisation, user.organisation_id)

    if not billing.configured():
        if not billing.local_mode_allowed():
            return RedirectResponse(
                "/settings/billing?notice=" + quote_plus(
                    "Payments are not configured yet, so plans cannot be changed. "
                    "Set the Stripe keys to start taking subscriptions."),
                status_code=303)
        # Local mode, explicitly enabled: apply the plan without taking money.
        org.plan, org.plan_status = plan, "active"
        org.period_start = dt.datetime.now(dt.timezone.utc)
        org.claims_used = 0
        db.commit()
        return RedirectResponse(
            "/settings/billing?notice=" + quote_plus(
                "Plan applied locally. Stripe is not configured, so no payment was taken."),
            status_code=303)

    try:
        url = billing.checkout_url(org, plan, user.email, _base_url(request))
    except RuntimeError as exc:
        return RedirectResponse(
            f"/settings/billing?notice={quote_plus(str(exc))}", status_code=303)
    db.commit()
    return RedirectResponse(url, status_code=303)


@app.post("/billing/portal")
def billing_portal(request: Request, user: User = Depends(require_owner),
                   db: Session = Depends(db_dependency)):
    org = db.get(Organisation, user.organisation_id)
    url = billing.portal_url(org, _base_url(request))
    if not url:
        return RedirectResponse(
            "/settings/billing?notice=" + quote_plus(
                "No Stripe customer yet. Choose a plan first."), status_code=303)
    return RedirectResponse(url, status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(db_dependency)):
    """Stripe is the authority on subscription state, not our own timers."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook(payload, signature)
    except Exception as exc:  # noqa: BLE001 - a bad signature is not our error
        raise HTTPException(400, f"Webhook rejected: {exc}") from exc

    result = billing.apply_event(
        event,
        org_by_id=lambda oid: db.get(Organisation, oid),
        org_by_customer=lambda cid: db.scalar(
            select(Organisation).where(Organisation.stripe_customer_id == cid)),
    )
    db.commit()
    return {"ok": True, "result": result}


# --------------------------------------------------------------- shared reports


@app.post("/jobs/{job_id}/share")
def publish_share(request: Request, job_id: str,
                  recipient: str = Form(""), passcode: str = Form(""),
                  expires_days: str = Form("90"),
                  user: User = Depends(require_editor),
                  db: Session = Depends(db_dependency)):
    """Freeze the current schedule and publish it to a link."""
    job = owned_job(db, job_id, user)

    rendered = report(job_id=job_id, request=request, user=user, db=db)  # the assessor's own view
    html = rendered.body.decode("utf-8") if hasattr(rendered, "body") else ""
    if not html:
        raise HTTPException(500, "The report could not be rendered for publishing.")

    existing = list(db.scalars(select(ReportShare).where(ReportShare.job_id == job.id)))
    try:
        days = int(expires_days)
    except ValueError:
        days = 90

    share = ReportShare(
        job_id=job.id,
        organisation_id=user.organisation_id,
        version=sharing.next_version(existing),
        html=sharing.strip_app_chrome(html),
        settlement_total=job.totals["settlement"],
        item_count=job.totals["count"],
        passcode_hash=sharing.hash_passcode(pwd_context, passcode),
        recipient_label=recipient.strip()[:200],
        created_by=user.id,
        expires_at=sharing.expiry_from_days(days),
    )
    db.add(share)
    db.commit()
    return RedirectResponse(f"/jobs/{job_id}/shares?published={share.id}", status_code=303)


@app.get("/jobs/{job_id}/shares", response_class=HTMLResponse)
def job_shares(request: Request, job_id: str, user: User = Depends(require_user),
               db: Session = Depends(db_dependency)):
    job = owned_job(db, job_id, user)
    shares = list(db.scalars(
        select(ReportShare).where(ReportShare.job_id == job.id)
        .order_by(ReportShare.version.desc())))
    return templates.TemplateResponse(request, "shares.html", {
        "user": user, "job": job, "shares": shares,
        "base_url": _base_url(request),
        "published": request.query_params.get("published", ""),
        "summarise_agent": sharing.summarise_agent,
        "active_section": "reports",
    })


@app.post("/shares/{share_id}/revoke")
def revoke_share(share_id: str, user: User = Depends(require_editor),
                 db: Session = Depends(db_dependency)):
    share = db.get(ReportShare, share_id)
    if not share or share.organisation_id != user.organisation_id:
        raise HTTPException(404, "Shared report not found")
    share.revoked_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return RedirectResponse(f"/jobs/{share.job_id}/shares", status_code=303)


def _log_view(db: Session, share: ReportShare, request: Request, outcome: str) -> None:
    db.add(ReportShareView(share_id=share.id, outcome=outcome,
                           **sharing.describe_viewer(request)))
    db.commit()


@app.get("/r/{slug}", response_class=HTMLResponse)
def public_report(request: Request, slug: str, db: Session = Depends(db_dependency)):
    """The recipient's view. No account, no session, no application chrome."""
    share = db.scalar(select(ReportShare).where(ReportShare.slug == slug))
    if not share:
        raise HTTPException(404, "This report link is not valid.")
    if not share.is_live:
        _log_view(db, share, request, "blocked")
        return templates.TemplateResponse(request, "share_gate.html", {
            "share": share, "state": "closed", "error": None}, status_code=410)
    if share.requires_passcode:
        return templates.TemplateResponse(request, "share_gate.html", {
            "share": share, "state": "locked", "error": None})
    _log_view(db, share, request, "opened")
    return HTMLResponse(share.html)


@app.post("/r/{slug}", response_class=HTMLResponse)
def public_report_unlock(request: Request, slug: str, passcode: str = Form(""),
                         db: Session = Depends(db_dependency)):
    share = db.scalar(select(ReportShare).where(ReportShare.slug == slug))
    if not share or not share.is_live:
        raise HTTPException(404, "This report link is not valid.")
    if not sharing.check_passcode(pwd_context, share, passcode):
        _log_view(db, share, request, "passcode_failed")
        return templates.TemplateResponse(request, "share_gate.html", {
            "share": share, "state": "locked",
            "error": "That passcode is not correct."}, status_code=401)
    _log_view(db, share, request, "opened")
    return HTMLResponse(share.html)


# --------------------------------------------------------------------- evidence


@app.post("/items/{item_id}/evidence")
def upload_evidence(
    item_id: str,
    return_to: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Attach photographs or documents to a line.

    Anything the report PDF did not carry: a receipt, a quote, a spec sheet, a
    photograph taken on the day. PDFs are rendered page by page so they appear
    as evidence in the schedule rather than as an attachment nobody opens.
    """
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)

    stored, errors = _store_uploads(db, job, item, files, user)
    db.commit()

    target = _back(job.id, return_to, item.id)
    if errors and stored:
        target = _with_notice(
            target, f"Stored {stored} page(s). " + " ".join(errors)
        )
    elif errors:
        target = _with_notice(target, " ".join(errors))
    elif stored:
        target = _with_notice(
            target, f"Added {stored} evidence page{'' if stored == 1 else 's'}."
        )
    else:
        target = _with_notice(target, "No file was selected.")
    return RedirectResponse(target, status_code=303)


@app.post("/photos/{photo_id}/delete")
def delete_evidence(
    photo_id: str,
    return_to: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Remove an uploaded evidence page.

    Only uploads. Photographs paired out of the assessor's report are part of
    the ingested record and are not deletable from here.
    """
    photo = db.get(Photo, photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found")
    job = owned_job(db, photo.job_id, user)
    if photo.kind != "upload":
        raise HTTPException(400, "Report photographs cannot be deleted here.")

    item = db.get(Item, photo.item_id) if photo.item_id else None
    label = photo.caption or photo.filename or "evidence page"
    db.delete(photo)
    if item is not None:
        record_event(db, item, "evidence", f"Removed uploaded evidence: {label}"[:2000], user=user)
    db.commit()

    return RedirectResponse(
        _with_notice(_back(job.id, return_to, item.id if item else ""), "Evidence removed."),
        status_code=303,
    )


# ---------------------------------------------------------------------- reports


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
def report(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    photos = _photo_index(db, job.id)
    rows = report_view.build_items(job, photos)
    return templates.TemplateResponse(
        request,
        "report.html",
        {"user": user,
            "job": job,
            "photos": photos,
            "rows": rows,
            "mix": report_view.build_summary(rows),
            "totals": job.totals,
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%d %B %Y"),
        },
    )


@app.get("/jobs/{job_id}/export.csv")
def export_csv(
    job_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "#", "Item", "Qty", "Make", "Model", "Serial", "Cause", "Location",
            "Photos", "Identified as", "Category", "Est. age (yrs)",
            "Effective life (yrs)", "Depreciation %", "Replacement (AUD ea)",
            "Indemnity (AUD ea)", "Line total replacement", "Line total indemnity",
            "Confidence", "Notes", "Sources", "Excluded",
        ]
    )
    for index, item in enumerate(job.items, start=1):
        qty = item.quantity or 1
        writer.writerow(
            [
                index, item.description, qty, item.make, item.model, item.serial,
                item.cause_of_damage, item.location,
                f"{item.photo_series}: {item.photo_refs}",
                item.identified_as, item.category,
                item.estimated_age_years or "", item.effective_life_years or "",
                round((item.depreciation_rate or 0) * 100, 1),
                item.replacement_value if item.replacement_value is not None else "",
                item.indemnity_value if item.indemnity_value is not None else "",
                round((item.replacement_value or 0) * qty, 2),
                round((item.indemnity_value or 0) * qty, 2),
                item.confidence, item.valuation_notes,
                " | ".join(item.source_list), "Y" if item.excluded else "",
            ]
        )
    totals = job.totals
    pad = [""] * 15
    writer.writerow([])
    writer.writerow(["", "TOTALS", *pad, totals["replacement"], totals["indemnity"]])
    writer.writerow(["", f"Settlement basis: {job.basis_label}", *pad, "", totals["gross"]])
    writer.writerow(["", "Less policy excess", *pad, "", -(job.policy_excess or 0)])
    writer.writerow(["", "NET SETTLEMENT", *pad, "", totals["settlement"]])

    filename = f"{job.claim_reference or job.reference or 'claim'}-schedule.csv"
    return StreamingResponse(
        io.BytesIO(buffer.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ------------------------------------------------------- workspace sections
# Every section below is built from data the platform already holds; none of
# them invent content.


def _org_jobs(db: Session, user: User) -> list[Job]:
    return db.scalars(
        select(Job)
        .where(Job.organisation_id == user.organisation_id)
        .order_by(Job.created_at.desc())
    ).all()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    jobs = _org_jobs(db, user)
    portfolio = {
        "claims": len(jobs),
        "open": sum(1 for j in jobs if j.status != JobStatus.valued),
        "item_count": 0, "replacement": 0.0, "indemnity": 0.0,
        "settlement": 0.0, "flagged": 0, "spend": 0.0,
    }
    for job in jobs:
        t = job.totals
        portfolio["item_count"] += t["count"]
        portfolio["replacement"] += t["replacement"]
        portfolio["indemnity"] += t["indemnity"]
        portfolio["settlement"] += t["settlement"]
        portfolio["flagged"] += t["flagged"]
        portfolio["spend"] += t["research_cost"]

    recent = db.scalars(
        select(ItemEvent)
        .join(Job, ItemEvent.job_id == Job.id)
        .where(Job.organisation_id == user.organisation_id)
        .order_by(ItemEvent.created_at.desc())
        .limit(12)
    ).all()

    # Events carry only a job_id. The activity feed groups by claim, so give the
    # template a reference it can label the group with.
    job_refs = {j.id: (j.claim_reference or j.reference or "Untitled claim") for j in jobs}

    return templates.TemplateResponse(
        request, "dashboard.html",
        {"user": user, "jobs": jobs[:8], "portfolio": portfolio,
         "recent": recent, "job_refs": job_refs, "active_section": "dashboard"},
    )


@app.get("/assessments", response_class=HTMLResponse)
def assessments(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    jobs = [j for j in _org_jobs(db, user) if j.status != JobStatus.valued]
    return templates.TemplateResponse(
        request, "jobs.html",
        {"user": user, "jobs": jobs, "active_section": "assessments",
         "heading": "Assessments in progress",
         "subheading": "Claims that still have items awaiting research or review."},
    )


@app.get("/reports", response_class=HTMLResponse)
def reports(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    jobs = _org_jobs(db, user)
    shared = db.scalar(
        select(func.count(ReportShare.id))
        .where(ReportShare.organisation_id == user.organisation_id)
    ) or 0
    return templates.TemplateResponse(
        request, "reports.html",
        {"user": user, "jobs": jobs, "shared": shared, "active_section": "reports"},
    )


@app.get("/clients", response_class=HTMLResponse)
def clients(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    """Insureds and insurers derived from the claims on file."""
    grouped: dict[str, dict] = {}
    for job in _org_jobs(db, user):
        key = (job.insured_name or "Unnamed insured").strip()
        entry = grouped.setdefault(
            key,
            {"name": key, "insurers": set(), "claims": 0, "replacement": 0.0,
             "settlement": 0.0, "latest": job.created_at, "jobs": []},
        )
        t = job.totals
        entry["claims"] += 1
        entry["replacement"] += t["replacement"]
        entry["settlement"] += t["settlement"]
        if job.insurer:
            entry["insurers"].add(job.insurer)
        entry["jobs"].append(job)
    return templates.TemplateResponse(
        request, "clients.html",
        {"user": user, "clients": sorted(grouped.values(), key=lambda c: -c["replacement"]),
         "active_section": "clients"},
    )


@app.get("/audit", response_class=HTMLResponse)
def audit_log(
    request: Request,
    page: int = 1,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    per_page = 50
    base = (
        select(ItemEvent)
        .join(Job, ItemEvent.job_id == Job.id)
        .where(Job.organisation_id == user.organisation_id)
    )
    total = len(db.scalars(base).all())
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    events = db.scalars(
        base.order_by(ItemEvent.created_at.desc())
        .offset((page - 1) * per_page).limit(per_page)
    ).all()

    items = {i.id: i for i in db.scalars(
        select(Item).where(Item.id.in_([e.item_id for e in events]))
    ).all()} if events else {}
    jobs = {j.id: j for j in _org_jobs(db, user)}

    return templates.TemplateResponse(
        request, "audit.html",
        {"user": user, "events": events, "items": items, "jobs": jobs,
         "page": page, "pages": pages, "total": total, "active_section": "audit"},
    )


@app.get("/settings", response_class=HTMLResponse)
def settings(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    jobs = _org_jobs(db, user)
    return templates.TemplateResponse(
        request, "settings.html",
        {"user": user, "active_section": "settings",
         "model": valuation.MODEL,
         "max_searches": valuation.MAX_SEARCHES,
         "workers": VALUATION_WORKERS,
         "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
         "spend": round(sum(j.totals["research_cost"] for j in jobs), 4),
         "claims": len(jobs),
         "members": db.scalars(
             select(User).where(User.organisation_id == user.organisation_id)
         ).all()},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}
