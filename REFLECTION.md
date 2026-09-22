# Reflection

## Edge-case decisions

**Item not found.** Both phases resolve free-text input through
`InventoryService.find_part` / `find_category`, which returns a clear `not_found`
status rather than throwing or returning `None` silently. I chose to surface this
plainly ("I couldn't find X in the inventory") instead of trying to auto-correct to
something unrelated — a wrong guess felt worse than admitting nothing matched.

**Misspellings.** Rather than build separate correction logic for each phase, I put
fuzzy matching (`difflib`, via `InventoryService.find_part` / `find_category`) into the
shared data-access layer, so both Phase 1 and Phase 2 benefit from the same matcher and
stay consistent with each other. Phase 1 states the correction directly in its
templated response ("Showing results for Brake Pads."); Phase 2's system prompt gives
the LLM the same instruction, so a typo is never silently substituted in either phase —
the user always sees what was actually matched. Keeping this logic in one place also
means a future improvement to the matching (e.g. better handling of abbreviations)
automatically benefits both phases without duplicated work.

**Ambiguous matches.** When more than one part or category name is plausible, both
phases return an `ambiguous` status with a list of suggestions instead of picking one
silently. I'd rather over-ask than guess wrong on inventory data someone might act on.

**Missing/ambiguous questions** (e.g. "how many do we have" with no item named). Phase
1 strips filler words and, if nothing meaningful remains, asks "Which item would you
like to check?" instead of erroring. Phase 2's system prompt gives the model the same
instruction. I considered defaulting to "show everything" instead, but that felt like
it would bury the answer the user actually wanted.

**Follow-up context.** Phase 1 explicitly has no memory, so I made pronouns like
"it"/"they" resolve to an *empty* entity rather than trying to guess — the parser
treats a pronoun the same as a missing item, which triggers the clarification prompt
above. Phase 2 sends the full session history with every request and the system prompt
tells the model to resolve pronouns from prior turns, which is the behavior the brief's
own example ("Where are they stored?") calls for.

**Low stock.** I added a `low_stock` flag (quantity < 5) to `check_stock`'s result and
a separate `flag_shortage` tool, but made the model only call `flag_shortage` after the
user asks or agrees — an assistant that files shortage reports on its own initiative,
without being asked, felt like the wrong default for something that might trigger a
real reorder process later.

**Gemini availability.** During testing I repeatedly hit transient `503`
("high demand") and `504` (timeout) errors from Gemini's free tier, and once a genuine
`404` when a model I'd initially chosen (`gemini-2.5-flash`) turned out to have been
deprecated for new API keys since I started this project. This pushed me to add a
configurable fallback chain (`GEMINI_FALLBACK_MODELS`) rather than hardcoding one model
name — `_generate()` tries the primary model, and on a transient error walks through
the fallback list in order, stopping at the first one that succeeds. A non-transient
error (bad key, deprecated model) is raised immediately rather than wasted on retries.
This turned into one of the more realistic "production AI system" problems I ran into,
closer to what the task brief was actually testing for than anything I could have
planned for in advance.

## What I'd improve with more time

- **Persistence for conversation memory.** Right now sessions live in a plain
  in-memory dict and vanish on server restart. Fine for the brief, but I'd want Redis
  or a lightweight DB-backed store for anything closer to real use.
- **Faster/more consistent Phase 2 latency.** Response times vary depending on which
  model in the fallback chain ends up answering. I'd like to explore streaming
  responses so the UI shows partial output while a slower fallback model is still
  working, instead of one long spinner.
- **Authentication and rate limiting** on the FastAPI endpoints — currently anyone who
  can reach the server can call `/chat` freely, which is fine for a local demo but
  wouldn't be fine for anything shared beyond the team.
- **Structured logging/metrics** on tool calls (which tools get used most, how often
  fuzzy correction triggers) — useful for understanding real usage patterns if this
  were rolled out to the team for real.
- **Inventory updates through tool calling** (e.g. an `update_quantity` tool), with an
  explicit user-confirmation step before any write — the read-only tools in this
  submission were a deliberate scope decision for the 5-day timeline.
