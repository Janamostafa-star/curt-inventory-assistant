# CURT Inventory Assistant

A small assistant for querying CURT's parts inventory in natural language. Built in two
phases:

- **Phase 1** — rule-based (regex/keyword parsing), no AI model, no network calls.
- **Phase 2** — a FastAPI backend that lets Google Gemini answer questions using
  function calling, backed by the same SQLite database, with per-session conversation
  memory.

Both phases share one database and one data-access layer (`InventoryService`) — the LLM
never touches SQL directly.

---

## 1. Architecture

```
┌─────────────────┐        ┌───────────────────┐        ┌───────────────┐
│   Streamlit UI   │───────▶│   FastAPI backend   │───────▶│  Gemini API    │
│ streamlit_app.py │  HTTP  │     app/main.py     │  tool   │ (function call)│
│                   │        │                     │  calls  │                │
│ Phase 1 toggle ───┼───┐    │  POST /chat          │◀───────┘                │
│ Phase 2 toggle ───┘   │    │  GET  /inventory      │
└───────────────────┘   │    │  GET  /health         │
                          │    └──────────┬──────────┘
                          │               │
                          │      app/tools.py (execute_tool)
                          │               │
                          ▼               ▼
                   app/phase1.py   app/service.py (InventoryService)
                   (regex parser)         │
                          │               ▼
                          └────────▶ app/db.py ──▶ SQLite (curt_inventory.db)
```

**Key design point:** `InventoryService` (in `app/service.py`) is the *only* place in
the project that runs SQL. Phase 1's parser and Phase 2's tool layer both call it —
neither writes queries directly, and Gemini never sees the database, only tool results.

### Request flow, Phase 2

1. Streamlit sends `{session_id, message}` to `POST /chat`.
2. `app/main.py` calls `app.llm.chat(session_id, message)`.
3. `app/llm.py` loads that session's history, sends it + the user's message to Gemini
   with the three tool declarations attached, and disables Gemini's automatic function
   calling so the loop can be run and logged manually.
4. If Gemini requests a tool call, `app/tools.py:execute_tool()` validates the tool name
   and argument, then runs the corresponding `InventoryService` method.
5. The tool's JSON result is sent back to Gemini as a `function_response`, and the loop
   repeats (up to 5 iterations) until Gemini returns plain text.
6. The full turn (user message, model turns, tool calls/results) is saved into
   `conversations[session_id]` — an in-memory dict, trimmed to the last 20 messages.

---

## 2. Tech stack

| Layer         | Choice                                  |
|---------------|------------------------------------------|
| Database      | SQLite (`curt_inventory.db`)             |
| Backend API   | FastAPI + Uvicorn                        |
| LLM provider  | Google Gemini (`google-genai` SDK), function calling |
| Frontend      | Streamlit                                |
| Tests         | pytest                                   |
| Secrets       | `python-dotenv`, `.env` (not committed)  |

Full dependency list: see `requirements.txt`.

---

## 3. Local setup

### Prerequisites
- Python 3.11+ (developed on 3.13)
- A Gemini API key (free tier is fine) — https://ai.google.dev

### Install

```bash
git clone <your-repo-url>
cd curt_inventory_assistant
python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### Configure secrets

Copy the example env file and fill in your key:

```bash
cp .env.example .env
```

Edit `.env`:

```
GEMINI_API_KEY=your_real_key_here
GEMINI_MODEL=gemini-flash-lite-latest
GEMINI_FALLBACK_MODELS=gemini-flash-latest,gemini-3.5-flash-lite
DB_PATH=curt_inventory.db
API_URL=http://localhost:8000
```

`.env` is git-ignored — never commit real keys. `.env.example` (below) is the template
that ships in the repo instead.

### Set up / seed the database

The database is created and seeded automatically on first run of either the backend
or the Phase 1/2 terminal tools, but you can also do it explicitly:

```bash
python -m app.db
```

This creates `curt_inventory.db` (if missing) with a `parts` table and inserts 15
seed parts (only if the table is currently empty — safe to re-run).

### Run the backend (required for Phase 2)

```bash
uvicorn app.main:app --reload
```

Runs on `http://localhost:8000` by default. Visit `http://localhost:8000/docs` for the
interactive FastAPI docs.

### Run the frontend

In a separate terminal:

```bash
streamlit run streamlit_app.py
```

Opens the chat UI with a Phase 1 / Phase 2 toggle, live inventory sidebar, and
tool-call inspector.

### Run tests

```bash
pytest
```

### Terminal smoke tests (no Streamlit)

```bash
python -m app.phase1     # Phase 1, rule-based, terminal chat
python -m app.llm        # Phase 2, real Gemini call, terminal chat
```

---

## 4. Database

**Table: `parts`**

| Column   | Type    | Notes                          |
|----------|---------|---------------------------------|
| id       | INTEGER | primary key, autoincrement      |
| name     | TEXT    | unique                          |
| quantity | INTEGER | `CHECK (quantity >= 0)`         |
| category | TEXT    |                                  |
| location | TEXT    |                                  |

Seeded with 15 parts across 5 categories: Braking, Electronics, Suspension,
Drivetrain, Chassis.

**Data-access layer** (`app/service.py`, class `InventoryService`) — the only code that
runs SQL:

| Method | Purpose |
|---|---|
| `get_part(name)` | exact, case-insensitive lookup |
| `part_exists(name)` | bool |
| `get_all_parts()` | full inventory, ordered by category/name |
| `get_by_category(category)` | exact, case-insensitive category lookup |
| `update_quantity(name, delta)` | adjusts stock, raises if it would go negative |
| `get_categories()` | distinct category list |
| `find_part(text)` | fuzzy-resolves messy user text to a real part (see §6) |
| `find_category(text)` | same, for categories |

`find_part` / `find_category` use `difflib`-based matching (`_match()`) to turn
misspelled or partial input into a confident match, a set of suggestions, or an
"ambiguous" / "not found" result — this is what both phases use for edge-case handling.

---

## 5. API reference (Phase 2 backend)

Base URL: `http://localhost:8000` (configurable via `API_URL`)

### `POST /chat`

Request:
```json
{ "session_id": "abc123", "message": "How many brake pads do we have?" }
```

Response:
```json
{
  "response": "We have 12 Brake Pads in stock (stored at Mechanical Workshop).",
  "tool_calls": [
    {
      "name": "check_stock",
      "args": { "item_name": "brake pads" },
      "result": {
        "found": true,
        "name": "Brake Pads",
        "quantity": 12,
        "location": "Mechanical Workshop",
        "category": "Braking",
        "low_stock": false,
        "match_type": "exact"
      }
    }
  ]
}
```

- `session_id` (1–100 chars) and `message` (1–500 chars) are required.
- Conversation history is kept in memory per `session_id` (lost on server restart).
- Errors never crash the endpoint: LLM/API failures return HTTP 502 with a friendly
  message; the underlying `chat()` function itself never raises.

### `GET /inventory`

Returns the full inventory as JSON (same shape as `InventoryService.get_all_parts()`):

```json
[
  { "id": 1, "name": "Brake Pads", "quantity": 12, "category": "Braking", "location": "Mechanical Workshop" },
  ...
]
```

### `GET /health`

```json
{ "status": "ok" }
```

---

## 6. Tool-calling definitions (Phase 2)

Gemini is given three tools (declared in `app/tools.py`, executed only through
`execute_tool()`, which validates the tool name and a single string argument before
calling `InventoryService`):

| Tool | Argument | Behavior |
|---|---|---|
| `check_stock(item_name)` | part name (free text) | Resolves the name via `find_part`, returns quantity, location, category, and a `low_stock` flag (quantity < 5). If not resolved, returns `found: false` with a reason (`not_found` / `ambiguous`) and suggestions. |
| `list_by_category(category)` | category name (free text) | Resolves via `find_category`, returns every part in that category with quantity/location. If not resolved, returns available categories as a hint. |
| `flag_shortage(item_name)` | part name (free text) | Logs a "low stock" warning (`logger.warning`, in-memory `_flagged` dict — no real alerting per the brief). Idempotent: flagging twice returns `already_flagged`. |

The model decides when to call which tool based on the user's message; the system
prompt (in `app/llm.py`) instructs it to never invent inventory facts, to only call
`flag_shortage` after the user asks or agrees, and to use match-type hints (`corrected`,
`partial`) to tell the user when it silently fixed a typo.

**Reliability:** Gemini calls go through the SDK's built-in retry (4 attempts,
exponential backoff) for transient errors (429/499/500/502/503/504); if the primary
model (`GEMINI_MODEL`) still fails, `_generate()` walks through
`GEMINI_FALLBACK_MODELS` in order until one succeeds or all are exhausted.

---

## 7. Edge-case handling

| Case | Phase 1 (rule-based) | Phase 2 (LLM) |
|---|---|---|
| **Item not in inventory** | `find_part`/`find_category` returns `not_found`; the assistant says so plainly, e.g. *"I couldn't find 'turbo boosters' in the inventory."* | Same underlying `find_part` result surfaces as `found: false, reason: "not_found"`; the model reports it and offers any suggestions rather than guessing. |
| **Misspelled / partial name** | Regex-only, no fuzzy layer by design (Phase 1 is explicitly "no AI model"). A misspelling like "break pads" won't match any intent pattern well enough and falls back to the generic help message. This is an intentional limitation: adding real fuzzy correction to Phase 1 would blur the line the brief draws between "rule-based" and "LLM-powered." | `InventoryService.find_part` uses `difflib` to fuzzy-match (`match_type: "corrected"`). The model is instructed to state the correction transparently, e.g. *"Showing results for Brake Pads."* rather than silently substituting. |
| **Ambiguous match** (e.g. text matches more than one part/category) | Returns a "did you mean X or Y?" prompt built from the suggestion list, rather than guessing. | Same underlying `ambiguous` status; the model is instructed to ask which one the user meant instead of picking one. |
| **Missing/ambiguous question** (e.g. "how many do we have" with no item) | `clean_entity()` strips filler words; if nothing is left, the assistant asks *"Which item would you like to check?"* rather than erroring. | The system prompt instructs the model to ask a short clarifying question when no item/category is named, instead of guessing. |
| **Follow-up context** ("where are they stored?") | Not supported — Phase 1 has no memory, so pronouns (`it`/`they`/`those`) are explicitly treated as "entity missing," prompting the user to name the item. | Full per-session history is sent with every request, and the system prompt tells the model to resolve pronouns from prior turns. |
| **Low stock** | N/A — Phase 1 doesn't flag shortages. | `check_stock` reports `low_stock: true` under a threshold of 5; the model mentions it and offers to call `flag_shortage`, but only acts after the user agrees. |

---

## 8. Known limitations / future improvements

- Conversation memory is in-process only (a dict); restarting the FastAPI server clears
  all sessions. Acceptable per the brief, but a real deployment would want Redis or a
  DB-backed store.
- Gemini's free tier has low daily request quotas per model, which is why a fallback
  model chain exists — response times can vary widely (a few seconds to ~30s) when a
  model is under load or a request needs to fall back.
- Phase 1 has no fuzzy matching by design; a "best of both" mode (rule-based with a
  fuzzy layer) would be a natural next step if the brief allowed it.
- No authentication on the API — fine for a local/demo tool, not for production.

---

## 9. Project structure

```
curt_inventory_assistant/
├── app/
│   ├── db.py          # schema, seeding, get_connection()
│   ├── service.py      # InventoryService — the only SQL in the project
│   ├── phase1.py        # rule-based intent parsing + answer()
│   ├── tools.py          # Gemini tool declarations + execute_tool()
│   ├── llm.py             # Gemini client, tool-calling loop, session memory
│   └── main.py             # FastAPI app: /chat, /inventory, /health
├── tests/
│   ├── test_service.py
│   ├── test_phase1.py
│   ├── test_tools.py
│   └── test_llm.py
├── streamlit_app.py    # frontend: phase toggle, chat, inventory sidebar
├── requirements.txt
├── .env.example
└── README.md
```
