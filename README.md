# Claimsight

Contents claim assessment platform. Ingests an assessor's inventory spreadsheet and
photo-report PDFs, matches every photograph to its inventory line, researches current
Australian replacement costs with AI, applies depreciation, and produces an
insurer-ready settlement schedule and report.

## How the ingestion works

Photo-report PDFs produced by Encircle/Rizon-style tools label each photograph with a
number in brackets — `(37)`. The inventory spreadsheet references those same numbers in
its `Photo #` column, scoped by the `Photo Series` column (initial attendance, second
attendance, and so on). Claimsight extracts each embedded image, reads its bracketed
label, and joins on `series:number`. Captions in the right-hand column of the report are
associated by vertical position and carried onto the item as an assessor note.

Model and serial columns are frequently used by field assessors as free-text remarks
("Unsure if it was operating prior to Incident"). These are detected and rerouted to the
assessor-note field so the valuation engine is never handed a remark as a model number.

## Valuation basis

For each item the engine researches the current Australian retail price of a new
equivalent, inclusive of GST, and estimates the item's age. Indemnity value is derived by
straight-line depreciation over an assessed effective life, floored at 10% of replacement
cost as residual value.

```
indemnity = replacement × (1 − min(age / effective_life, 0.90))
```

Where age cannot be established it defaults to half the effective life and the line is
marked low confidence. Every valuation carries a confidence rating and the source URLs
it was derived from. Nothing is auto-accepted — the assessor reviews and can override any
figure, which recalculates depreciation against the overridden replacement cost.

## Running locally

```bash
pip install -r requirements.txt
export DATABASE_URL="sqlite:///./local.db"
export ANTHROPIC_API_KEY="sk-ant-..."
export FORCE_HTTPS=0
uvicorn main:app --reload
```

Then open http://localhost:8000 and create an account.

## Seeding a job from the command line

```bash
python seed.py \
  --email you@example.com --password "your-password" \
  --org "Your Assessing Firm Pty Ltd" \
  --xlsx "Container Inventory.xlsx" \
  --pdf-initial "photos-initial.pdf" \
  --pdf-second  "photos-second.pdf" \
  --claim E010021610 --excess 500
```

Re-running replaces the job with the same claim reference rather than duplicating it.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | yes | Postgres connection string. `postgres://` and `postgresql://` are normalised automatically. |
| `ANTHROPIC_API_KEY` | for valuation | Enables the AI valuation engine. The app runs without it; the valuation button is disabled. |
| `VALUATION_MODEL` | no | Defaults to `claude-sonnet-5`. |
| `VALUATION_WORKERS` | no | Concurrent valuations, default 4. Raise to speed up large jobs at the cost of rate-limit pressure. |
| `ENABLE_WEB_SEARCH` | no | Set to `0` to value without live web research (much weaker; not recommended). |
| `FORCE_HTTPS` | no | Set to `0` for local HTTP development so session cookies are accepted. |

## Deployment

Deploys as a Docker image; the included `Dockerfile` is all Railway needs. Set
`DATABASE_URL` to reference the Postgres service and add `ANTHROPIC_API_KEY`.

## Data model

Organisations own users and jobs; every query is scoped by `organisation_id`, so one
assessing firm can never see another's claims. Photographs are stored as binary columns
on the job rather than on object storage, which keeps a job self-contained and portable
at the cost of database size — roughly 6 MB per 200-photograph claim.

## Status

Working platform with multi-tenant accounts, ingestion, AI valuation, review/override,
and report and CSV export. Not yet built: billing, team invitations, role enforcement
beyond the owner role, audit logging, and PDF report generation server-side (the report
page prints to PDF from the browser).
