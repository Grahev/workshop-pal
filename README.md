# Workshop Pal

Live tracking of bespoke manufacturing jobs through the workshop. See [PRD.md](PRD.md) for the full requirements.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Then open http://127.0.0.1:8000/admin/.

Runs on SQLite with no configuration. For Postgres, set `DATABASE_URL` (see `.env.example`).

## Structure

| App | Purpose |
|---|---|
| `accounts` | Custom user with a workshop role |
| `resources` | Sites and work centres |
| `jobs` | Customers, product types, jobs, bespoke items and spec history |
| `routing` | Route templates, per-item operations, hold reasons |
| `floor` | Immutable event log, issues, and the services that change operation status |
| `attachments` | Files (drawings, photos, NCRs) linked to any record, with drawing revisions |

## Two rules

1. **Status only changes through events.** Call the functions in `floor/services.py`
   (`start_operation`, `complete_operation`, `hold_operation`, ...). Never set
   `Operation.status` directly. Events cannot be edited or deleted.
2. **Business logic lives in `services.py`**, not in views or the admin, so pages,
   the API and future modules share one code path.

## First steps in the admin

1. Create a Site and its Work centres.
2. Create a Product type and a Route template with steps.
3. Create a Customer and a Job with items.
4. On the Job items list, select items and run "Apply route template".

## Tests

```bash
python manage.py test
```
