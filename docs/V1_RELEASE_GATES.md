# v1.0 release gates

These criteria are recorded before the final campaign, rather than inferred from
whichever tests happen to pass. A release remains blocked by unresolved failures.
The release targets the documented local, single-operator studio and supported
Python delivery workflow; it does not promise arbitrary generated software is
correct or that guarded execution isolates malicious programs from the host.

## Required evidence

1. Run backend lint, formatting, flake8, strict typing, and the entire backend
   suite on the final frozen source on Windows and Linux with locked dependencies.
   Explain every platform skip and retain failure logs. Tests with scripted model
   responses are unit/integration checks, not live model reliability measurements.
2. Run frontend typing, lint, formatting, unit tests, production build, dependency
   audit, and all browser scenarios against development and production routes.
3. Rebuild the wheel from that source and exercise the installed distribution
   outside the checkout: startup, health, blueprint catalog, and a real local
   demo with approval, failed check, committed repair, final verification, and
   persisted artifacts. Verify source-distribution contents as well.
4. Reproduce identified false-pass paths and repair regressions. Independent
   acceptance tests must execute despite project-level collection/skip settings;
   failures and skipped acceptance cases cannot become passing evidence. Keep
   protected tests and original verification scope intact throughout repairs.
5. Execute fresh, uncached live missions with fixed external acceptance checks.
   Begin with repeated CSV runs, then run all six controlled cases at least twice
   on the frozen candidate. No false completions are allowed. Preserve every run,
   including provider outages, timeouts, malformed outputs, and abandoned runs.
   Changes after a failed campaign require a new campaign with source hashes.
6. Verify cancellation/restart, approval and rejection, API authentication,
   budget reservations, provider failure handling, and sandbox boundaries with
   the existing real subprocess and persistence tests and relevant new regressions.
7. Publish only sanitized receipts and reproducible commands. Remove temporary
   credentials; scan staged source. Verify repository owner and author identity
   are MrMha. Do not enable billing or claim list-price estimates are invoices.

Controlled input examples, deliberate faults, and mock provider responses are
appropriate test fixtures when labeled. Measured outputs, elapsed times, pass
counts, and live API usage must come from actual execution. Fixed external
acceptance checks must not be weakened to make a candidate pass.

Passing a small controlled campaign is a release gate, not a population-level
reliability estimate or proof that every future mission succeeds. Report those
limits alongside the final results. Do not tag or advertise v1.0 while a required
gate remains unmet.
