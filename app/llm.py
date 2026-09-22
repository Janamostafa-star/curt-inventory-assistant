"""Gemini client, the manual tool-calling loop, and per-session memory."""
import logging
import os

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from app.tools import TOOL_DECLARATIONS, execute_tool

load_dotenv()
logger = logging.getLogger("curt.llm")

# The model is a setting because Google renames models often. Override it in .env.
DEFAULT_MODEL = "gemini-flash-lite-latest"
MAX_TOOL_ITERATIONS = 5        # max Gemini calls per user message (stops infinite loops)
MAX_HISTORY_MESSAGES = 20      # per-session history cap
MAX_SESSIONS = 500             # memory guard: oldest sessions are dropped first
REQUEST_TIMEOUT_MS = 45_000    # per attempt; overloaded models can be slow to answer
RETRY_ATTEMPTS = 4             # total tries per Gemini call (1 + 3 retries)
# Temporary Google-side failures worth retrying: 499 cancelled, 500/502 internal,
# 503 overloaded ("high demand"), 504 deadline exceeded. 429 = rate limit.
RETRYABLE_STATUS_CODES = (429, 499, 500, 502, 503, 504)

SYSTEM_PROMPT = """You are the CURT Inventory Assistant.

Rules:
- You have no direct access to the inventory. Use the tools for every inventory fact \
(quantities, locations, categories).
- Never invent quantities, locations, or part names. Only report what a tool returned.
- Use the conversation history to resolve words like "it", "they" and "those" to the \
last item or category discussed.
- If the user does not say which item or category they mean, ask a short clarification \
question instead of guessing.
- If a tool says an item was not found, tell the user and offer the suggestions if there \
are any. If the name is ambiguous, ask which one they meant. If check_stock returns \
match_type "fuzzy" or "partial", mention the name you used, for example \
"Showing results for Brake Pads."
- If check_stock reports low_stock true, say that stock is low and offer to flag a \
shortage. Only call flag_shortage after the user asks or agrees.
- Only answer inventory questions. Politely decline anything else and say what you can \
help with.
- Be concise."""

# session_id -> list of Content objects. In-memory only: lost when the server restarts.
conversations: dict[str, list[types.Content]] = {}

_client: genai.Client | None = None


def get_model() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL)


def get_fallback_models() -> list[str]:
    """Comma-separated list in .env, e.g.
    GEMINI_FALLBACK_MODELS=gemini-flash-latest,gemini-3.5-flash-lite
    Tried in order, after the primary model, only on transient errors."""
    raw = os.getenv("GEMINI_FALLBACK_MODELS", "").strip()
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return [m for m in models if m != get_model()]


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        _client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(
                    attempts=RETRY_ATTEMPTS,
                    initial_delay=1.0,      # seconds before the first retry
                    max_delay=8.0,
                    exp_base=2.0,           # 1s, 2s, 4s ...
                    http_status_codes=list(RETRYABLE_STATUS_CODES),
                ),
            ),
        )
    return _client


def _build_config() -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
        # We run the loop ourselves so every step can be validated, logged and demoed.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=0.2,
    )


# ---------- memory helpers ----------
def _is_user_text(content: types.Content) -> bool:
    return content.role == "user" and any(p.text for p in content.parts or [])


def _trim(history: list[types.Content]) -> list[types.Content]:
    """Keep the last MAX_HISTORY_MESSAGES messages, but never start in the middle of a
    tool exchange (a function_response without its function_call is rejected by Gemini)."""
    if len(history) <= MAX_HISTORY_MESSAGES:
        return history
    history = history[-MAX_HISTORY_MESSAGES:]
    while history and not _is_user_text(history[0]):
        history = history[1:]
    return history


def _save(session_id: str, history: list[types.Content]) -> None:
    conversations.pop(session_id, None)          # re-insert so recent sessions stay last
    conversations[session_id] = _trim(history)
    while len(conversations) > MAX_SESSIONS:
        del conversations[next(iter(conversations))]


# ---------- errors ----------
def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, errors.APIError):
        if exc.code == 429:
            return "The assistant is receiving too many requests right now. Please try again in a minute."
        if exc.code in (401, 403, 404) or "API_KEY_INVALID" in str(exc):
            return "The AI service rejected the request (check the API key and model name)."
        if exc.code in RETRYABLE_STATUS_CODES or exc.code >= 500:
            return ("The AI service is busy or timed out. Nothing was lost: "
                    "please send your message again in a few seconds.")
    return "Sorry, something went wrong while contacting the AI service. Please try again."


def _text_of(content: types.Content) -> str:
    return "".join(p.text for p in content.parts or [] if p.text and not p.thought).strip()


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, errors.APIError) and exc.code in RETRYABLE_STATUS_CODES


def _generate(client: genai.Client, contents: list[types.Content],
              config: types.GenerateContentConfig):
    """One Gemini call. Tries the primary model, then each configured fallback in
    order, stopping at the first one that doesn't raise a transient error. A
    non-transient error (bad key, bad model name, etc.) is raised immediately."""
    models_to_try = [get_model()] + get_fallback_models()
    last_exc: Exception | None = None
    for model in models_to_try:
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config)
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc):
                raise
            logger.warning("Model %s failed (%s), trying next", model,
                           getattr(exc, "code", "?"))
    raise last_exc


# ---------- the tool-calling loop ----------
def chat(session_id: str, message: str, client: genai.Client | None = None) -> dict:
    """Answer one user message. Returns {"response": str, "tool_calls": [...]}. Never raises."""
    tool_log: list[dict] = []
    try:
        client = client or get_client()
        # Work on a copy: history is only saved if the whole turn succeeds.
        working = list(conversations.get(session_id, []))
        working.append(types.Content(role="user", parts=[types.Part(text=message)]))
        config = _build_config()

        for _ in range(MAX_TOOL_ITERATIONS):
            response = _generate(client, working, config)
            if not response.candidates or response.candidates[0].content is None:
                return {"response": "I couldn't generate an answer. Please rephrase your question.",
                        "tool_calls": tool_log}
            model_content = response.candidates[0].content
            calls = response.function_calls

            if not calls:                       # plain text: this is the final answer
                working.append(model_content)
                _save(session_id, working)
                text = _text_of(model_content) or "I couldn't generate an answer. Please try again."
                return {"response": text, "tool_calls": tool_log}

            # Keep the model's turn exactly as returned, then answer every function call.
            working.append(model_content)
            result_parts = []
            for call in calls:
                args = dict(call.args or {})
                result = execute_tool(call.name, args)      # validates name and arguments
                logger.info("Tool call: %s(%s) -> %s", call.name, args, result)
                tool_log.append({"name": call.name, "args": args, "result": result})
                result_parts.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response=result)))
            working.append(types.Content(role="user", parts=result_parts))

        logger.warning("Stopped after %d model calls without a final answer", MAX_TOOL_ITERATIONS)
        return {"response": "I couldn't finish that request. Please try rephrasing your question.",
                "tool_calls": tool_log}
    except Exception as exc:                    # quota, network, timeout, bad key...
        logger.exception("Gemini call failed")
        return {"response": _friendly_error(exc), "tool_calls": tool_log}


def main() -> None:
    """Terminal chat with real Gemini, for a quick smoke test: python -m app.llm"""
    from app.db import init_db, seed_db
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    get_client()                     # fails fast with a clear message if the key is missing
    init_db()
    seed_db()
    print(f"CURT Inventory Assistant (Phase 2, model: {get_model()}, "
          f"fallbacks: {', '.join(get_fallback_models()) or 'none'}). Type 'quit' to exit.")
    while True:
        try:
            question = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        if question.strip().lower() in {"quit", "exit"}:
            break
        if question.strip():
            print(chat("terminal", question)["response"], "\n")


if __name__ == "__main__":
    main()