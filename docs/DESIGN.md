# Design: Operations Under Pressure

This is the step 1 design for the Newtonite challenge. It defines what a work item is, its workflow,
who can do what, the data model and the API. The decisions behind it, with their trade-offs, are in
[ENGINEERING_DECISIONS.md](../ENGINEERING_DECISIONS.md).

**Stack:** FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16 · React (Vite) + TanStack Query · pytest ·
Docker Compose.

## 1. Assumptions (product decisions the brief leaves open)

| Open question | Assumption |
|---|---|
| What is a work item? | One request owned by exactly one team, with an optional single assignee. |
| Item types | `incident`, `customer_issue`, `payment`, `compliance`, `task`. |
| Priority | `P1` urgent, `P2` high, `P3` normal, `P4` low. |
| Which items need approval? | `payment` and `compliance` always do. Others may opt in at creation (`requires_approval`). |
| Who approves? | A **Lead** of the item's team who is neither the item's assignee nor the person who submitted it (four-eyes rule). |
| Who can see an item? | Members of the owning team (any role) and admins. Everyone else gets **404**, so item ids don't leak. |
| Multiple teams | A user can belong to many teams with a different role in each. |
| Sign-up / SSO | Not built. Users are seeded and log in with email + password and get a JWT. |
| Real-time | No websockets. The UI polls every 15–30 s and refetches on focus. Stale writes are caught by versioning. |
| Deleting items | Not allowed. Items are cancelled, so history is never lost. |
| Notifications | In-app only (no email), produced asynchronously. |

## 2. Workflow

```
            claim / assign                submit (needs approval)
  OPEN ───────────────────► IN_PROGRESS ─────────────────────────► AWAITING_APPROVAL
   │                          │   ▲                                   │        │
   │                          │   └─────────── reject (Lead) ─────────┘        │ approve (Lead)
   │                          │ resolve (no approval needed)                    ▼
   │                          └──────────────────────────────────────────► RESOLVED ──close──► CLOSED
   │                                                                          │
   │                                         reopen ◄─────────────────────────┘
   └── cancel (Lead) ──► CANCELLED   (also allowed from IN_PROGRESS)
```

- `claim` (self, `POST /claim`) moves OPEN → IN_PROGRESS in one step. A Lead's `assign` only sets the owner, and the
  owner then uses `start`. The owner can `release` an IN_PROGRESS item back to OPEN and unassigned.
- Transitions are enforced on the server in one table (`ALLOWED_TRANSITIONS[from][action] -> to`
  plus a role check). Anything else returns `409 invalid_transition`.
- `resolve` straight from `IN_PROGRESS` is only allowed when `requires_approval = false`.
- `reject` and `reopen` need a comment, so the reason is recorded.
- `CLOSED` and `CANCELLED` are terminal.

## 3. Roles and permissions

Roles are **per team membership**: `VIEWER < MEMBER < LEAD`. `is_admin` on the user is a global override.

| Action | Viewer | Member | Lead | Admin |
|---|:-:|:-:|:-:|:-:|
| View items / activity of the team | ✓ | ✓ | ✓ | ✓ |
| Comment | ✓ | ✓ | ✓ | ✓ |
| Create item in the team | | ✓ | ✓ | ✓ |
| Claim an unassigned item (self) | | ✓ | ✓ | ✓ |
| Edit title/description/due date | | assignee or creator | ✓ | ✓ |
| Change priority | | assignee | ✓ | ✓ |
| Assign / reassign to someone else | | | ✓ | ✓ |
| Start / submit / resolve | | assignee | ✓ | ✓ |
| Approve / reject | | | ✓ (independent) | ✓ (independent) |
| Cancel, close, reopen | | | ✓ | ✓ |
| Manage teams and memberships | | | | ✓ |

All checks go through one function, `authorize(user, action, item)`. It loads the user's role for
`item.team_id`, so permissions are always scoped to the resource, not to the screen.

### 3.1 Four-Eyes Approval Policy for Payment and Compliance Items

Payment and compliance operations carry high financial and regulatory risk and strictly enforce dual control (the **four-eyes principle**):

1. **Mandatory Classification:**
   - Any work item created with `type="payment"` or `type="compliance"` has `requires_approval = true` enforced server-side.
   - Client flags attempting to set `requires_approval: false` on these types are overridden and ignored.
   - Direct resolution from `IN_PROGRESS` via `resolve` is forbidden (`409 invalid_transition`). The item must transition via `submit` to `AWAITING_APPROVAL`.

2. **Server-Side Approval Invariants:**
   To approve or reject an item in `AWAITING_APPROVAL`, the actor must satisfy all of the following:
   - **Team Leadership:** Must hold the `LEAD` role in the item's owning team (or `is_admin = true`).
   - **Not the Creator:** `item.created_by_id != user.id`. A Lead who initiated the work cannot self-authorize it.
   - **Not the Assignee:** `item.assignee_id != user.id`. The worker currently assigned to the item cannot approve their own output.
   - **Not the Submitter:** `item.submitted_by_id != user.id`. A Lead who submitted another user's item for review cannot approve their own submission.

3. **Consistent Error Codes:**
   - Non-members / outsiders receive **404 Not Found** to prevent leaking the existence of sensitive payment/compliance items.
   - Team Members or disqualified Leads (e.g., creator or assignee) receive **403 Forbidden**.
   - Rejections without an accompanying explanation receive **422 Unprocessable Entity** (`comment_required`).

## 4. Data model (PostgreSQL)

```
users(id, email UNIQUE, name, password_hash, is_admin, created_at)
teams(id, name UNIQUE, created_at)
memberships(user_id, team_id, role, PRIMARY KEY(user_id, team_id))

work_items(
  id, team_id FK, type, title, description, priority, status,
  assignee_id FK NULL, created_by FK, requires_approval, due_at NULL,
  version INT NOT NULL DEFAULT 1,           -- optimistic concurrency
  search_tsv TSVECTOR GENERATED,            -- full-text over title + description
  created_at, updated_at, resolved_at NULL)

activity_events(                            -- append-only, never updated
  id BIGSERIAL, item_id FK, actor_id FK, kind,    -- created | status_changed | assigned |
  from_value JSONB, to_value JSONB,               -- priority_changed | edited | commented
  comment_id NULL, created_at)

comments(id, item_id FK, author_id FK, body, created_at)

idempotency_keys(
  user_id, key, request_hash, status_code, response_body JSONB, created_at,
  PRIMARY KEY(user_id, key))                -- expired after 24 h

outbox(                                     -- transactional outbox for async work
  id BIGSERIAL, event_type, payload JSONB, status, attempts, available_at,
  last_error, created_at)

notifications(id, user_id, item_id, event_id, message, read_at, created_at,
  UNIQUE(user_id, event_id))                -- makes the worker idempotent
```

**Indexes** (driven by the dashboard queries):
- `work_items(team_id, status, priority, updated_at DESC, id)` for team lists and filters
- `work_items(assignee_id, status) WHERE status NOT IN ('CLOSED','CANCELLED')` for "assigned to me"
- `work_items(team_id, priority) WHERE assignee_id IS NULL AND status = 'OPEN'` for "unassigned urgent"
- `work_items(team_id, status) WHERE status = 'AWAITING_APPROVAL'` for "awaiting my approval"
- `GIN(search_tsv)` for search
- `activity_events(item_id, id DESC)` for the timeline
- `outbox(status, available_at)` for the worker

## 5. API

All endpoints except login need `Authorization: Bearer <jwt>`. Errors use one shape:
`{"error": {"code": "...", "message": "...", "details": {...}}}`.

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | email + password → JWT |
| GET | `/me` | user + team memberships/roles |
| GET | `/teams/{id}/members` | for the assignee picker |
| GET | `/items` | `team_id, status[], priority[], type[], assignee=me\|none\|<id>, q, view=assigned_to_me\|unassigned_urgent\|awaiting_my_approval, sort, cursor, limit≤100`. Only the caller's teams. Cursor (keyset) pagination. |
| POST | `/items` | create. `Idempotency-Key` header supported |
| GET | `/items/{id}` | detail, including `version` |
| PATCH | `/items/{id}` | partial edit. **Requires `If-Match: <version>`**. Stale → `409 version_conflict` with the current item |
| POST | `/items/{id}/claim` | atomic. Already taken → `409 already_claimed` with the current assignee |
| POST | `/items/{id}/assign` | Lead only. `If-Match` |
| POST | `/items/{id}/transitions` | `{action, comment?}`. `If-Match` + `Idempotency-Key` |
| GET | `/items/{id}/activity` | timeline with cursor pagination |
| GET | `/items/{id}/similar` | items sharing words with this one, best match first. Add-on, see [SIMILAR_ITEMS.md](SIMILAR_ITEMS.md) |
| POST | `/items/{id}/comments` | `Idempotency-Key` supported |
| GET | `/dashboard` | counts for the three dashboard views |
| GET | `/notifications` | the caller's unread and recent notifications |
| POST | `/notifications/{id}/read` | mark one as read |

## 6. The critical (non-CRUD) behaviours we will demonstrate and test

1. **Two people claim the same item.** `UPDATE work_items SET assignee_id=:me, status='IN_PROGRESS', version=version+1
   WHERE id=:id AND assignee_id IS NULL AND status='OPEN' RETURNING *`. Exactly one wins and the other gets 409.
2. **Stale edits.** Every write checks `version` (`WHERE id=:id AND version=:expected`). Zero rows updated means 409 with the
   fresh item, and the UI shows "this item changed, here's the latest" instead of overwriting.
3. **Retried or double-clicked requests.** `Idempotency-Key` stores the first response. A replay with the same key returns
   the same response, and the same key with a different body returns 422.
4. **Workflow rules on the server.** Transitions are enforced by the transition table, and approval needs a Lead who isn't the assignee.
5. **Resource-level authorization.** Every query is filtered by the caller's teams, and every mutation calls `authorize()`.
6. **Reliable async.** The item change, its activity event and its outbox row are committed in **one transaction**. The worker
   claims rows with `FOR UPDATE SKIP LOCKED`, retries with backoff, and inserts notifications with
   `ON CONFLICT (user_id, event_id) DO NOTHING`, so running a job twice is harmless.

## 7. Frontend

- React + Vite + TanStack Query, with React Router.
- **Dashboard:** "Assigned to me", "Unassigned urgent (P1/P2)", "Awaiting my approval", with counts.
- **Item list:** filters (team, status, priority, type, assignee), debounced search, "Load more" by cursor.
  All filtering happens on the server.
- **Item page:** fields, the allowed action buttons (from the server's `allowed_actions` in the item response),
  the activity timeline and comments.
- **Claim is optimistic:** the UI shows "claiming…". On a 409 it rolls back and shows who got it.
- On `version_conflict` it shows the server's version and lets the user re-apply their edit.
- Every mutation sends a fresh `Idempotency-Key` per user action (generated once per click, reused on retry).

## 8. Seed data

4 teams (Support, Payments, Engineering, Compliance), ~12 users with mixed roles, at least one user in two teams,
and one admin. About 20,000 items with activity, to show that pagination and indexes hold up.

## 9. Deliberately not built

Email, attachments, SSO / sign-up, websockets, SLA timers, a mobile app and fancy styling.
