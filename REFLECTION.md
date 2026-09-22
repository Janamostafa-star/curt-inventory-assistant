# Reflection

## Edge-case decisions

**Item not found.** Both phases resolve free-text input through
`InventoryService.find_part` / `find_category`, which returns a clear `not_found`
status rather than throwing or returning `None` silently. I chose to surface this
plainly ("I couldn't find X in the inventory") instead of trying to auto-correct to
something unrelated — a wrong guess felt worse than admitting nothing matched.

**Misspellings.** I split this deliberately between the two phases. Phase 2 uses
`difflib` fuzzy matching inside `InventoryService`, and the LLM is instructed (via the
system prompt) to state when it silently corrected something — e.g. "Showing results
for Brake Pads" — so the user isn't confused about which part they're actually looking
at. Phase 1 does *not* get fuzzy matching: the brief specifies it should use only
if/else or keyword matching with no AI model involved, and I felt that adding a fuzzy
layer would blur that line and misrepresent what "rule-based" means for the reviewer.
Instead, a misspelling in Phase 1 just falls through to the generic help message. This
is a real capability gap between the two phases, and I think it's a useful one to show
side-by-side in the demo — it's the clearest illustration of why Phase 2 exists.

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

## What I'd improve with more time

- **Persistence for conversation memory.** Right now sessions live in a plain
  in-memory dict and vanish on server restart. Fine for the brief, but I'd want Redis
  or a lightweight DB-backed store for anything closer to real use.
- **Fuzzy matching in Phase 1**, gated behind a flag, so a reviewer could see what a
  "rule-based + fuzzy" hybrid looks like without it being the default and without
  contradicting the "no AI model" constraint.
- **Faster/more consistent Phase 2 latency.** Response times ranged from ~3s to ~30s
  depending on which model in the fallback chain ended up answering. I'd like to
  explore streaming responses so the UI shows partial output while a slower fallback
  model is still working, instead of one long spinner.
- **Authentication and rate limiting** on the FastAPI endpoints — currently anyone who
  can reach the server can call `/chat` freely, which is fine for a local demo but
  wouldn't be fine for anything shared beyond the team.
- **Structured logging/metrics** on tool calls (which tools get used most, how often
  fuzzy correction triggers) — useful for understanding real usage patterns if this
  were rolled out to the team for real.
