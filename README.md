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

Then open http://127.0.0.1:8000/ and sign in.

To try it with sample jobs on an empty database:

```bash
python manage.py seed_demo
```

## Screens

**Live board** `/board/`
- By job: every active item with its current step, status, progress, and Late / At risk flags
- By work centre: a lane per work centre showing what is running, held, ready and coming up
- Search and filter by customer, work centre and status. Updates itself every 5 seconds.
- "At risk" means unfinished and due within `BOARD_AT_RISK_DAYS` (default 2, in settings)

**Jobs** `/jobs/`
- Create and edit jobs, add a new customer inline, archive and restore
- Job page: items with route progress, job files, issues, and the full event timeline
- Item page: spec form built from the product type's fields (plus ad-hoc extra fields),
  drawings and files, and the route builder
- Adding an item with a product type applies that type's route template automatically

**Routes** (on the item page)
- A route is a network, not a list. Each step says which steps it **comes after**; steps that are not
  waiting on each other run in parallel, and a step that comes after several is where branches join.
- Each link is **strict** (blocks) or **advisory** (warns), and is either "must finish before this
  starts" or "this can start, but cannot complete until that finishes".
- A step can also **need** gates: conditions that are not work, such as Hardware, Material or
  Drawing approval. The step cannot start until the gate is marked available on the item.
  Gate types are managed in Setup.
- Route settings: the default enforcement for new links, a shortcut to re-link everything as a
  simple sequence, and "save as template" to reuse a route you have built.
- "How it flows" previews the route in stages; steps side by side can run at the same time.
- Rules: no loops; a step that has started can be relaxed but not made stricter; a step with
  history is skipped, never removed. Removing an untouched step joins its neighbours.
- Templates are either a simple sequence (the default) or carry their own links and gates.

**Workstations** `/floor/`
- Choose a work centre, then work the queue: start, pause, resume, complete, hold with a reason, release
- Scan or type a job number to filter. Scanning `OP-<id>` opens that operation directly;
  that is the code to print as a barcode or QR on job sheets.
- Operation page: spec, drawings, route, history, notes, photos, raise an issue or NCR with a photo

**Setup** `/admin/` (staff only): users and roles, sites, work centres, product types and their
spec fields, route templates, hold reasons.

**API** `/api/v1/` for connecting other systems: create and update jobs, record floor events,
read the live board and attach files. See [docs/API.md](docs/API.md); interactive reference at
`/api/v1/docs` once signed in. Keys are created in Setup → API keys.

### Roles

| Role | Can do |
|---|---|
| Operator | Workstations, view board and jobs, upload files and photos, raise issues |
| Office, Supervisor, Admin | Everything above, plus create and edit jobs, items, specs and routes, clear gates, new drawing revisions, archive files, resolve issues, send work back for rework |

Set a user's role in Setup. Superusers can do everything.

### Spec fields

A product type's `spec_fields` is a JSON list. Supported types are `text`, `number`, `textarea`
and `choice`:

```json
[
  {"key": "width_mm", "label": "Width (mm)", "type": "number", "required": true},
  {"key": "finish", "label": "Finish", "type": "choice", "choices": ["Raw", "Galvanised", "Powder coat"]}
]
```

Pages are Django templates with HTMX (vendored in `static/js`, so it works without internet).
Live regions are inline template partials, so the full page and the HTMX refresh share one template.

## Structure

| App | Purpose |
|---|---|
| `accounts` | Custom user with a workshop role |
| `resources` | Sites and work centres |
| `jobs` | Customers, product types, jobs, bespoke items and spec history |
| `routing` | Route templates, per-item operations, hold reasons |
| `floor` | Immutable event log, issues, and the services that change operation status |
| `attachments` | Files (drawings, photos, NCRs) linked to any record, with drawing revisions |
| `board` | Live board views and the queries behind them (no models) |
| `api` | REST API (Django Ninja) and API keys; calls the same services as the screens |

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
