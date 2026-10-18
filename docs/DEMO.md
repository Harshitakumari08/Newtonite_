# Demo script and talking points

A 15–20 minute walkthrough of the behaviours that need more than CRUD. Each section has what to show, the
one-line point to make, the file to open if asked "show me the code", and the test that proves it.

Start everything with `docker compose up --build`, wait for the API to be healthy, and open http://localhost:5173. All logins use the
password `password` (see the [README](../README.md#logins)).

## Setup for the terminal parts

The UI shows most of this, but a terminal makes races and retries visible. Paste this once:

```bash
API=http://localhost:8000/api
tok() { curl -s $API/auth/login -H 'content-type: application/json' \
  -d "{\"email\":\"$1@example.com\",\"password\":\"password\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
ALICE=$(tok alice); BOB=$(tok bob); CAROL=$(tok carol); VICTOR=$(tok victor); PRIYA=$(tok priya)
psql_ops() { docker compose exec -T db psql -U postgres ops -c "$1"; }
```

The API doesn't end its JSON with a newline, so consecutive responses print on one line. Adding
`-w '\n'` to a `curl` call separates them.

## 0. The overview (1 min)

Log in as **alice** (Lead, Support). Show the dashboard: "Assigned to me", "Unassigned urgent", "Awaiting my
approval", then the item list with filters and search over 20,000 seeded items.

- **Point:** everything is filtered and paginated on the server with keyset cursors and a partial index per
  dashboard view, so the browser never loads the whole data set.
- **Code:** `list_items()` and `_apply_view()` in `backend/app/items.py`; indexes on `WorkItem` in
  `backend/app/models.py`.
- **Test:** `backend/tests/test_listing.py` (no gaps or duplicates across pages even with identical timestamps).

## 1. Two people claim the same item (3 min)

Create an urgent item as Alice, then have Bob and Carol claim it at the same moment.

```bash
ID=$(curl -s $API/items -H "Authorization: Bearer $ALICE" -H 'content-type: application/json' \
  -d '{"team_id":1,"type":"incident","title":"Checkout returning 500s","priority":"P1"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

for T in $BOB $CAROL; do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/items/$ID/claim -H "Authorization: Bearer $T" &
done; wait                                  # one 200, one 409

curl -s -X POST $API/items/$ID/claim -H "Authorization: Bearer $CAROL"   # 409 already_claimed, says who won
```

In the UI: open the same unassigned item in two browsers (bob and carol), click Claim in both. One shows
"claiming…" then success; the other rolls back and shows who got it.

- **Point:** the race is resolved by the database, not by the app. The claim is one conditional
  `UPDATE ... WHERE assignee_id IS NULL AND status = 'OPEN'`. Exactly one row update can succeed; the loser
  gets 409 with the current item so the UI can say who won.
- **Code:** `claim_item()` in `backend/app/items.py`.
- **Test:** `test_simultaneous_claims_exactly_one_wins` fires 10 claims through a thread barrier and asserts
  one 200, nine 409s, the version bumped exactly once and one `assigned` event.

## 2. Stale edits (3 min)

Item `$ID` is now at version 2 (the claim bumped it). Alice still has version 1 open:

```bash
curl -s -X PATCH $API/items/$ID -H "Authorization: Bearer $ALICE" -H 'content-type: application/json' \
  -H 'If-Match: 1' -d '{"priority":"P2"}'     # 409 version_conflict, details.current is the latest item

curl -s -X PATCH $API/items/$ID -H "Authorization: Bearer $ALICE" -H 'content-type: application/json' \
  -d '{"priority":"P2"}'                       # 428 precondition_required: writes must say what they're based on
```

In the UI: open the item as alice in one tab and as bob in another, change the priority as bob, then edit as
alice. Alice sees "this item changed" with the latest values instead of overwriting Bob.

- **Point:** optimistic concurrency. Every write runs `UPDATE ... WHERE version = :expected`; zero rows
  updated means someone else got there first. No locks are held while people think, and nothing is silently
  overwritten. Comments don't bump the version, so chatting on an item never blocks an edit.
- **Code:** `_guarded_update()` and `_version_conflict()` in `backend/app/items.py`; `required_version()` in
  `backend/app/api/routes.py`.
- **Tests:** `test_stale_edit_is_rejected_with_the_current_item`,
  `test_simultaneous_edits_on_the_same_version_only_one_applies`, `test_transitions_check_the_version`.

## 3. Double clicks and retries (2 min)

```bash
for i in 1 2; do
  curl -s -i $API/items -H "Authorization: Bearer $ALICE" -H 'content-type: application/json' \
    -H 'Idempotency-Key: demo-create-1' -d '{"team_id":1,"type":"task","title":"Rotate API keys"}' \
    | grep -iE '^HTTP/|^idempotent-replayed|^\{' | cut -c1-40
done                       # same id twice; the second response has Idempotent-Replayed: true

curl -s $API/items -H "Authorization: Bearer $ALICE" -H 'content-type: application/json' \
  -H 'Idempotency-Key: demo-create-1' -d '{"team_id":1,"type":"task","title":"Something else"}'
                           # 422 idempotency_key_reused
```

- **Point:** the client generates one key per click and reuses it on retry. The key row is written in the
  same transaction as the effect. A concurrent duplicate blocks on the unique index until the first commits,
  then replays its response. Failed requests aren't remembered, so a retry after fixing the problem runs.
- **Code:** `run_mutation()` in `backend/app/idempotency.py` (the docstring walks through the steps).
- **Tests:** `backend/tests/test_idempotency.py`, especially `test_double_click_sent_concurrently_creates_one_item`
  and `test_failed_requests_are_not_remembered`.

## 4. Permissions on the server (3 min)

```bash
curl -s -o /dev/null -w "priya (Payments only) reads a Support item: %{http_code}\n" \
  $API/items/$ID -H "Authorization: Bearer $PRIYA"                          # 404
curl -s -o /dev/null -w "victor (Support viewer) claims: %{http_code}\n" \
  -X POST $API/items/$ID/claim -H "Authorization: Bearer $VICTOR"           # 403
```

Then the workflow rules, on a payment item (payments always need approval):

```bash
PAY=$(curl -s $API/items -H "Authorization: Bearer $BOB" -H 'content-type: application/json' \
  -d '{"team_id":2,"type":"payment","title":"Duplicate charge refund"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s -X POST $API/items/$PAY/claim -H "Authorization: Bearer $BOB" > /dev/null          # version 2
curl -s -X POST $API/items/$PAY/transitions -H "Authorization: Bearer $BOB" -H 'If-Match: 2' \
  -H 'content-type: application/json' -d '{"action":"resolve"}'     # 409: this item requires approval
curl -s -X POST $API/items/$PAY/transitions -H "Authorization: Bearer $BOB" -H 'If-Match: 2' \
  -H 'content-type: application/json' -d '{"action":"submit"}'      # AWAITING_APPROVAL, version 3
curl -s -X POST $API/items/$PAY/transitions -H "Authorization: Bearer $PRIYA" -H 'If-Match: 3' \
  -H 'content-type: application/json' -d '{"action":"reject"}'      # 422 comment_required
```

In the UI: log in as victor and show that action buttons are missing, then explain that hiding them is only
cosmetic: the API refuses the same calls.

- **Point:** roles are per team membership. Every item endpoint calls one `authorize()` function and every
  list query is filtered to the caller's teams in SQL. Outsiders get 404 so ids don't leak; insiders without
  the right role get 403. The UI's buttons come from `allowed_actions`, which uses the same function.
- **Code:** `backend/app/permissions.py` (the whole permission table is `can()`), `backend/app/workflow.py`
  (the whole state machine is the `TRANSITIONS` table).
- **Tests:** `backend/tests/test_authorization.py`, `backend/tests/test_workflow.py`
  (`test_items_needing_approval_cannot_skip_it`, `test_leads_cannot_approve_their_own_work`,
  `test_a_lead_who_submits_someone_elses_item_cannot_approve_it`).

## 5. The outbox worker and failure handling (4 min)

Show that notifications arrive, then break things.

```bash
curl -s $API/notifications -H "Authorization: Bearer $ALICE"     # "Bob Member moved #… from OPEN to IN_PROGRESS"
psql_ops "SELECT status, count(*) FROM outbox GROUP BY status"
```

**Worker down:** stop it, keep working, start it again.

```bash
docker compose stop worker
curl -s -X POST $API/items/$ID/comments -H "Authorization: Bearer $BOB" -H 'content-type: application/json' \
  -d '{"body":"Rolled back the deploy"}'
psql_ops "SELECT id, status, attempts FROM outbox ORDER BY id DESC LIMIT 3"     # pending: the work is queued, not lost
docker compose start worker                                                     # it drains the backlog
```

**Poison message:** a job that can never succeed backs off, then is parked without blocking anything else.

```bash
psql_ops "INSERT INTO outbox (event_type, payload) VALUES ('activity', '{\"event_id\": 999999, \"item_id\": 1}')"
docker compose logs -f worker      # 'failed (attempt 1)', 2, 3… then 'is dead after 5 attempts'
psql_ops "SELECT id, status, attempts, available_at, last_error FROM outbox WHERE status <> 'done'"
```

The retries back off exponentially (2, 4, 8, 16 s), so it takes about half a minute to go dead.

- **Point:** the item change, its history event and its outbox row commit in one transaction, so we never
  notify about something that rolled back, and never lose a notification for something that committed.
  Workers claim jobs with `FOR UPDATE SKIP LOCKED`, so several can run at once. Delivery is at-least-once,
  and a unique `(user_id, event_id)` constraint makes a repeated job harmless.
- **Code:** `record_event()` in `backend/app/items.py`; `process_batch()`, `backoff()` and `recipients()` in
  `backend/app/worker.py`.
- **Tests:** `backend/tests/test_outbox.py` (`test_running_a_job_twice_does_not_duplicate_notifications`
  simulates a crash after the side effect, `test_failing_jobs_back_off_then_go_dead_without_blocking_others`).

## 6. History (1 min)

Open item `$ID` in the UI and show the timeline: created, assigned, status changed, comment. Or:

```bash
curl -s "$API/items/$ID/activity" -H "Authorization: Bearer $ALICE"
```

- **Point:** every change writes an append-only event in the same transaction, so history can't drift from
  the data, and a rejected change leaves no trace. `reject` and `reopen` require a reason.
- **Test:** `test_every_change_is_recorded_with_its_outbox_job`, `test_failed_transition_leaves_no_trace`.

## Likely questions

**What happens when things fail?**
- Database down: requests fail with 500 and nothing is half-written, because each action is one transaction.
- API crashes mid-request: the transaction rolls back, including the idempotency key, so the client's retry
  with the same key runs cleanly.
- Worker crashes mid-batch: its row locks are released and the jobs are picked up again; re-running is
  harmless because notifications are unique per event.
- A job keeps failing: exponential backoff, then `dead` after 5 attempts with `last_error` recorded.

**Which parts matter most?** The single transaction per action (`run_mutation`), the conditional updates for
claim and version, and the one `authorize()` function. If any of those were wrong, data would be silently
lost or exposed; everything else is presentation.

**What assumptions did you make?** See the table in [DESIGN.md §1](DESIGN.md#1-assumptions-product-decisions-the-brief-leaves-open):
one owning team per item, payment and compliance always need approval, Leads approve but never their own work,
items are cancelled, never deleted.

**What would you change at 10x?** See the end of [ENGINEERING_DECISIONS.md](../ENGINEERING_DECISIONS.md):
a real queue behind the outbox, partitioned history, push updates, replicas and a connection pooler.

**What would you do next week?** Team and membership management for admins, a dead-letter screen with a
retry button, login rate limiting, and push updates instead of polling. The full list is under "Known limitations" in the
[README](../README.md#known-limitations).

**Honest weak spots to raise before they're found:** seeded items only have a `created` event; JWTs can't be
revoked early (roles are re-read every request, which limits the damage); there's no field-level merge on a
conflict. A good story to tell: the four-eyes rule first only excluded the assignee, and reviewing it found that
a Lead could submit someone else's item and approve it. Now the submitter is recorded and excluded too.
