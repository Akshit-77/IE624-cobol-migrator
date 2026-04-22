# CLAUDE.md — Coding Practices for the COBOL → Python Agentic Migrator

This file is the source of truth for conventions in this project. Read it before writing code. When in doubt, match what's already in the tree — but never violate a rule below.

---

## 1. Project invariants (non-negotiable)

- **Python dependency management: `uv` only.** Never `pip install`, never `python -m venv`, never hand-edit `pyproject.toml` dependency lists.
- **Frontend: npm + Vite.** (`pnpm`/`yarn` are fine locally but don't commit their lockfiles.)
- **Python ≥ 3.11.** We rely on `typing.Self`, modern `TypedDict`, and `match`.
- **Scope discipline.** If it's listed under "Out of scope" in `cobol_agent_course_project_plan.md` (Docker, k8s, auth, vector DBs, multi-dialect parsers), do not add it.
- **Two services only:** `backend/` (FastAPI + LangGraph) and `frontend/` (Vite + React + TS). No third service, no microservice split.

---

## 2. Quick start

```bash
# First-time setup (installs all dependencies)
./setup.sh

# Run the application
./run.sh              # Development mode (auto-reload)
./run.sh --no-reload  # Stable mode (for testing migrations)
```

**What `setup.sh` does:**
1. Checks prerequisites (Python 3.11+, Node.js 18+, curl, git)
2. Installs `uv` (Python package manager) if not present
3. Installs backend Python dependencies via `uv sync`
4. Installs frontend Node.js dependencies via `npm install`
5. Creates `.env` from `.env.example` (you must add your API key)
6. Creates `logs/` and `data/` directories

---

## 3. Python conventions

### Dependencies with `uv`
```bash
uv add fastapi                    # runtime dep
uv add --dev pytest               # dev dep
uv remove <pkg>                   # remove
uv sync                           # install lockfile into .venv
uv run <cmd>                      # run a command in the project env
uv run python -m cobol_migrator   # module entry
uv lock --upgrade                 # bump versions deliberately
```
Commit `pyproject.toml` and `uv.lock`. Never commit `.venv/`.

### Style
- `from __future__ import annotations` at the top of every module.
- Full type hints on every function signature — including return types (`-> None` counts).
- Prefer `pydantic.BaseModel` at I/O boundaries (HTTP, LLM structured output, DB rows). Prefer `TypedDict` for internal graph state. Prefer `@dataclass(slots=True, frozen=True)` for small value types.
- `logging.getLogger(__name__)` — never `print()` in library code. `print` is allowed only in `scripts/` and CLI entry points.
- Use `pathlib.Path`, never `os.path`.
- Line length 100. Formatter: `ruff format`. Linter: `ruff check`.

### Exceptions
- Raise specific exceptions; define a module-level `class MigratorError(Exception)` hierarchy in `backend/src/cobol_migrator/errors.py`.
- Never bare `except:`. `except Exception as e:` is acceptable at the top of a node where we want to route to `finalize` with `state.error`.

---

## 4. LangGraph conventions

- **Nodes are pure-ish:** signature is `def node(state: AgentState) -> dict` and returns a *partial* state update. Never mutate the incoming `state` in place.
- **One responsibility per node.** Translate does not test. Test does not reflect. Don't pile logic on.
- **The `planner` node has no side effects** beyond its LLM call and emitting an event. No DB writes, no file writes, no subprocess — those belong to tool nodes.
- **Structured LLM output is mandatory.** Every LLM call uses `model.with_structured_output(PydanticModel)`. Do not parse free-form text with regex.
- **Every node emits exactly one event** via `state["emit"](event_type, payload)`. This is how the SSE stream stays honest.
- **Append-only history.** `python_drafts`, `test_runs`, `lessons_learned`, `tool_call_history` are appended to — never rewritten. Rollback is done by referencing an older `draft_id`, not by erasing.
- **Robustness guards are in code, not prompts.** Step budget, repeat detection, and JSON-retry live in the graph wrapper — never trust the LLM to self-limit.

---

## 5. FastAPI conventions

- Every route is `async def`. Blocking work goes through `await asyncio.to_thread(blocking_fn, ...)`.
- Every request and response has a Pydantic model. No `dict` in or out at the HTTP boundary.
- Errors return a structured body: `{"error_code": "STR_ENUM", "message": "...", "run_id": "..."}`. Use `HTTPException(status_code, detail=ErrorBody(...).model_dump())`.
- SSE endpoints:
  - Return `StreamingResponse(gen(), media_type="text/event-stream")`.
  - Each event is `f"data: {json.dumps(payload)}\n\n"`.
  - Send a heartbeat `: ping\n\n` every 15 s so proxies don't close the connection.
  - Always `yield` a final `data: {"type":"done"}\n\n` before closing.
- Dependency injection via `Depends(...)` — not module-level globals. The `RunRegistry` is injected, not imported.

---

## 6. Subprocess safety (non-trivial — read carefully)

Generated Python is untrusted. Rules:

- Always list-form: `subprocess.run(["cmd", "arg"], ...)`. Never `shell=True`.
- Always pass `timeout=` (default 10 s for test runs, 30 s for `cobc` compile).
- Execute generated code in a fresh `tempfile.TemporaryDirectory()`. Never `cwd=project_root`.
- Before executing generated code, walk its AST and **reject** any module whose top-level import targets `os`, `subprocess`, `socket`, `ctypes`, `shutil`, `pathlib` write operations, `requests`, `urllib`, or `multiprocessing`. Put this check in `backend/src/cobol_migrator/safety.py` and call it from `run_tests` and `validators/differential`.
- `env=` passes a minimal whitelist (`PATH`, `HOME`, `LANG`) — not `os.environ`.
- Capture stdout and stderr; truncate to 8 KB before storing in state (LLMs don't need more).

---

## 7. Frontend conventions

- TypeScript **strict** (`"strict": true` in `tsconfig.json`). `any` is a code-review blocker.
- Functional components + hooks only. One component per file; file name matches the component (`ScorecardCard.tsx`).
- **Server state → React Query.** **Local UI state → `useState`.** **Cross-page state → URL params.** No Redux, no Zustand, no Context-as-store.
- **SSE is raw `EventSource`** — do not wrap in React Query. Instantiate it in `useEffect`, clean up with `.close()` in the return.
- Tailwind utility classes inline. Extract a component once the same class string appears in ≥ 3 places.
- Monaco Editor is lazy-loaded (`React.lazy`) — it's large.
- All fetches go through `src/lib/api.ts`. No `fetch(...)` scattered in components.

---

## 8. Testing

### Backend
- `pytest` + `pytest-asyncio` (auto mode via `[tool.pytest.ini_options] asyncio_mode = "auto"`).
- File naming: `test_<module>.py`. Keep tests beside the module they test where practical.
- **Every LangGraph node has a unit test** with a hand-built `AgentState` fixture and (where applicable) a mocked LLM that returns canned structured output.
- **Integration tests** live under `backend/tests/integration/` and run the full graph against fixture COBOL programs under `backend/tests/fixtures/`.
- Slow tests are marked `@pytest.mark.slow` and excluded from the default `uv run pytest` via `addopts = "-m 'not slow'"`.
- Never hit real LLM APIs in unit tests. Integration tests may hit real APIs but are marked `slow` and run manually.

### Frontend
- Vitest + React Testing Library (added in Stage 5).
- Prefer testing user-visible behavior over implementation. Query by role, not by test-id unless necessary.

---

## 9. Error handling in the agent

- **The agent must never let an exception reach the API layer.** Wrap every node in a try/except in the graph wrapper; on failure, set `state.error`, emit an `error` event, and route to `finalize`.
- The API layer then returns the run with `status="errored"` and a populated error field. The user sees a clear message, not a 500.
- `finalize` always runs — even on error — and always writes a row to SQLite.

---

## 10. Secrets, config, and settings

- All config in one Pydantic settings class (`backend/src/cobol_migrator/config.py`) loading from `.env` via `pydantic-settings`.
- `.env` is gitignored. `.env.example` lists every key with a comment. Keep them in sync.
- **Multi-provider LLM support:** OpenAI, Anthropic, Google (Gemini), and xAI (Grok). Set `LLM_PROVIDER` to switch.
- API keys per provider: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`. Only the key for the selected provider is required.
- **All models are configurable via `.env`** — change models anytime without code changes:
  ```
  # Example: Use different OpenAI models
  OPENAI_TRANSLATE_MODEL=gpt-4o
  OPENAI_JUDGE_MODEL=gpt-4o
  OPENAI_PLANNER_MODEL=gpt-4o
  OPENAI_ANALYZE_MODEL=gpt-4o-mini
  OPENAI_REFLECT_MODEL=gpt-4o-mini
  ```
- Each provider has 5 configurable model slots: `{PROVIDER}_TRANSLATE_MODEL`, `{PROVIDER}_JUDGE_MODEL`, `{PROVIDER}_PLANNER_MODEL`, `{PROVIDER}_ANALYZE_MODEL`, `{PROVIDER}_REFLECT_MODEL`
- Use `get_chat_model(task)` or `get_structured_model(task, schema)` from `cobol_migrator.models` — never instantiate LLM clients directly in nodes.
- Never log API keys. Redact secrets from any error payload before emitting via SSE.

---

## 11. Git and commits

- **Commit after every stage milestone.** When you complete meaningful work on any stage (a feature, a fix, a verification passing), commit immediately and push to the repository. Do not accumulate large uncommitted changesets.
- **Repository:** given at the time of code writing
- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`. Prefix with stage number when relevant (e.g., `feat(stage-2): implement planner node`).
- One stage = one branch = one PR where practical. Commit messages describe *why*, not *what*.
- Never commit: `.env`, `.venv/`, `node_modules/`, `dist/`, `benchmark_results.csv` (it's regenerated), `*.db` SQLite files, any COBOL binary from GnuCOBOL compile.

---

## 12. What NOT to add (explicit out-of-scope list)

If you find yourself reaching for one of these, stop and re-read §1:

- Docker, Docker Compose, Kubernetes, Helm.
- Auth, user accounts, session management, rate-limiting middleware.
- Vector databases, RAG over docs, cross-run memory.
- Multi-dialect COBOL parsers (IBM, HP3000, Fujitsu variants).
- OpenTelemetry, Prometheus, Grafana, Sentry.
- Message queues (Celery, RQ, Redis Streams).
- A "plugin system" for validators.

A course project is graded on depth in the agentic loop and validation stack — not breadth.

---

## 13. When the user asks for something out of scope

Say so directly, cite the source plan, and offer the in-scope alternative. Do not silently expand scope to "be helpful."
