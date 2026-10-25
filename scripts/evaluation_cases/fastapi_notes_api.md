Use only local SPEC.md for research with zero external web dependencies. Deliver at most 3 source files (including root `app.py`), unit tests in `tests/`, `README.md`, QA verification, and a launch draft.
Exact signatures and behavior in `app.py`:
1. `def create_app() -> FastAPI`: returns a FastAPI instance with isolated in-memory note storage per `create_app()` call.
2. `app = create_app()` exposed at module level in `app.py`.
3. `POST /notes`: accepts JSON `{"title": str}`. `title` must be a `str` whose stripped value `clean = title.strip()` satisfies `1 <= len(clean) <= 80` and `len(title) <= 80`; otherwise return HTTP `422`. Stores `clean` with sequential positive integer `id` starting at `1`, never reusing deleted IDs and returns HTTP `201` with `{"id": int, "title": clean}`.
4. `GET /notes`: returns HTTP `200` with `list[dict]` of existing notes in insertion order.
5. `GET /notes/{note_id}`: `note_id` must be a positive integer (`>= 1`, else `422`). Returns HTTP `200` with note JSON or `404` if missing.
6. `DELETE /notes/{note_id}`: `note_id` must be a positive integer (`>= 1`, else `422`). Deletes the note and returns HTTP `204` with empty body if present, or `404` if missing.
