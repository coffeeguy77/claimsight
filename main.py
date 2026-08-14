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
import traceback
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

import ingest
import valuation
from models import (
    Item,
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
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

SESSION_COOKIE = "claimsight_session"
SESSION_DAYS = 14
VALUATION_WORKERS = int(os.environ.get("VALUATION_WORKERS", "4"))

_executor = ThreadPoolExecutor(max_workers=VALUATION_WORKERS)
_job_locks: dict[str, threading.Lock] = {}


@app.on_event("startup")
def on_startup() -> None:
    init_db()


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


@app.exception_handler(HTTPException)
async def redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


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
        {})


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
    policy_excess: float = Form(0.0),
    apply_depreciation: str = Form(""),
    inventory: UploadFile = File(...),
    photos_initial: UploadFile | None = File(None),
    photos_second: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
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
        policy_excess=policy_excess or 0.0,
        apply_depreciation=apply_depreciation == "on",
        status=JobStatus.ingesting,
    )
    db.add(job)
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


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    photos = {p.key: p for p in job.photos}
    pending = sum(1 for i in job.items if i.valuation_status == ValuationStatus.pending)
    running = sum(1 for i in job.items if i.valuation_status == ValuationStatus.running)
    return templates.TemplateResponse(
        request,
        "job.html",
        {"user": user,
            "job": job,
            "photos": photos,
            "totals": job.totals,
            "pending": pending,
            "running": running,
            "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
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

    targets = [
        i.id
        for i in job.items
        if not i.excluded
        and not i.manual_override
        and i.valuation_status in (ValuationStatus.pending, ValuationStatus.failed)
    ]
    for item in job.items:
        if item.id in targets:
            item.valuation_status = ValuationStatus.running
    job.status = JobStatus.valuing
    job.status_detail = f"Valuing {len(targets)} items…"
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
    db = get_session()
    try:
        item = db.get(Item, item_id)
        if item is None:
            return
        captions = [
            p.caption
            for p in db.scalars(
                select(Photo).where(Photo.job_id == item.job_id, Photo.series == item.photo_series)
            ).all()
            if p.key in item.photo_key_list and p.caption
        ]
        result = valuation.value_item(item, captions)

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
        db.commit()
    finally:
        db.close()


@app.get("/jobs/{job_id}/progress")
def valuation_progress(
    job_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    counts = {status.value: 0 for status in ValuationStatus}
    for item in job.items:
        counts[item.valuation_status.value] += 1
    return {"status": job.status.value, "detail": job.status_detail, "counts": counts, "totals": job.totals}


@app.post("/items/{item_id}")
def update_item(
    item_id: str,
    replacement_value: str = Form(""),
    quantity: int = Form(1),
    excluded: str = Form(""),
    valuation_notes: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    job = owned_job(db, item.job_id, user)

    item.quantity = max(1, quantity)
    item.excluded = excluded == "on"
    if valuation_notes:
        item.valuation_notes = valuation_notes
    if replacement_value.strip():
        try:
            value = float(replacement_value.replace("$", "").replace(",", ""))
            item.replacement_value = value
            item.indemnity_value, item.depreciation_rate = valuation.compute_indemnity(
                value, item.estimated_age_years, item.effective_life_years
            )
            item.manual_override = True
            item.valuation_status = ValuationStatus.overridden
        except ValueError:
            pass
    db.commit()
    return RedirectResponse(f"/jobs/{job.id}#item-{item.id}", status_code=303)


# ---------------------------------------------------------------------- reports


@app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
def report(
    job_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(db_dependency),
):
    job = owned_job(db, job_id, user)
    photos = {p.key: p for p in job.photos}
    return templates.TemplateResponse(
        request,
        "report.html",
        {"user": user,
            "job": job,
            "photos": photos,
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


@app.get("/healthz")
def healthz():
    return {"ok": True}
