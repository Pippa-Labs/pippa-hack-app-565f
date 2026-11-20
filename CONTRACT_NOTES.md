# Pippa contract digest for London Companion

Short facts for coders and reviewers. CLAUDE.md is the full contract and wins on any conflict.

## Shape
- `app.py` holds `class App` with `manifest` and `handle(call) -> Result(data, narrative)`. Every app file sits at the top level.
- `docs/`, `scripts/` and `test_*.py` are not deployed. Data files go beside `app.py`.
- Python 3.12 stdlib only, plus `pippa_agentapp_sdk` and PyYAML. Use urllib, never requests or httpx.
- No state, no file writes, `persistence: none`. Under 3 MB. 20 s per call: aim for a few seconds and fan out with threads.
- Use `call.context.now`, never `datetime.now()`. A datetime output must carry an offset; we return HH:MM strings instead.
- `getattr(call.context, "explicit", False)` is True when the person picked the app with @. Never refuse because the words don't fit; refuse only for a missing or invalid input.

## Manifest
- Outcome id: dotted lowercase. The first segment has letters and digits only, with no underscore (`transportation.fares`).
- Types: string, integer, number, boolean, datetime, duration, timezone, array (needs `item_type`), object.
- `data` must match `output` exactly: every required field present, nothing extra, right types, never None.
- Inputs: keep them `required: false`. A string description must say "return an empty string if not mentioned". A number or boolean description must say "leave this out entirely if not mentioned; never guess".
- Tests must send booleans as True/False, not "true".
- `app.summary` must never contain "by". Routing sees its first clause (about 110 chars) and the first 4 phrases, round-robin across outcomes.
- 5-10 phrases per outcome, lower case, written the way people type. No `?`, at most 9 words.
- Never use the diary/reserved words: today, tomorrow, calendar, diary, meeting, schedule, remind, reminder, alarm, remember, forget, search, google, news. Avoid "this week".

## Network (l1_http.py)
- Every request goes through `l1_http.get_json(url, service, timeout)`. The single seam is `l1_http.fetch_bytes`.
- It sends a custom User-Agent; TfL and Nominatim need one.
- 300/400/404/422 → `NotFound`; 401/403 → `Blocked` ("<service> live data is unavailable right now."); 429 → `RateLimitedError`; timeouts and other errors → `ServiceUnavailableError`.
- Build URLs with urlencode/quote on fixed hosts. Never pass a provider's error text to the user.
- A user-facing failure is an AgentAppError subclass with `user_message`. Use `refuse(cls, message, internal)` to name the thing.

## Layers
- l1: fetch and parse one source. l2: calls l1 only. l3: calls l1 and l2. `app.py`: dispatch only.
- An entry function is `fn(inputs, now) -> {headline, facts[], source, as_of, ...declared extras}`. app.py adds `task` and drops undeclared keys.

## Tests (test_app.py)
- Run through `LocalRunner(App()).call(outcome, inputs, granted_secrets={}, now=NOW, request_id=..., explicit=...)`.
- Fixed `NOW` = 2026-11-20 14:30 UTC (a Friday; GMT, so London = UTC).
- Network stubbed with FakeNet via `monkeypatch.setattr(l1_http, "fetch_bytes", fake)`; the first URL fragment that matches wins.
- The tests only run in the build. Locally: scratchpad `sim.py` with the SDK stub (it does not check output shapes).

## Safety outcome
- Never auto-dial, and never call a place or route "safe" or "unsafe", or tell people to avoid an area.
- Crime answers always state the data month and "not a safety rating".

## Publish
- Pushing `main` publishes. Read the `pippa/publish` status and only the github-actions[bot] comment.
- `CODE_ERROR`: fix and push (stop after 5 in a row). `REFUSED`: do what it says. `PLATFORM_ERROR`: stop.
- 0.1.0 is a draft, so it can be re-pushed. Once submitted it is frozen: bump `app.version`.
