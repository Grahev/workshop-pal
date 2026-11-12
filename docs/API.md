# Workshop Pal API guide

Base URL: `/api/v1`. Everything is JSON, except file uploads, which are `multipart/form-data`.

Interactive reference with every field: sign in to the app, then open `/api/v1/docs`.
The machine-readable schema is at `/api/v1/openapi.json`.

## 1. Get a key

In the app: **Setup → API keys → Add**. Give it a name, pick the user it acts as, save, and copy
the key from the message at the top. It is shown once; only a hash is stored.

Or from the command line:

```bash
python manage.py create_api_key "ERP sync" --user erp
```

The key acts as its user:

| User role | The key can |
|---|---|
| Operator | Read everything, record floor events, raise issues, upload files |
| Office, Supervisor, Admin | All of the above, plus create and change jobs, items, specs and routes, skip and rework steps, resolve issues, revise and archive files |

Create a dedicated user per external system, so the timeline shows which system did what.
To revoke a key, untick **Is active**.

Send the key with every request:

```
Authorization: Bearer wp_xxxxxxxxxxxxxxxx
```

## 2. Create a job

One call creates the job, its items, their specs and their routes. If any part is invalid,
nothing is saved.

```bash
curl -X POST https://your-host/api/v1/jobs \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{
    "reference": "J-2051",
    "title": "Entrance canopy",
    "customer": "Riverside Homes",
    "customer_reference": "PO-88213",
    "due_date": "2026-12-05",
    "priority": "high",
    "items": [
      {
        "name": "Canopy frame",
        "product_type": "Steel frame",
        "quantity": 1,
        "spec": {"width_mm": 3200, "height_mm": 2700, "finish": "RAL 9005"}
      }
    ]
  }'
```

- `reference` is optional; one is generated if you leave it out. It must be unique: a duplicate returns 409,
  which makes retries safe. Look a job up by number with `GET /jobs?reference=J-2051`.
- `customer` is a name and is created if new. Or send `customer_id`.
- `product_type` is a name, or send `product_type_id`. The spec is checked against that type's fields.
- `priority` is `low`, `normal`, `high` or `urgent`.
- The response contains every item and operation with its `id`, and each operation's scan `code` (`OP-<id>`).

**Where an item's route comes from**, in order:

1. `operations`: an explicit list (see below)
2. `route_template_id`
3. The product type's active template (turn off with `"apply_default_route": false`)

### Routes with parallel steps and gates

A route is a network. Each step says which steps it comes `after`, and which gates it `needs`.

```json
"operations": [
  {"name": "Program", "work_centre": "CUT", "after": []},
  {"name": "Cut", "work_centre": "CUT"},
  {"name": "Fold", "work_centre": "FLD"},
  {"name": "Glue", "work_centre": "GLU", "after": ["Fold"]},
  {"name": "Build", "work_centre": "ASM", "needs": ["Hardware"],
   "after": ["Fold", {"step": "Glue", "type": "finish_to_finish"}]},
  {"name": "Inspect", "work_centre": "QC"}
]
```

- No `after`: the step follows the one before it, so a plain list is a simple sequence.
- `"after": []`: the step can start straight away.
- `"after": ["Fold"]`: by step name. Two steps after the same one run in parallel; a step after
  several waits for all of them.
- An object sets the link type and enforcement: `type` is `finish_to_start` (default) or
  `finish_to_finish` (can start, cannot complete until the other finishes); `enforcement` is
  `strict` (blocks) or `advisory` (warns). The default enforcement is the item's
  `link_enforcement`, which is `strict` unless you send otherwise.
- `needs` lists gate types by name. The step cannot start until that gate is cleared.

Every operation in a response shows `after`, `needs`, `can_start`, and what it is waiting for:
`waiting_on` (strict earlier steps), `waiting_gates`, `finish_waiting`, and `earlier_steps` (advisory).

## 3. Change jobs, items and routes

| Call | Does |
|---|---|
| `GET /jobs` | List; filters `q`, `reference`, `customer_id`, `archived`, `due_before`, `due_after`, `limit`, `offset` |
| `GET /jobs/{id}` | Job with items and routes |
| `PATCH /jobs/{id}` | Change any fields; `"due_date": null` clears it |
| `POST /jobs/{id}/archive`, `/restore` | Hide from or return to the board |
| `POST /jobs/{id}/items` | Add an item (same shape as in job create) |
| `GET`, `PATCH /items/{id}` | Read; change name, quantity, product type |
| `PUT /items/{id}/spec` | Replace the spec; the old version is kept |
| `GET /items/{id}/spec-history` | Earlier spec versions |
| `POST /items/{id}/route/apply-template?template_id=` | Route from a template (only if the item has none) |
| `POST /items/{id}/operations` | Add a step; follows the last step unless `after` says otherwise |
| `POST /operations/{id}/links` | `{"after": <operation id>, "type": ..., "enforcement": ...}` make a step come after another |
| `PATCH`, `DELETE /operations/{id}/links/{after_id}` | Change or remove a link |
| `POST /operations/{id}/gates` | `{"gate_type": "Hardware"}` make a step need a gate |
| `DELETE /operations/{id}/gates/{gate_id}` | Stop a step needing a gate |
| `POST /items/{id}/route/sequence` | Re-link the whole route as a simple sequence |
| `POST /items/{id}/route/save-template` | `{"name": "..."}` save the route as a template |
| `PATCH /operations/{id}` | Change a step that has not started |
| `POST /operations/{id}/move` | `{"direction": "up"}` or `"down"`; display order only |
| `DELETE /operations/{id}` | Remove a step with no history; otherwise record a `skip` event |

## 4. Record what happens on the floor

Status only ever changes by recording an event:

```bash
curl -X POST https://your-host/api/v1/operations/16/events \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"type": "start"}'
```

| `type` | Allowed when | Notes |
|---|---|---|
| `start` | pending | Blocked while a strict earlier step is unfinished or a gate is not cleared |
| `pause` | in progress | |
| `resume` | paused | |
| `complete` | in progress or paused | Blocked while a strict `finish_to_finish` step is unfinished |
| `hold` | pending, in progress, paused | Needs `hold_reason_id` or `hold_reason` (name) |
| `release` | on hold | Work that was running comes back paused |
| `rework` | completed | Office roles only |
| `skip` | pending | Office roles only |
| `note` | any | Needs `note`; does not change status |

Optional on any event: `note`, `work_centre_id` (where it was recorded), and `occurred_at`
(ISO 8601, for events captured offline or by another system).

A move that is not allowed returns **409** with the reason, and nothing is recorded. The response
to a successful event contains the event and the operation's new state.

## 5. Read live state

| Call | Returns |
|---|---|
| `GET /board/items` | One row per active item: current step, state, progress, late / at-risk flags, plus headline counts. Filters `q`, `customer_id`, `work_centre_id`, `status` |
| `GET /board/work-centres` | Open work grouped by work centre |
| `GET /work-centres/{id}/queue` | One work centre's queue: active, on hold, ready, upcoming |
| `GET /operations` | Filters `status`, `work_centre_id`, `job_id`, `item_id` |
| `GET /operations/{id}/events` | One operation's history |
| `GET /events?after_id=` | Feed of everything, oldest first |

**Keeping another system in sync:** poll `GET /events?after_id=<last id you saw>` (up to 500 per call
with `limit`). Each event carries the job, item and operation it belongs to. Filter with `job_id` or `type`.

## 5a. Gates

A gate is a condition on an item that is not a work step, such as hardware being available.

```bash
# everything still waiting on hardware
curl "https://your-host/api/v1/gates?cleared=false&gate_type=Hardware" -H "Authorization: Bearer $KEY"

# the hardware has arrived
curl -X POST https://your-host/api/v1/gates/12/clear \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"note": "Delivered, bay 3"}'
```

- `GET /gates` filters: `cleared`, `gate_type`, `job_id`, `item_id`. Each gate lists the operations it is `needed_by`.
- `POST /gates/{id}/clear` and `/reopen`; `PATCH /gates/{id}` with `{"expected_date": "2026-12-01"}`.
- `GET /board/items?status=gate` lists items waiting on a gate.
- A stock or purchasing system can clear gates as goods arrive, which releases the waiting steps.

## 6. Issues and NCRs

```bash
curl -X POST https://your-host/api/v1/operations/16/issues \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"description": "Cut 5mm short", "category": "ncr"}'
```

`GET /issues?open=true` lists them; `POST /issues/{id}/resolve` with `{"resolution": "Recut"}` closes one.

## 7. Files

```bash
curl -X POST https://your-host/api/v1/attachments \
  -H "Authorization: Bearer $KEY" \
  -F target_type=item -F target_id=7 \
  -F category=drawing -F revision=A \
  -F file=@GA-drawing.pdf
```

- `target_type` is `job`, `item`, `operation` or `issue`.
- `category` is `drawing`, `photo`, `ncr`, `certificate`, `customer_doc` or `other`.
- `GET /attachments?target_type=item&target_id=7` lists current files; add `include_history=true` for old revisions.
- `POST /attachments/{id}/supersede` (multipart: `revision`, `file`) uploads a new drawing revision.
- `POST /attachments/{id}/archive` hides a file. Nothing is ever deleted.

## 8. Reference data

`GET /sites`, `/work-centres`, `/product-types`, `/route-templates`, `/hold-reasons`, `/gate-types`, `/customers`.
`POST /customers` with `{"name": "..."}` creates a customer, or returns the existing one with that name.

These are set up in the app under Setup; the API reads them so you can map your system's values.

## Errors

All errors are `{"detail": "..."}`.

| Status | Meaning |
|---|---|
| 401 | Missing, wrong or deactivated key |
| 403 | The key's user role cannot do this |
| 404 | No such record |
| 409 | Not allowed in the current state (duplicate reference, invalid status move, step has history, link would make a loop, tightening a started step) |
| 422 | Invalid data; the message says which field |

Lists that can grow (`/jobs`, `/operations`, `/issues`) return `{"items": [...], "count": n}` and accept
`limit` (default 100) and `offset`.
