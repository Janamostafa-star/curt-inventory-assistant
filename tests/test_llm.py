"""Tests for the Gemini loop using a scripted fake client (no API key, no network)."""
from types import SimpleNamespace

import pytest
from google.genai import types

from app import db, llm, tools


# ---------- a fake Gemini ----------

def model_calls_tool(name, args):
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return types.Content(role="model", parts=[part])


def model_says(text):
    return types.Content(role="model", parts=[types.Part(text=text)])


class FakeResponse:
    def __init__(self, content):
        self.candidates = [SimpleNamespace(content=content)]
        self.function_calls = [p.function_call for p in content.parts if p.function_call]


class FakeClient:
    """Plays back scripted model turns and records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []  # snapshot of the history sent on each call
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        self.requests.append(list(contents))
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
    # user question, model tool call, tool result, final answer
    assert len(llm.conversations["s1"]) == 4


def test_tool_result_is_sent_back_to_the_model():
    client = FakeClient([
        model_calls_tool("check_stock", {"item_name": "ECU"}),
        model_says("2 ECUs, stored in the Electronics Cabinet."),
    ])
    llm.chat("s1", "Where is the ECU?", client=client)

    second_request = client.requests[1]
    tool_result = second_request[-1].parts[0].function_response
    assert tool_result.name == "check_stock"
    assert tool_result.response["result"]["location"] == "Electronics Cabinet"


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

    third_request = client.requests[2]   # first model call of the follow-up
    assert len(third_request) == 5       # 4 earlier messages + the new question
    assert third_request[0].parts[0].text == "How many brake pads do we have?"
    assert third_request[-1].parts[0].text == "Where are they stored?"


def test_sessions_do_not_share_memory():
    client = FakeClient([model_says("Hi."), model_says("Hello.")])
    llm.chat("alice", "hi", client=client)
    llm.chat("bob", "hello", client=client)

    assert len(client.requests[1]) == 1  # bob's first request has only his own message


def test_reset_session_clears_memory():
    llm.chat("s1", "hi", client=FakeClient([model_says("Hi.")]))
    llm.reset_session("s1")
    assert "s1" not in llm.conversations


def test_history_is_trimmed_and_starts_on_a_user_message(monkeypatch):
    monkeypatch.setattr(llm, "MAX_HISTORY_MESSAGES", 4)
    client = FakeClient([model_says(f"reply {i}") for i in range(6)])
    for i in range(6):
        llm.chat("s1", f"question {i}", client=client)

    history = llm.conversations["s1"]
    assert len(history) <= 4
    assert llm._is_plain_user_turn(history[0])


# ---------- failure handling ----------

def test_tool_loop_is_capped():
    client = FakeClient([model_calls_tool("check_stock", {"item_name": "ECU"})] * llm.MAX_TOOL_ROUNDS)
    result = llm.chat("s1", "loop forever", client=client)

    assert "trouble" in result["response"]
    assert llm.conversations["s1"] == []          # nothing half-finished is kept


def test_api_failure_returns_friendly_message_and_keeps_memory_clean():
    client = FakeClient([RuntimeError("connection reset"), model_says("Hi again.")])

    first = llm.chat("s1", "hi", client=client)
    assert "couldn't reach" in first["response"]
    assert llm.conversations["s1"] == []

    second = llm.chat("s1", "hi", client=client)   # the session still works afterwards
    assert second["response"] == "Hi again."


def test_quota_error_gets_a_specific_message():
    client = FakeClient([RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")])
    assert "free quota" in llm.chat("s1", "hi", client=client)["response"]


def test_empty_model_answer_is_handled():
    client = FakeClient([types.Content(role="model", parts=[types.Part(text="")])])
    result = llm.chat("s1", "hi", client=client)
    assert "rephrase" in result["response"]
    assert llm.conversations["s1"] == []


# ---------- input validation ----------

@pytest.mark.parametrize("message", ["", "   ", None])
def test_empty_message_is_rejected(message):
    with pytest.raises(ValueError):
        llm.chat("s1", message, client=FakeClient([]))


def test_oversized_message_is_rejected():
    with pytest.raises(ValueError):
        llm.chat("s1", "x" * (llm.MAX_MESSAGE_LENGTH + 1), client=FakeClient([]))