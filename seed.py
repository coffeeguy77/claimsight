"""Seed an organisation, a user and the Rizon E010021610 flood claim.

Usage:
    python seed.py --email you@example.com --password ******** \
        --xlsx "Container Inventory.xlsx" \
        --pdf-initial "photos1.pdf" --pdf-second "photos2.pdf"

Safe to re-run: an existing user is reused, and a job with the same claim
reference is replaced rather than duplicated.
"""
from __future__ import annotations

import argparse
import sys

from passlib.context import CryptContext
from sqlalchemy import select

import ingest
from models import Item, Job, JobStatus, Organisation, Photo, User, get_session, init_db

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--org", default="Revive Disaster Recovery Pty Ltd")
    parser.add_argument("--name", default="")
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--pdf-initial")
    parser.add_argument("--pdf-second")
    parser.add_argument("--claim", default="E010021610")
    parser.add_argument("--insured", default="")
    parser.add_argument("--insurer", default="")
    parser.add_argument("--site", default="Old Well Station Rd, Gungahlin ACT")
    parser.add_argument("--peril", default="Water")
    parser.add_argument("--date-of-loss", default="26 June 2026")
    parser.add_argument("--excess", type=float, default=0.0)
    args = parser.parse_args()

    init_db()
    db = get_session()
    try:
        email = args.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            org = db.scalar(select(Organisation).where(Organisation.name == args.org))
            if org is None:
                org = Organisation(name=args.org)
                db.add(org)
                db.flush()
            user = User(
                organisation_id=org.id,
                email=email,
                name=args.name,
                password_hash=pwd_context.hash(args.password),
                role="owner",
            )
            db.add(user)
            db.commit()
            print(f"Created user {email} in {org.name}")
        else:
            user.password_hash = pwd_context.hash(args.password)
            db.commit()
            print(f"Reused user {email}; password reset")

        existing = db.scalar(
            select(Job).where(
                Job.organisation_id == user.organisation_id,
                Job.claim_reference == args.claim,
            )
        )
        if existing:
            db.delete(existing)
            db.commit()
            print(f"Removed existing job {args.claim}")

        job = Job(
            organisation_id=user.organisation_id,
            created_by_id=user.id,
            claim_reference=args.claim,
            insured_name=args.insured,
            insurer=args.insurer,
            site_address=args.site,
            peril=args.peril,
            date_of_loss=args.date_of_loss,
            policy_excess=args.excess,
            status=JobStatus.ingesting,
        )
        db.add(job)
        db.commit()

        sources = []
        if args.pdf_initial:
            sources.append((args.pdf_initial, "initial"))
        if args.pdf_second:
            sources.append((args.pdf_second, "second"))

        data = ingest.build_job(args.xlsx, sources)

        for photo in data.photos:
            db.add(
                Photo(
                    job_id=job.id, series=photo.series, number=photo.number,
                    page=photo.page, caption=photo.caption,
                    content_type=photo.content_type, width=photo.width,
                    height=photo.height, data=photo.image,
                )
            )
        for order, entry in enumerate(data.items):
            quantity = 1
            import re

            match = re.match(r"^\s*(\d+)\s*(?:x|X)?\s+", entry.description)
            if match and 1 <= int(match.group(1)) <= 500:
                quantity = int(match.group(1))
            db.add(
                Item(
                    job_id=job.id, sort_order=order, description=entry.description,
                    quantity=quantity, make=entry.make, model=entry.model,
                    serial=entry.serial, location=entry.location,
                    cause_of_damage=entry.cause_of_damage, photo_series=entry.series,
                    photo_refs=",".join(str(n) for n in entry.photo_numbers),
                    assessor_note=entry.assessor_note,
                )
            )

        job.status = JobStatus.ready
        job.status_detail = (
            f"{len(data.items)} items and {len(data.photos)} photographs ingested. "
            "Run the AI valuation to price the schedule."
        )
        db.commit()
        print(f"Seeded job {args.claim}: {len(data.items)} items, {len(data.photos)} photos")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
