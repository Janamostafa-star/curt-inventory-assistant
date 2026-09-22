"""Tests for the Gemini tool-calling loop, matched to the actual app/llm.py in this
project. Uses a scripted fake Gemini client, so no API key or network is needed."""
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from app import db, llm, tools


# ---------- a fake Gemini ----------

def model_calls_tool(name, args, call_id="call-1"):
    part = types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args))
    return types.Content(role="model", parts=[part])


def model_says(text):
    return types.Content(role="model", parts=[types.Part(text=text)])


def api_error(code, message="error"):
    """Build a real errors.APIError so _friendly_error / _is_transient see the right type."""
    return errors.APIError(code, {"error": {"code": code, "message": message}})


class FakeResponse:
    def __init__(self, content):
        self.candidates = [SimpleNamespace(content=content)]
        self.function_calls = [p.function_call for p in (content.parts or []) if p.function_call]


class FakeClient:
    """Plays back a scripted list of model turns/exceptions and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []          # (model, contents) for every call, including failed ones
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        self.requests.append((model, list(contents)))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return FakeResponse(step)


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.setup_database()
    monkeypatch.setattr(tools, "_flagged", {})
    monkeypatch.setattr(llm, "conversations", {})
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)


# ---------- tool calling ----------

def test_model_calls_tool_then_answers():
    client = FakeClient([
        model_calls_tool("check_stock", {"item_name": "brake pads"}),
        model_says("We have 12 brake pads."),
    ])
    result = llm.chat("s1", "How many brake pads do we have?", client=client)

    assert result["response"] == "We have 12 brake pads."
    assert result["tool_calls"][0]["name"] == "check_stock"
    assert result["tool_calls"][0]["result"]["quantity"] == 12
    assert len(llm.conversations["s1"]) == 4  # user, tool-call, tool-result, final answer


def test_tool_result_is_sent_back_to_the_model():
    client = FakeClient([
        model_calls_tool("check_stock", {"item_name": "ECU"}),
        model_says("2 ECUs, in the Electronics Cabinet."),
    ])
    llm.chat("s1", "Where is the ECU?", client=client)

    second_request_contents = client.requests[1][1]
    tool_result_part = second_request_contents[-1].parts[0]
    assert tool_result_part.function_response.name == "check_stock"
    assert tool_result_part.function_response.response["location"] == "Electronics Cabinet"


def test_unknown_tool_is_rejected_but_chat_continues():
    client = FakeClient([
        model_calls_tool("drop_table", {"item_name": "parts"}),
        model_says("Sorry, I can't do that."),
    ])
    result = llm.chat("s1", "delete everything", client=client)

    assert "error" in result["tool_calls"][0]["result"]
    assert result["response"] == "Sorry, I can't do that."


# ---------- memory ----------

def test_follow_up_receives_earlier_messages():
    client = FakeClient([
        model_calls_tool("check_stock", {"item_name": "brake pads"}),
        model_says("We have 12 brake pads."),
        model_calls_tool("check_stock", {"item_name": "brake pads"}),
        model_says("They are in the Mechanical Workshop."),
    ])
    llm.chat("s1", "How many brake pads do we have?", client=client)
    llm.chat("s1", "Where are they stored?", client=client)

    third_request_contents = client.requests[2][1]  # first model call of the follow-up
    assert len(third_request_contents) == 5          # 4 earlier messages + the new question
    assert third_request_contents[0].parts[0].text == "How many brake pads do we have?"
    assert third_request_contents[-1].parts[0].text == "Where are they stored?"


def test_sessions_do_not_share_memory():
    client = FakeClient([model_says("Hi."), model_says("Hello.")])
    llm.chat("alice", "hi", client=client)
    llm.chat("bob", "hello", client=client)

    assert len(client.requests[1][1]) == 1  # bob's first request has only his own message


def test_history_is_trimmed_and_starts_on_a_user_message(monkeypatch):
    monkeypatch.setattr(llm, "MAX_HISTORY_MESSAGES", 4)
    client = FakeClient([model_says(f"reply {i}") for i in range(6)])
    for i in range(6):
        llm.chat("s1", f"question {i}", client=client)

    history = llm.conversations["s1"]
    assert len(history) <= 4
    assert llm._is_user_text(history[0])


def test_oldest_session_is_evicted_once_max_sessions_reached(monkeypatch):
    monkeypatch.setattr(llm, "MAX_SESSIONS", 2)
    client = FakeClient([model_says("hi")] * 3)
    llm.chat("s1", "hi", client=client)
    llm.chat("s2", "hi", client=client)
    llm.chat("s3", "hi", client=client)

    assert "s1" not in llm.conversations       # oldest evicted
    assert set(llm.conversations) == {"s2", "s3"}


# ---------- model fallback (the feature this session added) ----------

def test_falls_back_to_the_next_model_on_a_transient_error(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-model")
    client = FakeClient([api_error(503, "overloaded"), model_says("Hi from backup.")])

    result = llm.chat("s1", "hi", client=client)

    assert result["response"] == "Hi from backup."
    assert client.requests[0][0] == llm.DEFAULT_MODEL
    assert client.requests[1][0] == "backup-model"


def test_does_not_fall_back_on_a_non_transient_error(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-model")
    client = FakeClient([api_error(404, "model not found")])

    result = llm.chat("s1", "hi", client=client)

    assert len(client.requests) == 1  # never tried the fallback
    assert "rejected the request" in result["response"]


def test_all_models_failing_returns_friendly_message_and_keeps_memory_clean(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "backup-model")
    client = FakeClient([api_error(503), api_error(503)])

    result = llm.chat("s1", "hi", client=client)

    assert "busy or timed out" in result["response"]
    assert llm.conversations == {}  # a failed turn is never saved


# ---------- failure handling ----------

def test_tool_loop_is_capped():
    client = FakeClient([model_calls_tool("check_stock", {"item_name": "ECU"})] * llm.MAX_TOOL_ITERATIONS)
    result = llm.chat("s1", "loop forever", client=client)

    assert "couldn't finish" in result["response"]


def test_quota_error_message():
    client = FakeClient([api_error(429, "quota exceeded")])
    assert "too many requests" in llm.chat("s1", "hi", client=client)["response"]


def test_empty_model_answer_is_handled():
    client = FakeClient([types.Content(role="model", parts=[types.Part(text="")])])
    result = llm.chat("s1", "hi", client=client)
    assert "try again" in result["response"]


def test_missing_api_key_gives_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_client", None)
    result = llm.chat("s1", "hi", client=None)
    assert "went wrong" in result["response"] or "API" in result["response"]