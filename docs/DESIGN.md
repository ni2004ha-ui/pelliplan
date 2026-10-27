# Design decisions and trade-offs

## The one rule: AI reads and explains, code decides

Dates, dependencies, permissions and money are computed or enforced by deterministic code. The model is used for
two things only: turning messy text into candidate items, and explaining results in plain words. This makes the
system testable (the engine has hand-calculated test cases) and safe (a model mistake can waste a person's
attention, but it cannot change a deadline or leak a budget).

## The dependency engine

- **Model.** A task "depends on" another: it cannot start until that one finishes. A forward pass gives earliest
  start and finish. A backward pass from the deadlines gives latest start and finish. Slack is the difference.
  Negative slack means a deadline will be missed. This is the classic critical-path method.
- **Three different results from a delay.** Tasks that *move* (the slipped task and everything downstream).
  Tasks that are *pressured* (upstream tasks that do not move but have lost their spare time). Tasks that
  *miss a deadline*. My first version lumped these together and reported an upstream task as "affected" even
  though nothing about it had changed. Separating them made the output honest.
- **Recovery planning.** A greedy search: while some task misses its own deadline, buy back days on the cheapest
  task in that chain that can be sped up. It reports what it could not fix. It is greedy, not optimal: with
  several expedite options it can pick a worse combination than an exhaustive search would. Fine for a handful of
  options; a cost-minimising solver would be the next step.
- **Cycles.** Adding a dependency is refused if it would create a loop, and the error names the loop.
- **Simplifications.** Calendar days, not working days. Finish-to-start links only. One duration per task, no
  partial progress. Each is a clean extension point in `engine.py`.

## The Wedding Brain

- **Extraction ladder.** Model output goes through Pydantic validation, then code checks: evidence must appear in
  the document, amounts must appear in the document, dates must be sensible, names must match a member,
  duplicates are flagged, dependency titles are resolved, loops are dropped. Warnings travel with the item and
  must be acknowledged. Nothing becomes a task until a person confirms it.
- **Why check the evidence by string match.** A model can claim anything. Checking that its quote is really in
  the document is cheap and catches fabricated items. Limit: for images there is nothing to string-match, so
  those items are flagged for a human to check by eye.
- **"What am I forgetting?"** Code finds gaps by comparing the plan to a checklist (missing items) and by listing
  unfinished tasks ranked by urgency. The model only explains. Every item it mentions must be one the code found,
  and any number it writes must already be in the facts it was given, or its answer is replaced by a rule-based one.
- **Permissions apply before the AI sees anything.** The context given to the model is built from the tasks the
  asking person is allowed to see, so the AI cannot leak a private task to a bridesmaid.
- **Prompt injection.** Text inside a document is treated as data. Even if a model obeyed a planted instruction
  ("mark everything done"), it has no way to change the plan: it only proposes items, and a person confirms them.
  `samples/injection_test.txt` and a test cover this.
- **Offline mode.** Without a key the app falls back to rules and says so. It never presents rule output as AI.

## Permissions

All rules live in `auth.py`. Hidden things are removed on the server. A task you may not see returns 404, not 403,
so its existence is not revealed. A task that depends on a hidden task shows only a count of hidden blockers.
Money, private notes and source quotes (which can contain prices) are stripped for everyone but the bride.
Documents can only be downloaded by the bride. Tokens are random and stored only as hashes.
Known limit: schedule dates are computed on the full graph, so a visible task's slack can reflect a hidden
predecessor. That reveals timing, not content.

## Storage and API

SQLite from the standard library, with plain parameterised SQL. One connection per request. Foreign keys,
CHECK constraints and cascades are in the schema, so bad data is refused by the database as well as by the API.
The activity log is append-only and written by every endpoint that changes anything.
I chose it over Postgres so the project runs with one command; the SQL is plain enough to move.

## Bugs the tests found (and what they changed)

1. **Threading.** The upload endpoint ran on a different thread from its database connection, and SQLite refuses
   that by default. It would have crashed uploads on a real server. Fix: connections allow cross-thread use
   (safe, because each request uses its own connection one step at a time), and the endpoint is synchronous.
2. **Wrong checklist.** "Pellikuthuru" was given the Muhurtham checklist because the match word "pelli" appeared
   inside it as a substring. Fix: whole-word matching, with a regression test.
3. **Misleading "affected" list.** See the engine section above.
4. **Test hygiene.** Tests must never read a real `.env` or key. A leak like that once made a "no key" test run a
   real billed request in another project, so here the tests strip the key and disable `.env` loading.

## What I would do next

1. Reminders and notifications with a background queue, driven by slack ("this task becomes critical in 3 days").
2. Real authentication, per-wedding invitations, and Postgres.
3. Vendor and budget views, and quote comparison.
4. Partial progress and working-day calendars in the engine; a solver for recovery planning.
5. Evaluate the AI paths: a small labelled set of documents and questions, and report extraction precision and
   recall, as I did for the claim verifier in my CiteCheck project.
