# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning rules used here:

- **MAJOR** — incompatible changes: removed or renamed configuration keys, breaking API changes,
  database schema that cannot be migrated automatically.
- **MINOR** — backward-compatible features.
- **PATCH** — backward-compatible bug fixes, performance work, dependency and documentation updates.

The authoritative version string lives in [`VERSION`](VERSION), mirrored by
`core/version.py` and `pyproject.toml`. Run `python scripts/check_version.py` to verify.

---

## [1.4.1] — 2026-11-03

### Fixed

- **Gateway refused to start whenever CHANNEL_TOKEN was configured.** The startup preflight's
  token probe posted to `/v1/ingest` **without** the `X-Channel-Token` header; core answers 401 to
  any unauthenticated request, so the probe read "mismatch" and the gateway exited with code 2 —
  even with a perfectly correct token on both sides. The probe now sends the configured header;
  only a 401/403 *with* the token counts as a mismatch. Regression from the QQ self-check commit
  (019a22c), found while bringing a real gateway back up.

---

## [1.5.0] — 2026-11-04

QQ becomes a fully optional, self-contained channel: deploy the protocol end from the console,
no separate clones or manual installs.

### Added

- **In-console protocol deployment wizard** (`core/qq_deploy.py` + `core/api/qq_deploy.py` +
  dashboard). The「接入 QQ」dialog now walks the whole path: check → 一键部署 (downloads the
  latest NapCat Shell release with the strict segmented downloader, progress bar, resume on
  interruption, config/ preserved) → 启动协议端 (background boot via `scripts/napcat_local.py`)
  → 扫码登录 (existing QR proxy) → logged in. The dialog states plainly that the agent does not
  depend on QQ; skipping it costs nothing.
- `core/napcat_fetch.py` — the strict segmented downloader promoted to an importable module
  (206-only, per-block length validation, incremental flush, mirror rotation, CRC at the end);
  `scripts/napcat_fetch_part.py` remains as its CLI wrapper.
- Endpoints: `GET /api/channel/qq/deploy-status`, `POST /api/channel/qq/deploy`,
  `POST /api/channel/qq/protocol-start` (bot uin from `AUTHORIZED_QQ`).
- Windows AV file-lock hardening: zip open/extract retries with backoff.
- Tests: deploy state machine, config/ never overwritten, honest failure surfacing, single-flight
  lock, protocol start refuses undeployed binary and spawns with the right args.

---

## [1.8.1] — 2026-11-09

### Fixed

- **`qr_code` leaked its input to a third party.** When the local `qrcode` package was missing,
  the tool fell back to `api.qrserver.com`, sending the user's text — passwords, addresses,
  private links — as a query parameter to an external server. Every call in a default install
  took that path, because the package was not a dependency at all. QR codes are now generated
  locally only; if the library is absent the tool refuses and tells you how to install it,
  rather than quietly exfiltrating data. `qrcode[pil]` added to both dependency manifests.
- Two tests lock this down: one asserts no external domain appears anywhere in the result
  (verified by reintroducing the old code and watching it fail), the other asserts a real
  `data:image/png;base64,` payload comes back.

### Changed

- Test suite: 979 → 981.

---

## [1.8.0] — 2026-11-08

Any-account toolkit: tools and knowledge that do not depend on whose QQ account is logged in.

### Added

- **13 general-purpose tools** (`core/tools_general.py`), taking the registry from 42 to 55:
  - Unit conversion (length / weight / volume / temperature), timestamp ↔ date,
    Base64, hashing (md5/sha1/sha256/sha512), JSON format/minify/validate,
    regex testing, random draw, password generation, colour format conversion.
  - Weather, translation and currency conversion — the three that need the network,
    each with an honest failure message instead of a silent empty answer.
  - Currency conversion tries three providers in order, because single free FX sources
    are unreliable (frankfurter.app returned a 301 that httpx does not follow by default).
- **Rule routing for the offline case.** Without a model key, 「5km 换算成英里」、
  「时间戳 1730000000 是几号」、`base64 编码 …`、`sha256 …`、`json 格式化 …` still reach
  the right tool. General routing runs *before* QQ platform actions — otherwise a question
  containing 戳 was captured by `qq_poke`.
- **7 general knowledge entries** (`GEN001`–`GEN007`): time/date conventions, unit conversion
  facts, file & encoding basics, privacy red lines, how to say "I don't know", common office
  workflows, and debugging order. Knowledge base: 14 → 21 entries.
- `core/tool_result.py` — result-contract helpers extracted so `tools` and `tools_general`
  no longer import each other in a cycle.

### Fixed

- **`tool_color("#0E5C55")` returned `#000537`.** The branch test looked at how many digits
  it could extract, and hex letters are not digits — `0E5C55` yielded `['0','5','55']`,
  was misread as RGB and re-emitted as a different colour. Now the input *form*
  (comma/parentheses vs bare hex) decides the branch.
- **`empty_result` contract regression.** While extracting helpers, `status="empty"` was
  briefly replaced by `empty=True`; three tests caught it immediately because the pipeline
  branches on `status` and would have turned "you have no reminders" into an escalation ticket.

### Changed

- `AUTHORIZED_QQ` cleared in this deployment: no account holds privileged tool access by
  default. Set it explicitly after scanning the account you actually want to own the bot.

### Known limitation

- Emote library: 300 of 1289 files carry visual tags. `index.json` is a 300-entry ring
  (oldest evicted) by design, so tagging stops there; the remaining files are historical
  and not currently served.

---

## [1.7.1] — 2026-11-08

QQ account identity: it now follows whoever actually logged in, with no default account baked in.

### Fixed

- **A hardcoded QQ number acted as the default identity.** `scripts/launcher.py` fell back to a
  specific account when its config file was missing, so after switching accounts the launcher
  would quick-login the *old* number while the user was scanning a new one. The default is now an
  empty string, and starting the protocol side with an empty number is refused with a clear
  message instead of silently reusing cached credentials.
- **Account details had no single source of truth.** `uin` / `nick` were stitched together in the
  frontend and could survive an account switch. New `core/qq_identity.py` stores the identity that
  NapCat actually reported at login; `/api/channel/qq/login-status` now always returns an
  `account` object (present even when NapCat is unreachable) and the UI renders only that.
- **Switching accounts left stale data.** Identity sync now *replaces* rather than merges: a
  different `uin` overwrites `nick` and avatar outright. There is no path where account A's name
  survives into account B's session.
- **`logged_in_at` was rewritten on every poll.** The change check compared the raw
  `avatar_url` argument (often empty) against the derived URL, so a new timestamp was written
  every 3 seconds and the UI could never show how long the session had been up.

### Added

- `tests/test_qq_identity.py` — 7 tests covering: empty identity before first login, identity
  following the real login, switch-replaces-not-merges, stable timestamps on repeated polls,
  empty input not clearing a known identity, the `account` shape from the API, and opt-in
  behaviour of the authorization follow.
- `QQ_IDENTITY_FOLLOW_LOGIN` (default off). When enabled, a successful login appends the account
  to `AUTHORIZED_QQ`. Off by default on purpose: scanning a QR code should not silently grant
  privileged tool access.

### Changed

- Test suite: 958 → 965.

---

## [1.7.0] — 2026-11-07

Type safety done end to end, and the last persistence split.

### Added

- **`mypy` is now blocking across the whole codebase** — 126 pre-existing errors fixed to zero
  across 88 files. Most were not cosmetic: annotations disagreed with what the code actually
  does, which is exactly the class of bug that fails at runtime instead of at build time.
- **`core/session_store.py`** — sessions, messages, visitor identity and ticket creation moved out
  of `core/store.py` (194 lines). Same compatibility-shim pattern as `reminder_store`:
  `store.*` keeps forwarding, so all 40 call sites are untouched.

### Fixed

- **Duplicate `reminder_*` definitions in `core/store.py`.** The earlier extraction left three
  old implementations behind; they shadowed the forwarding layer, so behaviour happened to be
  correct but the file contained two divergent copies of the same functions. Removed.
- **`gateway/bot.py` could read `result.ok` when `result` was still `None`** — `split_reply()`
  returning an empty list left the variable unassigned and the reply-out event then raised
  `AttributeError`. Now guarded.
- **`_post_ingest` was annotated `-> str` but returns and is consumed as a dict.** Corrected.
- **`_ambient_push` / `_ambient_snapshot` were annotated as `str` but store dicts.** Corrected.
- **`upsert_tool` claimed `-> dict` while it can return `None`.** Corrected in both the
  implementation and the shim.
- **`list_files` reused the `except ... as e` binding** after Python deletes it. Renamed.

### Changed

- `build.chunkSizeWarningLimit` raised to 600 with a comment explaining why: the 542 KB echarts
  chunk is lazy — measured 0 echarts requests on the home page, and 8 charts rendered only after
  navigating to `/dashboard`. A warning that fires on correct behaviour trains people to ignore
  warnings.

### Known limitation

- `core/main.py` still holds 79 route handlers inside `create_app`. They share roughly 30 closure
  variables (`app`, `hub`, `app.state.*`, config clients). Extracting them needs those
  dependencies threaded explicitly through each router — a structural refactor with real
  regression risk, not a mechanical move. Left as-is deliberately; the file is verified clean
  (no duplicate handlers, no duplicate routes, 99 routes).

---

## [1.6.1] — 2026-11-07

Correctness and tooling: two real bugs, plus the i18n work that surfaced them.

### Fixed

- **Reminders created through the admin API fired at the wrong hour.** `core/api/reminders.py`
  passed `datetime.now(timezone.utc)` into `parse_due()`, overriding that function's contract
  that "the user's 9am means local 9am". The same sentence — 「明天早上9点」 — therefore landed
  8 hours apart depending on whether it came from the web API or from the chat tool. The call
  now uses the local-clock default, and a regression test asserts both entry points agree to
  within 60 seconds. This was the last P1 left over from the 1.4.0 roadmap.
- **`list_files` reused the `except ... as e` variable after the block.** Python deletes that
  binding when the handler exits, so the following list comprehension read a deleted name
  (mypy flags it as `Trying to read deleted variable`; at runtime it can raise `NameError`
  on some paths). Renamed to `item`.

### Added

- `scripts/i18n_wrap.py`, `scripts/i18n_gap.py`, `scripts/i18n_fill.py` — idempotent tooling to
  wrap untranslated UI strings, detect gaps, and fill translations. Gap count is now **0**
  (437 source msgids, 748 `en.ts` entries; was ~536 gaps).
- Blocking `mypy` for new modules in CI (`core/api/*` with `--follow-imports=skip`). Legacy
  modules stay advisory — 126 pre-existing errors across 16 files would be a risky
  big-bang change, so new code simply stops adding to the debt.

### Changed

- `bootstrapAdminToken` aborts after 3s. It resolves before React mounts the root, so a fetch
  that never settles left the page permanently blank with no console error.
- Test suite: 956 → 958.

---

## [1.6.0] — 2026-11-06

Delivery layer: the project becomes installable, shippable, and self-explanatory on first run.

### Added

- **`zhike` CLI** (`core/cli.py`): `init`, `run`, `doctor`, `status`, `version`. `init` creates
  `.env`, generates 32-byte admin/channel tokens and creates the database; `run` starts the
  server and auto-initializes on first use; `doctor` reports what is actually true (deps, tokens,
  ports, database, frontend build, live service health) instead of making users guess.
  Registered as `[project.scripts] zhike` and installable via `pip install -e .`.
- **Single-file Windows exe** (`packaging/`, `scripts/build_exe.py`): 60 MB, no Python needed on
  the target machine. `build_exe.py` verifies `/healthz`, `/readyz`, the console page, and a real
  tool call before declaring success.
- **`core/paths.py`**: one place that resolves read-only assets (bundled in `_MEIPASS`) versus
  writable data (`zhike-data/` next to the exe, or `%LOCALAPPDATA%\Zhike`). Previously every
  module computed `Path(__file__).parent` independently, which cannot survive packaging.
- **First-run setup wizard** (`core/api/setup.py` + `SetupWizard.jsx`): four steps (welcome,
  model, optional QQ, self-check), resumable, skippable, local-only, auto-skipped for existing
  installations.
- **Standalone QR login page** (`/qq-scan`): a persistent page designed to be left open — the QR
  refreshes every 10 seconds so the displayed code is always scannable, with a visible countdown
  and 3-second login polling.
- **Competitive research** (`docs/research/COMPETITIVE-RESEARCH.md`): 12 comparable projects with
  source-level evidence, including which deployment model each uses and why.

### Fixed

- **Admin pages hung on "Loading" forever when no token was in storage.** `baseHeaders` omitted
  the admin header, the resulting 401 was swallowed by the loading state, and `TokenGate` — which
  only renders on an auth *error* — never appeared. The app now resolves the local token before
  mounting.
- **The protocol-end downloader hardcoded a proxy at `127.0.0.1:7897`.** On any machine without
  that proxy running, all six parallel ranges failed with `ConnectError` and the download stalled
  with no actionable message. Proxies are now opt-in via `NAPCAT_DL_PROXY`.
- **`zhike init` could leave two `ADMIN_TOKEN` entries in `.env`** by appending instead of filling
  the existing empty key in place.
- **CLI crashed on Windows GBK consoles** at the first `✓` (`UnicodeEncodeError`), which made the
  exe fail on its very first command. Output now degrades to ASCII when UTF-8 is unavailable.
- **`zhike status`/`doctor` misreported healthy checks as failures** (`configured` was read as a
  boolean `ok`).
- **`report_demo.py` clicked an element that only exists in a non-default tab** of the runs page,
  so demo recording failed. It now switches to the action-audit view first.

### Changed

- Test suite: 937 → 954 backend, 12 → 16 frontend.
- README (both languages) rewritten around a demo GIF and three commands, with the screenshots
  moved below the fold.

---

## [1.5.1] — 2026-11-06

Observability and offline regression hardening: the fixes here are all things that
were silently broken rather than visibly broken.

### Fixed

- **500 responses lost their correlation ID.** `create_app()` registered
  `@app.exception_handler(Exception)` twice; FastAPI keys handlers by exception class, so
  the second registration silently replaced the first. Every unexpected error therefore
  returned `{"detail": "服务内部错误，已记录日志"}` with no `request_id` and no
  `X-Request-ID` header — exactly the information needed to line a user report up with the
  server log. Now there is a single handler, and the middleware also stores the ID on
  `request.state` because the contextvar is already reset by the time `ServerErrorMiddleware`
  handles the exception. The body and header now agree.
- **`logger.debug()` never reached the log ring.** `events_log.attach()` hardcoded
  `setLevel(INFO)` on both `core` and `cs.events`, so the diagnostics worth having —
  retrieval scores, rule-layer hits, model fallbacks — were dropped before reaching the
  in-memory ring and `/api/admin/logs`. New `CS_AGENT_LOG_LEVEL` (default `INFO`) makes the
  level configurable; the console's log page gained a `DEBUG` filter and per-row
  `request_id` (click to copy).
- **Protocol-end download could hang forever.** `napcat_fetch._download_part()` reset its
  `idle` counter after printing "all mirrors struggling", so a download where every mirror
  returned zero bytes never returned. `strict_download()` then blocked on `thread.join()`
  forever: the console deploy wizard sat at "下载中…" with no error and no timeout, and the
  only plausible user conclusion was a bad network. Consecutive zero-byte rounds are now
  capped (`NAPCAT_DL_MAX_STALL_ROUNDS`, default 30) and the stall raises a message naming
  the last real error. Thread exceptions are also propagated to the caller instead of
  surfacing as a bare size mismatch.
- **Character cards could contradict themselves.** Nothing checked that a catchphrase or a
  reference utterance wasn't also listed as a taboo. Such a card makes the consistency guard
  reject every normal reply, and the retry loop rewrites until it gives up. The offline
  harness now extracts the terms actually banned by "不用「X」"-style taboos and fails on
  overlap. Matching is deliberately narrow: a naive substring test flagged `tech`'s
  "必须先看现象和日志" as banning its own catchphrase 日志.

### Added

- `scripts/harness_all.py` — single entry point for the offline regression checks
  (version consistency, persona consistency, persona registry, guard behaviour, emote tags).
  Five checks, a couple of seconds, no model calls, no tokens. `--list` shows what would run,
  `--live` adds the checks that need a model or a running core.
- CI now runs `scripts/harness_all.py` after the test suite, so persona drift and version
  drift fail the build instead of being noticed later by hand.
- `tests/test_version_tracing.py`, `tests/test_napcat_fetch.py` — the segmented downloader
  (206-only, size and CRC validation, mirror rotation, stall handling) and the
  version/tracing switches had 17%/39%/41% coverage; the three modules are now at 82%.

### Changed

- Test suite: 913 → 937 passing.

---

## [Unreleased]

### Planned

- Split the remaining monoliths (`core/store.py`, `core/tools.py`, `core/main.py`) along entity
  boundaries: sessions, memory, actions, and the remaining admin routes.
- Raise frontend i18n coverage from ~36% to full parity.
- Promote `mypy` from advisory to blocking for newly added modules.

---

## [1.4.0] — 2026-11-03

QQ QR-code login from the console: link the bot's QQ account without ever opening the NapCat
WebUI or a terminal.

### Added

- **Console QR login** (`core/api/qq_login.py` + dashboard). The「渠道连接」card's QQ row gets a
  「扫码登录」 button: core proxies the NapCat WebUI login endpoints (`/api/auth/login` with the
  `SHA256(token + ".napcat")` handshake, `CheckLoginStatus`, `GetQQLoginQrcode` /
  `RefreshQRcode`), the frontend renders the returned URL as a QR and polls status every 3s.
  Scan with the phone → the dialog flips to "logged in" with the account name; the gateway
  reconnects on its own.
- Credential handling stays server-side: the WebUI token never reaches the browser, cached
  `Credential` auto re-logins once on 401/403, and business errors wrapped in HTTP 200
  (`{"code": 非0}`) are surfaced as text instead of pretending the QR works. QR auto-refreshes
  at ~110s (NapCat expires them at ~2min).
- New config keys (see `.env.example`): `NAPCAT_WEBUI_URL` (default `http://127.0.0.1:6099`),
  `NAPCAT_WEBUI_TOKEN`. Unconfigured → the dialog says so honestly and points at the old paths.
- `tests/test_qq_login_api.py` — auth handshake hash, token never in responses, 200-wrapped
  business errors surfaced, credential expiry re-login and replay, status shape.

---

## [1.3.1] — 2026-11-03

Gateway resilience pass: no more silently dropped messages during short core outages, and HTTP
connection reuse across the gateway.

### Added

- **Ingest retry after core outage** (`gateway/bot.py`). When `/v1/ingest` exhausts its inline
  retry (core restarting, network blip), the gateway now schedules one delayed redelivery
  (`GATEWAY_INGEST_RETRY_DELAY`, default 30s, concurrent tasks capped at 20) through the same
  deliver path instead of dropping the user's message with only a log line. Second failure is
  final — delayed nonsense replies are worse than silence. Disable with
  `GATEWAY_INGEST_RETRY_DELAY=0`.
- Regression tests: pool reuse, exactly-one retry on failure, no re-schedule after the redelivery
  itself fails, bounded retry concurrency.

### Changed

- **Gateway and provider connectivity checks reuse the shared HTTP connection pool**
  (`core/http_pool`). `gateway/bot.py` created a fresh `httpx.AsyncClient` for every QQ message,
  every heartbeat tick and every image/audio download — six sites, all now pooled per
  (event loop, timeout) bucket, exactly like the core side already did. `core/providers.py`
  model-list and connectivity probes migrated as well. The shutdown hook closes the pool.
- `docs/ARCHITECTURE.md` documents the two-layer persona design (built-in character cards vs
  session-bound DB cards) and why they are deliberately not merged, plus the gateway-core fault
  tolerance paths.

---

## [1.3.0] — 2026-11-03

Character cards: every persona preset ships with a structured, maintainable fact sheet instead of
a single blob of prompt text, and replies are checked against it before they leave the pipeline.

### Added

- **Character card system** (`core/character.py`). A persona is now two layers: structured
  *canon* (fields, narrative sections, voice samples, taboos, unknown-topic policy) stored as
  `core/data/characters/<id>.json`, and a fixed-order *renderer* that assembles the system
  prompt. All 15 built-in presets have cards. Cards with the same fact stated once stop the
  model from inventing an age, an address or a backstory mid-conversation.
- **Character consistency guard** (`core/character_guard.py`) wired into the reply pipeline.
  After the generic quality gate, the active persona's card is looked up by rendering-equality
  (`core/character.card_for_persona`) and the reply is checked for assistant-speak, identity
  drift, taboo phrases and lost catchphrases. Violations trigger one rewrite; if the rewrite
  fails on card-only issues the original text is kept rather than a fallback line in someone
  else's voice. Personas edited away from any card render are skipped — never judged against a
  card that does not match.
- `/api/personas` now reports `has_card` and a `card_summary` per preset; `GET
  /api/personas/{id}/card` returns the structured card. The console persona page shows a
  「设定」 badge on presets that have a card and opens a viewer with facts, narrative sections,
  voice samples and boundaries.
- `scripts/character_harness.py` — offline character regression (static mode is CI-safe, no
  model calls): every preset must render completely, pass drift checks, and stay distinct from
  the other presets (similarity floor 0.72).
- `tests/test_character_guard.py`, `tests/test_tools_uncovered.py` — card rendering, drift
  detection, pipeline integration, plus coverage for four previously untested tools
  (`initiate_refund`, `list_files`, `qq_friends`, `qq_groups`).

### Fixed

- **Identity rewrite criteria were hard-coded to the cat persona.** The gate demanded the word
  「猫」 in any reply to "who are you", so switching to 老陈 (tech support) or 知客 (secretary)
  forced a pointless rewrite of every correct answer. Anchors are now inferred from the persona
  itself (`quality._identity_anchors`), and rewrite prompts no longer re-assert the cat persona.
- **Fallback lines spoke in the wrong character's voice.** When a rewrite fails, the fallback
  short line follows the active persona (cat-flavored for the cat, neutral for everyone else).
  Detection ignores the modem sense of 猫 (「拨号上网的猫」 in the tech card is a dial-up modem,
  not the persona).
- `test_persona_apply_writes_config` asserted the old short preset text; persona apply now
  writes the card render and reports `has_character_card`.

### Changed

- `POST /api/personas/{id}/apply` prefers the character card render over the short preset
  blurb and records `source: character_card | preset` in the audit entry.

---

## [1.2.1] — 2026-11-03

Fixes found by running the whole stack against a real model, a real database and a real QQ
gateway. Every item below was reproduced first, then fixed, then locked down with a test.

### Fixed

- **Knowledge retrieval returned nothing for natural long questions.** `开发票需要多久？`
  scored zero hits while `发票` hit fine — the coverage gate demanded `max(2, n//2)` matching
  bigrams, and long questions spend most of their bigrams on question phrasing (`需要`/`多久`),
  so the single topical hit got diluted below the threshold. Net effect: the more naturally a
  user phrased a question, the less likely the knowledge base was found. Gate now scales with
  the number of content bigrams and keeps a floor of one match.
- **Question-phrasing words were treated as topic words.** `需要`, `多久`, `多长时间`, `几天`,
  `是否`, `能否` and similar are now in the filler list, so they no longer count toward
  retrieval overlap.
- **Citation badges never appeared.** The LLM reply path passed `parsed.citations` straight
  through, but the prompt never asks the model to emit citations, so the field was always
  empty — the console's `依据 KB005` badge could never render and users had no way to tell a
  grounded answer from a hallucinated one. Citations are now backfilled from the retrieval
  results when the model does not supply them.
- **Half-sentences were sent to users when the model hit its token limit.** Truncation was
  only handled when the content was *empty*; a reply that had content but was cut off mid
  answer was returned as a success. Reproduced live: `退货怎么弄` replied only
  `那我把步骤交出来喵` and dropped the entire return procedure. Content-bearing truncations
  now retry once with a larger budget and keep whichever answer is longer.
- **`t()` did not interpolate untranslated strings.** i18next only substitutes placeholders on
  a resource hit; this project uses Chinese source text as the key, so untranslated strings
  came back verbatim and the data page rendered a literal `schema v{v}`. Missing keys are now
  interpolated locally.
- **Stat card labels were clipped in English.** The cards were a single flex row, so
  `Total conversations` overflowed and rendered as `Total conve`. Now a two-row grid with
  wrapping labels; verified with a DOM overflow check in both languages.
- **Admin emotes API never returned vision tags.** Tags live in `tags.json` keyed by data
  path, but the endpoint read `it["tags"]` from the index, which never has that key — the
  console fell back to showing `image` for every item and tag search matched nothing, even
  after tagging the whole library.
- **`scripts/tag_emotes.py` hard-coded a model name** (`u2-med`) that 404s against the
  configured endpoint, and wrote its results only once at the end — a timeout lost the entire
  run. It now reads the configured model, falls back across providers, and flushes
  incrementally.
- **Gateway defaulted to the wrong core port.** `CORE_BASE_URL` defaulted to `8000` while core
  listens on `8010`. The gateway started cleanly and connected to NapCat, then posted every
  message into a dead port — from the user's side, "the bot ignores the group" with no visible
  error anywhere. Default aligned and a startup preflight now fails loudly.
- **`scripts/napcat_local.py` hung on unattended start.** It shelled out to NapCat's
  `launcher-user.bat`, which ends in `pause`; with no console to press a key, the process
  blocked forever and `run.log` contained only `请按任意键继续...`. It now invokes
  `NapCatWinBootMain.exe` directly and sets `NAPCAT_QQ_DATA_DIR` so the QQ kernel data
  directory is isolated from the desktop client.
- **`scripts/napcat_local.py` now reports QQ/NapCat version mismatch.** A QQ auto-update makes
  injection fail silently: the QQ process starts but stays at ~20 MB, no ports open, no logs
  written. `status` and `start` print an explicit warning instead.

### Added

- `frontend/src/lib/interpolate.ts` + tests — placeholder interpolation shared by `t()`.
- Regression tests for retrieval recall on long natural questions, citation backfill, emotes
  API tag exposure, and untranslated-string interpolation.
- `docs/QQ-LOCAL.md` troubleshooting for the two failure modes above, including the upstream
  NapCat issue reference.

### Changed

- `README.md` / `README.en.md` rewritten with current screenshots and an explicit
  limitations section.
- Landing page badges and links updated; test-count badge replaced with a version badge so it
  cannot go stale.

---

## [1.2.0] — 2026-11-02

Consolidates the engineering hardening work done after the 1.1.0 tag: persistence layer
decomposition, a new reminders module, Chinese numeral time parsing fidelity, and honest
capability documentation.

### Added

- `core/config_store.py`, `core/agent_store.py`, `core/tool_store.py` — persistence concerns
  extracted out of the 2,100-line `core/store.py`, each with its own focused test suite
  (`tests/test_config_store.py`, `tests/test_agent_store.py`, `tests/test_tool_store.py`).
- `core/reminder_parser.py` — self-contained reminder time parser (relative, absolute, clock,
  weekly, monthly expressions) with a fixed reference-time API; no service-layer imports.
- `core/api/reminders.py` — reminders REST router extracted from `core/main.py`.
- `core/version.py` + `VERSION` — single source of truth for the version string, surfaced by
  `/healthz` and `/api/agent/health`.
- `scripts/check_version.py` — guards against version drift between `VERSION`, `core/version.py`
  and `pyproject.toml`; wired into CI.
- `CHANGELOG.md`, `ROADMAP.md`, `docs/RELEASING.md` — release and roadmap management.
- Emote library console page (`/emotes`) with search, thumbnails and confirmed deletion.
- Dual-view run history page (`/runs`): agent tasks and action audit in one place.
- Windows-portable QQ end-to-end harness.

### Changed

- `core/main.py` route registration moved into `core/api/` packages where cohesion allows;
  SPA fallback must stay last (documented in `docs/ARCHITECTURE.md`).
- Dashboard charts now load on demand instead of shipping in the initial bundle.
- Settings console split from a 1,268-line file into a 283-line shell plus
  `src/settings/{bits,panels-core,panels-kb,panels-data}.tsx`.
- Console information architecture regrouped by task rather than by backend module.
- README rewritten to state verified capabilities and explicit limitations instead of
  promising everything works out of the box.

### Fixed

- Chinese numeral time expressions (`十分钟后`, `每天八点`) no longer lose their original
  span when routed to the reminder tool.
- `remember` rule routing returned empty arguments, causing a tool-level `TypeError`.
- Weekday reminders created on a weekend were silently dropped.
- Knowledge-base instruction material could leak verbatim into group chat replies.
- Three QQ tool routing gaps found on a real device: send, voice and proactive message.
- Windows subprocess output decoded as UTF-8 raised `UnicodeDecodeError` in the executor
  policy tests.

### Removed

- Feature branch `fix/qq-e2e-windows` — merged into `main`.

---

## [1.1.0] — 2026-10-29

First tagged release. Establishes the self-hosted QQ/Web secretary positioning and the
security baseline.

### Added

- Tiered action permissions (L0/L1/L2) with approval flow and hash-chained audit log.
- Knowledge base import (markdown, text, PDF, DOCX, XLSX), trial recall playground, export.
- Hybrid retrieval: SQLite FTS5 BM25 plus optional `sqlite-vec` vectors fused with RRF.
- Multi-provider LLM fallback chain with parallel racing, cooldown and content-level failover.
- Native function calling with JSON fallback for endpoints that lack tool support.
- MCP server exposing context-free tools to external MCP clients.
- Voice input (faster-whisper) and voice output (edge-tts), both optional.
- Persona system with 15 presets as a single backend source of truth.
- Prometheus metrics, request IDs, `/healthz` and `/readyz` probes.
- Docker multi-stage image with non-root runtime and health check.
- QQ gateway: NapCat / OneBot v11, group throttle, bot-to-bot loop guard, emote learning.
- Bilingual documentation site under `docs/`.

### Security

- Admin and channel token separation with constant-time comparison.
- `cryptography` pinned to 50.0 to clear known CVEs in 43.x/46.x.
- Path traversal and SSRF guards on executor and outbound HTTP clients.
- Secrets encrypted at rest with Fernet; masked on read-back.

---

## [1.0.0] — 2026-09-29

Initial public code drop: FastAPI core, React console, SQLite storage, rule-based reply engine,
LLM integration, and the first QQ channel adapter.
