# Engineering Decisions: Operations Under Pressure

This document outlines the core architectural and systems decisions made in Newtonite, detailing the context, decision rationale, considered alternatives, engineering trade-offs, failure modes, and automated test evidence for each.

---

## 1. Atomic Concurrency Control (Self-Claim Race Elimination)

### Context
In high-throughput operational environments, multiple operators simultaneously monitor queue dashboards for unassigned `OPEN` work items—particularly urgent `P1` and `P2` incidents. When an incident appears, multiple users frequently attempt to claim it at the exact same fraction of a second ("thundering herd"). If concurrency control relies on an application-level check (`if item.assignee_id is None: item.assignee_id = user.id`), a classic read-modify-write race condition occurs where multiple workers believe they own the work, or the item is overwritten erroneously.

### Decision
Execute work-item claim operations through an atomic conditional SQL statement executed directly within PostgreSQL:
```sql
UPDATE work_items
SET assignee_id = :user_id,
    status = 'IN_PROGRESS',
    version = version + 1,
    updated_at = NOW()
WHERE id = :item_id
  AND assignee_id IS NULL
  AND status = 'OPEN'
RETURNING id;
```
If the statement returns a row, the caller is guaranteed to have won the claim. If 0 rows are updated, PostgreSQL's row-level locking guarantees another transaction updated the row first. The server then refreshes the item and returns an HTTP `409 Conflict` containing the winner's current identity in `details.current`.

### Alternatives Considered
1. **Distributed Locks (Redis / Redlock):** Introduces an external dependency, network latency, lock timeout edge cases, and split-brain risks during network partitions.
2. **Pessimistic Application-Level Locks (`SELECT FOR UPDATE`):** Holding row locks across user interaction degrades database connection pools and risks deadlocks.
3. **Application In-Memory Mutexes:** Only works on a single-node process; fails completely when running multiple API workers behind a load balancer.

### Trade-offs
- **Pros:** Zero additional infrastructure; 100% ACID safety guaranteed by the storage engine; scales linearly across multi-replica API deployments.
- **Cons:** Losing clients receive an immediate rejection (409) rather than being placed in a wait queue; UI must handle the 409 gracefully and present the current assignee.

### Failure Mode
When two or more requests attempt to claim the same item concurrently:
- Exactly **one** request succeeds with HTTP 200 and acquires ownership.
- All losing requests immediately receive HTTP `409 Conflict` with code `"already_claimed"` and payload `{ "details": { "current": { "assignee": { "name": "<Winner>" } } } }`.
- If the claiming user already owns the item, the operation is treated as an idempotent success.

### Test Evidence
- [`backend/tests/test_concurrency.py::test_simultaneous_claims_exactly_one_wins`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_concurrency.py) (10 threads simultaneously racing on one item; exactly 1 HTTP 200, 9 HTTP 409).
- [`backend/tests/test_concurrency.py::test_claiming_twice_by_the_same_user_is_harmless`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_concurrency.py).
- [`backend/tests/test_scenarios.py::test_scenario_thundering_herd_claim_race`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_scenarios.py).

---

## 2. Version-Based Optimistic Concurrency Control (Guarded Writes)

### Context
Work items have multi-minute or multi-hour lifecycles. Users inspect items, draft remediation steps, and modify priorities or assignments while other operators may simultaneously update descriptions or transition status. If writes use unconditional `UPDATE` statements, the latest write silently obliterates prior modifications ("lost update anomaly").

### Decision
Implement optimistic concurrency control backed by an integer `version` column starting at `1`:
- Any write mutating an item (`PATCH /api/items/{id}`, `POST /api/items/{id}/assign`, `POST /api/items/{id}/transitions`) must supply an `If-Match: <version>` HTTP header (missing header returns HTTP `428 Precondition Required`).
- The update runs as a guarded SQL query:
```sql
UPDATE work_items
SET title = :title,
    description = :description,
    version = version + 1,
    updated_at = NOW()
WHERE id = :id AND version = :expected_version
RETURNING id;
```
- If the row count is zero, the transaction aborts and raises HTTP `409 Conflict` (`"version_conflict"`), embedding the freshest state in `details.current`.
- Comments deliberately do **not** bump the item's version, ensuring team communication never invalidates an in-flight draft.

### Alternatives Considered
1. **Last-Write-Wins (LWW):** Unconditional updates. Rejected because operational safety forbids silent data loss.
2. **Pessimistic Locking (`SELECT FOR UPDATE` across HTTP requests):** Holding a database transaction open while a user edits a form exhausts connections and creates vulnerability to abandoned locks.
3. **Timestamp-Based Checking (`updated_at`):** Microsecond resolution differences across OS/database clocks can cause missed conflicts or false rejections. An integer version is deterministic.
4. **Automatic 3-Way Field Merge:** High complexity, hides semantic conflicts (e.g. changing description based on an outdated priority).

### Trade-offs
- **Pros:** Non-blocking throughput; database holds zero locks during user think-time; clean client-facing conflict detection.
- **Cons:** Stale writes are rejected; clients must re-read and reconcile. The frontend implements an explicit draft preservation and rebase workflow (`EditForm.tsx`).

### Failure Mode
- Request without `If-Match`: Rebuffed with HTTP `428 Precondition Required`.
- Stale version: Rebuffed with HTTP `409 Conflict`, code `"version_conflict"`, returning current item state and allowed actions.

### Test Evidence
- [`backend/tests/test_concurrency.py::test_stale_edit_is_rejected_with_the_current_item`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_concurrency.py).
- [`backend/tests/test_concurrency.py::test_simultaneous_edits_on_the_same_version_only_one_applies`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_concurrency.py).
- [`backend/tests/test_concurrency.py::test_writes_without_if_match_are_refused`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_concurrency.py).
- [`backend/tests/test_concurrency.py::test_comments_do_not_make_other_peoples_edits_stale`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_concurrency.py).

---

## 3. Resource-Level Authorization & Four-Eyes Approval Policy

### Context
Users belong to multiple teams with distinct roles in each (`VIEWER < MEMBER < LEAD`). Global admin overrides exist. Furthermore, critical operational categories (financial adjustments, regulatory compliance) require separation of duties to prevent fraud and operational error.

### Decision
1. **Resource-Scoped Authorization:** All access checks go through `authorize(user, action, item)` in [`backend/app/permissions.py`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/app/permissions.py). Permissions are evaluated against the owning `item.team_id` by re-reading live team memberships from PostgreSQL on every request.
2. **Information Leakage Prevention:** Non-members attempting any action on an item receive HTTP `404 Not Found` (never 403), preventing attackers from enumerating item IDs or verifying that an item exists.
3. **Four-Eyes Approval Policy (`Action.APPROVE`):**
   - Work items with `type in ('payment', 'compliance')` or created with `requires_approval=True` cannot be directly resolved from `IN_PROGRESS` (HTTP 409).
   - They must be transitioned via `submit` to `AWAITING_APPROVAL`, recording `submitted_by_id = user.id`.
   - Approval/rejection requires a user who:
     1. Holds `Role.LEAD` in the item's owning team (or `is_admin=True`).
     2. Is **not** the current assignee (`item.assignee_id != user.id`).
     3. Is **not** the item's creator (`item.created_by_id != user.id`).
     4. Is **not** the item's submitter (`item.submitted_by_id != user.id`).
4. **Untrusted Client Inputs:** Client-provided `creator_id`, `role`, `status`, or `requires_approval: false` flags are discarded.

### Alternatives Considered
1. **Role Claims Embedded in JWT:** Storing roles in the token avoids a DB lookup but cannot be revoked immediately if a user is removed from a team. Live database queries ensure zero-lag permission revocation.
2. **Route-Level Decorators (`@require_role("LEAD")`):** Lacks resource context because role depends on *which team* owns the item being operated on.

### Trade-offs
- **Pros:** Cryptographically verified identity; audited separation of duties; immediate membership revocation; no resource enumeration leaks.
- **Cons:** Additional database lookup per item mutation (mitigated by indexed `(user_id, team_id)` primary key on `memberships`).

### Failure Mode
- User not member of item's team: HTTP `404 Not Found`.
- Team member lacks permission (e.g. VIEWER trying to claim): HTTP `403 Forbidden`.
- Lead who created, worked on, or submitted the item attempting self-approval: HTTP `403 Forbidden`.

### Test Evidence
- [`backend/tests/test_authorization.py::test_non_members_get_404_not_403`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_authorization.py).
- [`backend/tests/test_authorization.py::test_lists_only_show_items_from_my_teams`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_authorization.py).
- [`backend/tests/test_workflow.py::test_leads_cannot_approve_their_own_work`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_workflow.py).
- [`backend/tests/test_workflow.py::test_a_lead_who_submits_someone_elses_item_cannot_approve_it`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_workflow.py).
- [`backend/tests/test_items_api.py::test_four_eyes_approval_policy_payment_and_compliance`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_items_api.py).
- [`backend/tests/test_scenarios.py::test_scenario_dual_control_payment_dispute_revision_cycle`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_scenarios.py).

---

## 4. Database-Backed Idempotency Keys on Mutating Endpoints

### Context
Mobile or flaky browser network connections frequently drop before receiving HTTP responses. Operators click "Submit" or "Approve" multiple times when requests appear hung. Without idempotency, retries produce duplicate items, double-posted comments, or conflicting transitions.

### Decision
All state-modifying HTTP endpoints accept an optional `Idempotency-Key` header:
- The server stores keys in PostgreSQL table `idempotency_keys` with primary key `(user_id, key)` alongside a SHA-256 `request_hash`.
- `run_mutation()` in [`backend/app/idempotency.py`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/app/idempotency.py) begins a transaction and executes:
```sql
INSERT INTO idempotency_keys (user_id, key, request_hash, status_code, response_body)
VALUES (:user_id, :key, :hash, 0, NULL)
ON CONFLICT (user_id, key) DO NOTHING;
```
- If a duplicate concurrent request arrives while the first is executing, PostgreSQL blocks the second transaction on the primary key index until the first commits.
- Once committed, subsequent requests matching `(user_id, key)` replay the original response with header `Idempotent-Replayed: true`.
- If the same key is reused with a different path, body, or version, the server rejects it with HTTP `422 Unprocessable Entity`.
- Failed requests roll back the key insert so subsequent retries are free to execute. Keys expire after 24 hours.

### Alternatives Considered
1. **Redis Cache:** Separate store risks inconsistency if Redis writes succeed while database transactions roll back.
2. **Stateless Deduplication:** Client-side UUIDs discarded without tracking replay payloads.
3. **No Idempotency:** Forces users to refresh and manually determine if writes landed.

### Trade-offs
- **Pros:** Transactions and idempotency records commit atomically in the same database engine; eliminates double-submit risks; transparent to callers.
- **Cons:** Additional write per mutation; table requires background cleanup (handled by worker hourly sweep).

### Failure Mode
- Re-transmitting request during network timeout: Returns original response with `Idempotent-Replayed: true`.
- Reusing key with altered payload: HTTP `422 Unprocessable Entity` (`"idempotency_key_reused"`).
- Failed mutation: Key rolls back automatically, allowing clean retry.

### Test Evidence
- [`backend/tests/test_idempotency.py::test_retried_create_returns_the_same_item_and_creates_one`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_idempotency.py).
- [`backend/tests/test_idempotency.py::test_double_click_sent_concurrently_creates_one_item`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_idempotency.py).
- [`backend/tests/test_idempotency.py::test_reusing_a_key_for_a_different_request_is_rejected`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_idempotency.py).
- [`backend/tests/test_idempotency.py::test_keys_are_scoped_per_user`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_idempotency.py).
- [`backend/tests/test_idempotency.py::test_failed_requests_are_not_remembered`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_idempotency.py).

---

## 5. Transactional Activity Log & Outbox Pattern for Async Work

### Context
Operational integrity requires that history events ("who changed what, when") cannot silently vanish or drift from true database state. Synchronous dispatch of notifications (e.g. notifying leads upon submission) inside HTTP request handlers causes dual-write anomalies (e.g. notification dispatched, but DB transaction rolls back) and slow HTTP response times.

### Decision
1. **Transactional Outbox & Append-Only Log:** Every change to a work item writes to `activity_events` and inserts a task into table `outbox` within the **exact same database transaction** that modifies `work_items`.
2. **Worker Concurrency (`SKIP LOCKED`):** The background worker process ([`backend/app/worker.py`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/app/worker.py)) claims due messages in batches:
```sql
SELECT id, event_type, payload
FROM outbox
WHERE status = 'pending' AND available_at <= NOW()
ORDER BY id
LIMIT 50
FOR UPDATE SKIP LOCKED;
```
3. **Idempotent Notification Insertion:** Notifications are inserted with `ON CONFLICT (user_id, event_id) DO NOTHING`. Running a job twice cannot duplicate notifications.
4. **Exponential Backoff & Poison-Message Parking:** If a handler raises an unhandled exception, the worker increments `attempts` and reschedules `available_at = NOW() + (2 ^ attempts) seconds`. When `attempts >= 5`, status becomes `dead`, preventing poison pills from stalling the queue.

### Alternatives Considered
1. **Message Broker (RabbitMQ / Kafka / Celery):** Introduces two-phase commit risks where the DB transaction fails after publishing to the broker, or the broker is unavailable during request processing.
2. **Synchronous Notifications in Request:** Ties API response latency to downstream consumers; network failures roll back the user's primary mutation.

### Trade-offs
- **Pros:** 100% transactional consistency; no dual-write vulnerabilities; horizontally scalable workers using `SKIP LOCKED`.
- **Cons:** ~1s polling latency when idle; table requires retention partitioning at massive scale.

### Failure Mode
- Worker crashes mid-batch: PostgreSQL rolls back the transaction; rows are released for other workers immediately.
- Corrupted / poison payload: Fails 5 times with exponential backoff, then marks record as `dead` with error traceback captured in `last_error`. Normal jobs proceed unaffected.

### Test Evidence
- [`backend/tests/test_workflow.py::test_every_change_is_recorded_with_its_outbox_job`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_workflow.py).
- [`backend/tests/test_workflow.py::test_failed_transition_leaves_no_trace`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_workflow.py).
- [`backend/tests/test_outbox.py::test_running_a_job_twice_does_not_duplicate_notifications`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_outbox.py).
- [`backend/tests/test_outbox.py::test_failing_jobs_back_off_then_go_dead_without_blocking_others`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_outbox.py).
- [`backend/tests/test_scenarios.py::test_scenario_outbox_notification_lifecycle_and_poison_job_handling`](file:///Users/harshitakumari589gmail.com/Newtonite/backend/tests/test_scenarios.py).
