# PRD: Workshop Production Tracker (working title)

- **Status:** Draft v0.2 (adds attachments)
- **Date:** 12 November 2026
- **Owner:** TBC
- **Scope of this document:** the core of the app, live monitoring of bespoke production on the workshop floor

---

## 1. Summary

A web app that tracks bespoke, made-to-customer-requirement jobs as they move through a manufacturing workshop. Operators record progress at workstations with a scan or a tap. Supervisors and the office see, live, where every item is, what it is waiting on, and whether it will be on time.

The first release covers the tracking core only. Quoting, invoicing, stock and costing are later modules that build on the same data.

## 2. Problem

Bespoke work has no fixed product catalogue and no fixed route through the workshop. Each job is different, so:

- Nobody can see at a glance where a job is or what is blocking it.
- Progress is chased by walking the floor, phone calls or paper job sheets.
- Late jobs are noticed when they are already late.
- There is no reliable record of how long operations really take, so estimates do not improve.

## 3. Goals and non-goals

### Goals (v1)

1. Define a job with one or more bespoke items, each with its own specification.
2. Give every item its own route (ordered operations), created from a template and editable per job.
3. Capture shop-floor progress with minimal operator effort.
4. Show a live view of the workshop: item location, queues, blockers, lateness.
5. Keep a complete, timestamped history of everything that happened to a job.
6. Be structured so new modules can be added without reworking the core.

### Non-goals (v1)

- Quotes, sales orders, invoicing
- Stock control and purchasing
- Detailed job costing
- Automatic scheduling or capacity planning
- Customer portal
- Native mobile apps
- Machine or sensor integration

## 4. Users

| Role | Needs | Main device |
|---|---|---|
| Operator | See what to work on, record start / pause / complete, raise an issue | Workstation tablet or phone |
| Supervisor | Live view of the floor, reprioritise, resolve holds, edit routes | Tablet, PC, wall screen |
| Office / planner | Create jobs, set due dates, answer customer "where is my order" questions | PC |
| Admin | Manage users, work centres, templates, spec fields | PC |

## 5. Core concepts

| Concept | Description |
|---|---|
| Job | A customer order: customer, reference, due date, priority. |
| Job item | One thing being made within a job. Carries its own bespoke spec. |
| Spec | Flexible fields per product type (dimensions, material, finish, notes) plus attached drawings. Versioned. |
| Route | The ordered list of operations for one job item. |
| Route template | A reusable starting route for a product family. Copied onto an item, then edited. |
| Operation | One stage of work at a work centre (e.g. cut, weld, finish, assemble, QC, dispatch). Has status, estimate, dependencies. |
| Work centre | A place or machine group where operations happen. Has a queue. |
| Event | An immutable record of something that happened: start, pause, resume, complete, hold, release, issue raised, rework. Records who, where, when, which operation. |
| Hold / blocker | A reason an operation cannot proceed: waiting on material, drawing, customer approval, machine down, other. |
| Attachment | A file linked to a job, item, operation or issue: drawing, photo, NCR, certificate, customer document, other. Drawings carry a revision. |

**Principle:** status is never edited directly. It is always derived from events. This gives live state and full history from the same data.

## 6. Functional requirements

Priority: **M** = must have for v1, **S** = should have, **C** = could have.

### 6.1 Jobs and items

| ID | Requirement | Pri |
|---|---|---|
| J1 | Create, edit and archive a job with customer, reference, due date, priority | M |
| J2 | Add one or more items to a job | M |
| J3 | Record a bespoke spec per item using fields defined for its product type | M |
| J4 | Attach drawings and documents to an item (see 6.7) | M |
| J5 | Keep a version history of spec changes, with who and when | S |
| J6 | Flag in-progress operations when the spec changes mid-job | S |
| J7 | Items can have child items (sub-assemblies) | C |

### 6.2 Routes

| ID | Requirement | Pri |
|---|---|---|
| R1 | Admin can create route templates per product family | M |
| R2 | Apply a template to an item, creating its operations | M |
| R3 | Add, remove, reorder and skip operations on an individual item's route | M |
| R4 | Each operation has a work centre and an estimated duration | M |
| R5 | Operations can depend on other operations (cannot start until predecessors complete) | S |
| R6 | Parallel operations on the same item | C |

### 6.3 Shop-floor capture

| ID | Requirement | Pri |
|---|---|---|
| F1 | Operator signs in at a workstation (PIN or badge scan) | M |
| F2 | Workstation shows the queue for its work centre, ordered by priority and due date | M |
| F3 | Operator can start, pause, resume and complete an operation in one tap each | M |
| F4 | Operator can find an operation by scanning a barcode / QR code on the job sheet | M |
| F5 | Operator can put an operation on hold with a reason | M |
| F6 | Operator can raise an issue with a note and photo | S |
| F7 | Operator can view the item spec and drawings from the workstation | M |
| F8 | Operation can be sent back for rework, creating a visible rework loop | S |
| F9 | Events are queued locally and sent when connection returns | C |

### 6.4 Live monitoring

| ID | Requirement | Pri |
|---|---|---|
| L1 | Live board showing every active item, its current operation and status | M |
| L2 | View by work centre: what is running, what is queued | M |
| L3 | Items on hold are highlighted with reason and time on hold | M |
| L4 | Items at risk or late against due date are highlighted | M |
| L5 | Board updates without manual refresh, within 5 seconds of an event | M |
| L6 | Filter by customer, job, work centre, status, due date | S |
| L7 | Job detail page with full timeline of events | M |
| L8 | Wall-screen mode (read-only, large text, auto-rotating) | C |

### 6.5 Administration

| ID | Requirement | Pri |
|---|---|---|
| A1 | Manage users and roles (operator, supervisor, office, admin) | M |
| A2 | Manage work centres | M |
| A3 | Manage product types and their spec fields | M |
| A4 | Manage hold reasons | S |
| A5 | Supervisor can correct mistakes by adding a correcting event (never by deleting history) | M |

### 6.6 Reporting (basic)

| ID | Requirement | Pri |
|---|---|---|
| P1 | Estimated vs actual time per operation and per job | S |
| P2 | Time lost to holds, by reason | S |
| P3 | Export job and event data to CSV | S |

### 6.7 Attachments

Files such as drawings, photos, NCR documents and certificates can be attached throughout the app using one shared mechanism.

| ID | Requirement | Pri |
|---|---|---|
| T1 | Attach files to a job, job item, operation or issue | M |
| T2 | Each attachment has a category: drawing, photo, NCR, certificate, customer document, other | M |
| T3 | Record original filename, type, size, uploader, upload time and optional notes | M |
| T4 | Drawings have a revision; uploading a new revision supersedes the previous one | M |
| T5 | Workstation shows only the current drawing revision by default; older revisions remain in history | M |
| T6 | A new drawing revision writes an event and flags operations in progress on that item | S |
| T7 | Operator can take a photo with the device camera and attach it in one step | M |
| T8 | Photos taken at a workstation attach automatically to the operation or issue in hand | M |
| T9 | Images are resized and compressed on upload; thumbnails are generated for lists | S |
| T10 | PDFs and images open in the browser on any device | M |
| T11 | Other file types (e.g. DWG, DXF, STEP) are stored and downloadable; a PDF copy is expected for floor viewing | S |
| T12 | An issue can be categorised as an NCR and hold supporting photos and documents | S |
| T13 | Files are never deleted, only superseded or archived, so the audit trail stays complete | M |
| T14 | Files are private; access requires login and uses short-lived links | M |
| T15 | Allowed file types and maximum file size are restricted and configurable | M |

**NCR note:** in v1 an NCR is an issue with category "NCR" plus attachments. In the future quality module it becomes its own record (cause, disposition, sign-off); existing attachments stay linked.

## 7. Non-functional requirements

- **Usability on the floor:** large touch targets, usable with gloves, no typing needed for normal flow, one tap per status change.
- **Performance:** workstation actions respond in under 1 second; live board reflects events within 5 seconds.
- **Reliability:** no event is ever lost; the event log is append-only.
- **Auditability:** every change records who, when and from which workstation.
- **Security:** role-based permissions; all access authenticated; HTTPS only.
- **Scale (v1 assumption):** one site, up to ~50 users, ~20 work centres, a few hundred active items.
- **Extensibility:** multi-site should be possible later without restructuring data.
- **Devices:** modern browsers on tablet, phone and PC. No native app.

## 8. Technical approach

| Area | Decision |
|---|---|
| Architecture | Modular monolith: one codebase, one database, separate modules with clear boundaries |
| Backend | Django |
| Database | PostgreSQL; JSON fields for bespoke specs |
| Modules (Django apps) | `jobs`, `routing`, `floor` (events and status), `resources` (work centres, people), `attachments`, `accounts` |
| Attachments | One generic `Attachment` model linked to any record via Django generic relations (ContentType + object ID); one upload component and one viewer for the whole app |
| File storage | `FileField` with `django-storages` on S3-compatible object storage (local disk in development); path pattern `site/job/item/filename`; private files served through signed URLs |
| Image handling | Pillow for resize, compression and thumbnails; camera capture via HTML file input (`capture="environment"`) |
| Source of truth | Append-only event table; current-state fields updated from events |
| Business logic | Service functions (e.g. `start_operation()`), shared by pages, API and admin |
| Front end (v1) | Django templates + HTMX, Alpine.js for small interactions; inline template partials and `hx-select` to keep templates to roughly one per screen |
| Live updates | Polling first, then WebSockets (Django Channels + Redis) |
| Back office | Django admin for setup data in early versions |
| API | REST API over the same service functions, for future front ends and integrations |
| Scanning | Device camera or USB barcode scanner (keyboard input) |

## 9. Release plan

| Phase | Contents | Exit test |
|---|---|---|
| 1. Foundations | Models, admin, jobs, items, specs, route templates, editable routes | A real job can be entered with its route |
| 2. Capture | Event log, status logic, workstation screen, scan to find, holds | An operator can run a real job end to end |
| 3. Live board | Board, work-centre view, job timeline, late/at-risk flags, auto-update | Supervisor stops walking the floor to find jobs |
| 4. Pilot | One real job, then one work centre, then whole workshop | Two weeks of daily use without paper backup |
| 5. Hardening | Issues with photos, rework, corrections, basic reports, CSV export | v1 sign-off |

## 10. Success measures

- 95%+ of operations have start and complete events recorded on the day they happen.
- Any job's location and status can be answered in under 30 seconds without leaving the desk.
- Late jobs are flagged before their due date, not after.
- Estimated vs actual time is available for every completed job.

## 11. Future modules (out of scope, design must not block them)

- Quotes, orders and invoicing
- Stock, batches and material traceability
- Quality: inspection forms, sign-offs, non-conformance records
- Job costing: labour, material, overhead
- Scheduling and capacity planning
- Customer portal and notifications
- Accounting / ERP integrations
- Multi-site

Each of these reads from or reacts to the existing jobs, routes and event log.

## 12. Assumptions

- Single workshop site to begin with.
- Workshop has usable wifi at most workstations.
- Operators share workstation devices rather than each having their own.
- Built and maintained by one developer.
- Job sheets can be printed with a barcode or QR code.

## 13. Open questions

1. What is manufactured, and what product families exist? (Drives spec fields and route templates.)
2. Does a job move as one unit, or split into parts made in parallel that meet at assembly? (Decides whether J7 and R6 become must-haves.)
3. How strict is the route: must operations happen in order, or can operators choose?
4. Is labour time needed per person, or is operation start/complete enough?
5. How many work centres and operators are there today?
6. How are jobs entered today, and is there existing data to import?
7. Is offline working needed in any part of the workshop?
8. Does anything need to integrate with existing systems in v1 (accounts, ERP)?
9. What file formats do drawings arrive in (PDF, DWG, DXF, STEP), and is a PDF copy always available for the floor?
10. Roughly how many files and how much storage per job? (Drives storage cost and size limits.)
