# Newtonite: Operations Under Pressure

An internal operational work-management platform for creating, triaging, assigning, and resolving mission-critical operational work items across cross-functional teams with rigorous concurrency guarantees, server-enforced workflows, and verifiable audit trails.

- **Architecture & Decisions:** [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md)
- **Detailed Design Specification:** [docs/DESIGN.md](docs/DESIGN.md)
- **Demo Script & Scenarios:** [docs/DEMO.md](docs/DEMO.md)
- **Similar Items Engine:** [docs/SIMILAR_ITEMS.md](docs/SIMILAR_ITEMS.md)

---

## 1. Architecture Overview

Newtonite is designed around a single source of truth in PostgreSQL, separating synchronous HTTP operations from background asynchronous notification delivery via a transactional outbox pattern.

```
                           ┌────────────────────────────────────────┐
                           │   Browser (React 18 + Vite + TS)       │
                           │   TanStack Query · Typed API Client    │
                           └───────────────────┬────────────────────┘
                                               │ HTTP + JWT
                                               │ If-Match: <version>
                                               │ Idempotency-Key: <uuid>
                                               ▼
                           ┌────────────────────────────────────────┐
                           │      Reverse Proxy (nginx / Vite)       │
                           └───────────────────┬────────────────────┘
                                               │ /api/*
                                               ▼
                           ┌────────────────────────────────────────┐
                           │         FastAPI Application            │
                           │  - Resource-Level RBAC (permissions.py)│
                           │  - Finite State Machine (workflow.py)  │
                           │  - Optimistic Guard (items.py)         │
                           │  - Mutation Transaction (idempotency.py│
                           └───────────────────┬────────────────────┘
                                               │ Single Atomic Transaction
                                               │ (Item + Events + Outbox + Key)
                                               ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   PostgreSQL 16                                        │
│  ┌───────────────────────┐  ┌──────────────────────┐  ┌─────────────────────────────┐  │
│  │ work_items            │  │ activity_events      │  │ idempotency_keys            │  │
│  │ (version, tsvector)   │  │ (append-only audit)  │  │ (request_hash, replay body) │  │
│  └───────────────────────┘  └──────────────────────┘  └─────────────────────────────┘  │
│  ┌───────────────────────┐  ┌──────────────────────┐  ┌─────────────────────────────┐  │
│  │ teams & memberships   │  │ outbox               │  │ notifications               │  │
│  │ (VIEWER, MEMBER, LEAD)│  │ (pending events)     │  │ (in-app per user)           │  │
│  └───────────────────────┘  └──────────┬───────────┘  └──────────────▲──────────────┘  │
└────────────────────────────────────────┼─────────────────────────────┼─────────────────┘
                                         │ SELECT FOR UPDATE           │ Insert
                                         │ SKIP LOCKED                 │ Notification
                                         ▼                             │
                           ┌───────────────────────────────────────────┴┐
                           │             Background Worker              │
                           │           (python -m app.worker)           │
                           │  - Exponential backoff & dead parking      │
                           └────────────────────────────────────────────┘
```

---

## 2. PostgreSQL Data Model

The database schema is managed via Alembic migrations in `backend/alembic/versions/`:

### Core Tables & Constraints

1. **`users`**:
   - `id` (PK), `email` (VARCHAR 255, UNIQUE), `name` (VARCHAR 120), `password_hash` (VARCHAR 255), `is_admin` (BOOLEAN, default `false`), `created_at` (TIMESTAMPTZ).
2. **`teams`**:
   - `id` (PK), `name` (VARCHAR 120, UNIQUE), `created_at` (TIMESTAMPTZ).
3. **`memberships`**:
   - `user_id` (FK `users.id` ON DELETE CASCADE), `team_id` (FK `teams.id` ON DELETE CASCADE).
   - `role` (VARCHAR 16, Check Constraint: `'VIEWER'`, `'MEMBER'`, `'LEAD'`).
   - Primary Key: `(user_id, team_id)`.
   - Index: `ix_memberships_team` on `(team_id, role)`.
4. **`work_items`**:
   - `id` (PK), `team_id` (FK `teams.id`), `type` (VARCHAR 32, Check Constraint: `'incident'`, `'customer_issue'`, `'payment'`, `'compliance'`, `'task'`).
   - `title` (VARCHAR 200), `description` (TEXT), `priority` (VARCHAR 2: `'P1'`, `'P2'`, `'P3'`, `'P4'`).
   - `status` (VARCHAR 32: `'OPEN'`, `'IN_PROGRESS'`, `'AWAITING_APPROVAL'`, `'RESOLVED'`, `'CLOSED'`, `'CANCELLED'`).
   - `assignee_id` (FK `users.id`, nullable), `created_by_id` (FK `users.id`).
   - `submitted_by_id` (FK `users.id`, nullable): Records the user who transitioned the item into approval review.
   - `requires_approval` (BOOLEAN, default `false`): Enforced `true` for `payment` and `compliance` types.
   - `due_at` (TIMESTAMPTZ, nullable), `resolved_at` (TIMESTAMPTZ, nullable).
   - `version` (INTEGER NOT NULL DEFAULT 1): Optimistic locking counter.
   - `search_tsv` (`TSVECTOR` generated column over `title` and `description`).
   - `created_at`, `updated_at` (TIMESTAMPTZ).
   - **Constraints & Indexes**:
     - Check Constraint `ck_active_work_has_assignee`: `status NOT IN ('IN_PROGRESS', 'AWAITING_APPROVAL') OR assignee_id IS NOT NULL`.
     - Index `ix_items_team_updated` on `(team_id, updated_at DESC, id DESC)`.
     - Index `ix_items_team_priority` on `(team_id, priority, created_at, id)`.
     - Partial Index `ix_items_assignee_active` on `(assignee_id, updated_at DESC)` WHERE `status IN ('OPEN', 'IN_PROGRESS', 'AWAITING_APPROVAL')`.
     - Partial Index `ix_items_unassigned_open` on `(team_id, priority, created_at)` WHERE `assignee_id IS NULL AND status = 'OPEN'`.
     - Partial Index `ix_items_awaiting_approval` on `(team_id, updated_at DESC)` WHERE `status = 'AWAITING_APPROVAL'`.
     - GIN Index `ix_items_search` on `search_tsv`.
5. **`activity_events`**:
   - `id` (BIGSERIAL PK), `item_id` (FK `work_items.id`), `actor_id` (FK `users.id`).
   - `kind` (`'created'`, `'status_changed'`, `'assigned'`, `'priority_changed'`, `'edited'`, `'commented'`).
   - `from_value` (JSONB), `to_value` (JSONB), `comment_id` (FK `comments.id`, nullable), `created_at` (TIMESTAMPTZ).
   - Index: `ix_activity_item` on `(item_id, id DESC)`.
6. **`comments`**:
   - `id` (PK), `item_id` (FK `work_items.id`), `author_id` (FK `users.id`), `body` (TEXT), `created_at` (TIMESTAMPTZ).
7. **`idempotency_keys`**:
   - `user_id` (FK `users.id` ON DELETE CASCADE), `key` (VARCHAR 64).
   - `request_hash` (VARCHAR 64), `status_code` (INTEGER), `response_body` (JSONB), `created_at` (TIMESTAMPTZ).
   - Primary Key: `(user_id, key)`. Index on `created_at` for TTL cleanup.
8. **`outbox`**:
   - `id` (BIGSERIAL PK), `event_type` (VARCHAR 64), `payload` (JSONB), `status` (`'pending'`, `'dead'`).
   - `attempts` (INTEGER DEFAULT 0), `available_at` (TIMESTAMPTZ), `last_error` (TEXT, nullable), `created_at` (TIMESTAMPTZ).
   - Partial Index `ix_outbox_due` on `(available_at, id)` WHERE `status = 'pending'`.
9. **`notifications`**:
   - `id` (PK), `user_id` (FK `users.id`), `item_id` (FK `work_items.id`), `event_id` (FK `activity_events.id`), `message` (TEXT), `read_at` (TIMESTAMPTZ, nullable), `created_at` (TIMESTAMPTZ).
   - Unique Constraint: `uq_notifications_user_event` on `(user_id, event_id)`.

---

## 3. Workflow & State Machine

```
            claim / assign (Lead)            submit (needs approval)
  OPEN ───────────────────────────► IN_PROGRESS ─────────────────────────► AWAITING_APPROVAL
   │                                  │   ▲                                   │        │
   │                                  │   └─────────── reject (Lead) ─────────┘        │ approve (Lead)
   │                                  │ resolve (requires_approval = false)            ▼
   │                                  └──────────────────────────────────────────► RESOLVED ──close (Lead)──► CLOSED
   │                                                                                  │
   │                                                 reopen (Lead) ◄──────────────────┘
   └── cancel (Lead) ─────────────► CANCELLED   (also allowed from IN_PROGRESS and AWAITING_APPROVAL)
```

### Transition Invariants
- **`START`**: `OPEN` → `IN_PROGRESS` (valid only if an assignee has already been set by a Lead).
- **`RELEASE`**: `IN_PROGRESS` → `OPEN` (clears assignee back to null; gives item back to the team queue).
- **`SUBMIT`**: `IN_PROGRESS` → `AWAITING_APPROVAL` (valid only when `requires_approval = True`; records `submitted_by_id = caller.id`).
- **`RESOLVE`**: `IN_PROGRESS` → `RESOLVED` (valid only when `requires_approval = False`; sets `resolved_at = NOW()`).
- **`APPROVE`**: `AWAITING_APPROVAL` → `RESOLVED` (requires `Action.APPROVE`; sets `resolved_at = NOW()`).
- **`REJECT`**: `AWAITING_APPROVAL` → `IN_PROGRESS` (requires `Action.APPROVE` and a non-empty `comment`).
- **`CLOSE`**: `RESOLVED` → `CLOSED` (requires `Action.MANAGE` = Lead; terminal state).
- **`REOPEN`**: `RESOLVED` → `IN_PROGRESS` (requires `Action.MANAGE` = Lead and a non-empty `comment`).
- **`CANCEL`**: `OPEN | IN_PROGRESS | AWAITING_APPROVAL` → `CANCELLED` (requires `Action.MANAGE` = Lead; terminal state).

### Four-Eyes Approval Policy
For payment and compliance items (and any item with `requires_approval = True`), approval or rejection enforces dual control:
1. Actor must hold `Role.LEAD` in the owning team (or `is_admin = True`).
2. Actor **cannot be the assignee** (`item.assignee_id != user.id`).
3. Actor **cannot be the creator** (`item.created_by_id != user.id`).
4. Actor **cannot be the submitter** (`item.submitted_by_id != user.id`).

---

## 4. API Reference

All routes are prefixed with `/api`. Authentication requires header `Authorization: Bearer <token>`.

### Authentication & Current User
- `POST /api/auth/login`: `{ "email": "...", "password": "..." }` → `{ "access_token": "...", "token_type": "bearer" }`.
- `GET /api/me`: Returns current user info, admin flag, and team memberships with roles.
- `GET /api/teams/{team_id}/members`: List members and roles for a team.

### Work Items (Reads)
- `GET /api/items`: Keyset-paginated items filtered to caller's teams in SQL.
  - Query parameters:
    - `view`: `assigned_to_me` | `unassigned_urgent` | `awaiting_my_approval`.
    - `team_id` or `team`: Filter by team ID.
    - `status`: Multi-value enum filter (`?status=OPEN&status=IN_PROGRESS`).
    - `priority`: Multi-value enum filter (`?priority=P1&priority=P2`).
    - `type`: Multi-value enum filter (`?type=payment&type=compliance`).
    - `assignee`: `'me'`, `'none'`, or `<user_id>`.
    - `q`: Full-text search or item number (`#123`).
    - `sort`: `"-updated_at"` (default) or `"priority"`.
    - `cursor`: Base64 keyset cursor.
    - `limit`: Bounded integer (`1 <= limit <= 100`, default 25).
  - Response: `{ "items": [...], "next_cursor": "<str | null>" }`.
- `GET /api/items/{id}`: Work item detail. Outsiders get HTTP 404. Returns `allowed_actions`.
- `GET /api/items/{id}/similar`: List similar items ranked by shared tsvector lexemes.
- `GET /api/items/{id}/activity`: Keyset-paginated audit events with linked comments.
- `GET /api/dashboard`: Aggregate counts for `assigned_to_me`, `unassigned_urgent`, and `awaiting_my_approval`.

### Work Items (Mutations)
Mutations accept optional header `Idempotency-Key: <uuid>`.
- `POST /api/items`: Create item (Status forced to `OPEN`, `creator_id` set from JWT).
- `PATCH /api/items/{id}`: Guarded update. Requires `If-Match: <version>`.
- `POST /api/items/{id}/claim`: Atomic conditional claim on unassigned `OPEN` item.
- `POST /api/items/{id}/assign`: Reassign item to another team Member/Lead. Requires `If-Match`.
- `POST /api/items/{id}/transitions`: Move state machine. Requires `If-Match`.
- `POST /api/items/{id}/comments`: Post comment. Bumps `updated_at` without bumping `version`.

### Notifications & System
- `GET /api/notifications`: List notifications for current user (`unread_only=true` supported).
- `POST /api/notifications/{id}/read`: Mark a specific notification as read (HTTP 204).
- `POST /api/notifications/read-all`: Mark all notifications as read for current user (HTTP 204).
- `GET /health` & `GET /api/health`: System health status and database connectivity check.
- `GET /healthz`: Liveness check.

---

## 5. Setup & Quickstart

### Option A: Docker Compose (Recommended)
Requires Docker Desktop / Docker Engine:
```bash
# Clean start (builds containers, runs migrations, seeds 20,000 items)
docker compose up --build
```
- Frontend UI: **http://localhost:5173**
- Backend API Docs: **http://localhost:8000/docs**
- Health Endpoint: **http://localhost:8000/health**

To restart with a fresh database:
```bash
docker compose down -v && docker compose up --build
```

### Option B: Local Development (Without Docker)

#### Prerequisites
- Python 3.9+ (Python 3.9 to 3.12 supported)
- Node.js 18+ and npm
- PostgreSQL 16 running on `localhost:5432` with databases `ops` and `ops_test`

#### 1. Database Setup
```bash
psql -U postgres -h localhost -c "CREATE DATABASE ops;"
psql -U postgres -h localhost -c "CREATE DATABASE ops_test;"
```

#### 2. Backend Setup
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Run migrations
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/ops"
export JWT_SECRET="local-dev-secret-key-12345"
alembic upgrade head

# Seed initial dataset (4 teams, 12 users, initial items)
python -m app.seed --items 5000

# Start API server (Terminal 1)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start Background Notification Worker (Terminal 2)
python -m app.worker
```

#### 3. Frontend Setup
```bash
cd frontend
npm ci

# Typecheck and build
npm run typecheck
npm run build

# Start Vite dev server (proxies /api to http://localhost:8000)
npm run dev
```

---

## 6. Environment Variables

| Variable | Service | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | API / Worker | `postgresql+psycopg://postgres:postgres@localhost:5432/ops` | PostgreSQL connection string |
| `JWT_SECRET` | API | `dev-only-secret-change-me-in-production-please` | Secret for signing HS256 auth tokens |
| `JWT_TTL_MINUTES` | API | `720` (12 hours) | Token expiration duration |
| `IDEMPOTENCY_TTL_HOURS` | API / Worker | `24` | Retention period for idempotency cache |
| `OUTBOX_MAX_ATTEMPTS` | Worker | `5` | Maximum retries before moving job to `dead` |
| `CORS_ORIGINS` | API | `http://localhost:5173` | Allowed CORS origins (comma-separated) |
| `API_URL` | Frontend (Vite) | `http://localhost:8000` | Target for Vite dev proxy |
| `VITE_API_URL` | Frontend (Client) | `""` (defaults to `/api`) | Optional explicit base URL for production builds |

---

## 7. Demo & Seed Users

All seeded demo accounts have password: `password`.

| Email | Name | Roles & Teams | Recommended Demo Scenario |
|---|---|---|---|
| `admin@example.com` | Admin | Global Administrator (Lead in all teams) | Global visibility, cross-team approvals |
| `alice@example.com` | Alice Lead | Lead: **Support** | Approving Support work items; assigning tickets |
| `priya@example.com` | Priya Lead | Lead: **Payments** | Four-Eyes rule: approving payments submitted by others |
| `erin@example.com` | Erin Lead | Lead: **Engineering** | Managing incident response & engineering tasks |
| `grace@example.com` | Grace Lead | Lead: **Compliance** | Auditing regulatory compliance items |
| `bob@example.com` | Bob Member | Member: **Support**, **Payments** | Claim race against Carol; submitting payment adjustments |
| `carol@example.com` | Carol Member | Member: **Support** | Claim race against Bob; parallel drafting |
| `dana@example.com` | Dana Member | Member: **Engineering**, **Support** | Cross-functional incident remediation |
| `dev@example.com` | Dev Member | Member: **Payments** | Handling payment disputes |
| `frank@example.com` | Frank Member | Member: **Engineering** | Deploying engineering hotfixes |
| `henry@example.com` | Henry Member | Member: **Compliance** | Submitting compliance audit items |
| `victor@example.com` | Victor Viewer | Viewer: **Support**, **Compliance** | Read-only access & comment permissions (cannot claim/edit) |

---

## 8. Automated Test Suite

### Running Backend Tests
The backend test suite executes against real PostgreSQL (`ops_test`). The test runner verifies that the database name ends in `_test` to prevent accidental wiping of development data.

#### Running inside Docker (Recommended):
```bash
docker compose exec -e DATABASE_URL="postgresql+psycopg://postgres:postgres@db:5432/ops_test" api pytest -v
```

#### Running locally (Host venv):
```bash
cd backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ops_test .venv/bin/pytest -v
```
**Results:** **74 passed in ~7s**.

### Test Suite Structure
| Test File | Focus Areas | Key Test Functions |
|---|---|---|
| `test_auth_and_model.py` | Password hashing, JWT signing, `/me`, RBAC hierarchy, database constraints | `test_login_success`, `test_role_membership_hierarchy`, `test_work_item_attributes_and_defaults` |
| `test_concurrency.py` | Atomic conditional claims, version conflict detection, optimistic locking | `test_simultaneous_claims_exactly_one_wins`, `test_stale_edit_is_rejected_with_the_current_item`, `test_assign_requires_if_match_and_rejects_stale_version` |
| `test_authorization.py` | Cross-team isolation, 404 information-leak prevention, role permissions | `test_non_members_get_404_not_403`, `test_lists_only_show_items_from_my_teams` |
| `test_workflow.py` | State machine transitions, four-eyes approval rule, comment enforcement | `test_full_approval_path`, `test_leads_cannot_approve_their_own_work`, `test_creator_cannot_approve_even_if_lead_and_item_submitted_by_another_member` |
| `test_idempotency.py` | Idempotency key replay, concurrent double-click deduplication, payload mismatch 422 | `test_retried_create_returns_the_same_item_and_creates_one`, `test_reusing_a_key_for_a_different_request_is_rejected` |
| `test_items_api.py` | REST API integration, bounded pagination, filters, non-trust of client values | `test_work_item_full_valid_lifecycle`, `test_four_eyes_approval_policy_payment_and_compliance` |
| `test_scenarios.py` | Real-world operational pressure non-CRUD scenarios | `test_scenario_incident_triage_escalation_lifecycle`, `test_scenario_thundering_herd_claim_race` |
| `test_outbox.py` | Outbox worker polling (`SKIP LOCKED`), deduplication, poison backoff | `test_failing_jobs_back_off_then_go_dead_without_blocking_others`, `test_mark_all_notifications_read_comprehensive_matrix` |
| `test_similar.py` | PostgreSQL tsvector-based lexeme ranking for similar tickets | `test_similar_items_share_words_and_rank_closest_first` |
| `test_health.py` | Liveness, readiness, and degraded database state detection | `test_get_health`, `test_get_health_degraded` |

### Running Frontend Tests
```bash
cd frontend
npm test
```
**Results:** **23 passed in ~1s** (covers `client.test.ts`, `item-page.test.tsx`, `app-flows.test.tsx`).

---

## 9. Known Limitations & Production Roadmap

1. **No Admin Screen for Dead Outbox Messages:** Poison messages parked as `dead` are inspected and retried via SQL (`UPDATE outbox SET status = 'pending', attempts = 0 WHERE status = 'dead'`).
2. **Token Blacklisting / Revocation List:** JWTs remain valid until expiration (12 hours). However, team memberships are re-queried from PostgreSQL on every request, so revoking a user's role takes effect immediately.
3. **No Field-Level Automated Merge:** Concurrent overlapping field edits prompt the operator to review changes and re-apply or discard their draft.
4. **Activity Events Partitioning:** The append-only audit log grows monotonically. At 10x scale (>10M rows), PostgreSQL table partitioning by month is recommended.
5. **No Websockets / SSE:** The client polls every 20 seconds and refetches on tab focus. Optimistic concurrency catches stale writes, keeping the solution simple and robust.
