# Recorded regression fixtures

`caller_sensitive_csv.py.txt` is the unchanged delivered `app.py` from repeat 1 of
live batch `20261025T132547Z-4c6011`. SHA-256:
`8fe298186c124108e8b5c8976ed91dc4dc3c56e18fae0d88cf63be452a196c85`.
That mission failed its generated QA, while the earlier fixed external CSV
checks passed. Its repair inspected caller filenames to change empty-input
behavior. It is deliberately invalid delivery code retained as a regression
fixture, not an example or a passing product result.

`csv_original_checks.py.txt` preserves the CSV external checks from commit
`ea82355`. Current checks retain those assertions and add caller-independence
coverage. The regression replays both versions against the recorded delivery.
