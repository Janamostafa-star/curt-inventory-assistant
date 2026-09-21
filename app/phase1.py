import pytest

from app import db
from app.phase1 import answer, parse_intent


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    # throwaway database so tests never touch the real one
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.setup_database()


# ---------- intent parsing (no database needed) ----------

@pytest.mark.parametrize("message, entity", [
    ("How many brake pads do we have?", "brake pads"),
    ("how many brake pads do we have left", "brake pads"),
    ("Do we have any data loggers?", "data loggers"),
    ("quantity of wheel hub", "wheel hub"),
    ("is there a damper in stock", "damper"),
])
def test_stock_intent(message, entity):
    assert parse_intent(message) == {"intent": "stock_query", "entity": entity}


@pytest.mark.parametrize("message, entity", [
    ("Where is ECU?", "ecu"),
    ("where's the ecu", "ecu"),
    ("Where are the brake discs stored?", "brake discs"),
    ("location of the battery pack", "battery pack"),
])
def test_location_intent(message, entity):
    assert parse_intent(message) == {"intent": "location_query", "entity": entity}


@pytest.mark.parametrize("message, entity", [
    ("List all items in Electronics", "electronics"),
    ("List electronics items", "electronics"),
    ("show me the braking parts", "braking"),
    ("what items do we have in suspension", "suspension"),
    ("how many items in electronics", "electronics"),
])
def test_category_intent(message, entity):
    assert parse_intent(message) == {"intent": "category_query", "entity": entity}


def test_unknown_intent():
    assert parse_intent("what's the weather")["intent"] == "unknown"


# ---------- full answers (uses the temporary database) ----------

def test_stock_answer():
    assert answer("How many brake pads do we have?") == (
        "Brake Pads: 12 in stock (stored at Mechanical Workshop)."
    )


def test_location_answer():
    assert answer("Where is the ECU?") == "Location of ECU: Electronics Cabinet."


def test_category_answer_lists_every_item():
    reply = answer("List all items in Electronics")
    assert reply.startswith("Items in Electronics:")
    assert reply.count("\n- ") == 5


# ---------- edge cases ----------

def test_unknown_item():
    assert answer("Where is turbocharger?") == "I couldn't find 'turbocharger' in the inventory."


def test_misspelled_item_is_corrected_and_says_so():
    reply = answer("How many break pads do we have?")
    assert reply.startswith("Showing results for Brake Pads.")
    assert "12 in stock" in reply


def test_partial_name():
    assert answer("where are the pads") == "Location of Brake Pads: Mechanical Workshop."


def test_ambiguous_partial_asks_which_one():
    reply = answer("how many brake")
    assert "Did you mean" in reply
    assert "Brake Pads" in reply and "Brake Disc" in reply


def test_missing_item_asks_for_clarification():
    assert answer("How many do we have?") == "Which item would you like to check?"


def test_pronoun_without_memory_asks_for_clarification():
    assert answer("where is it") == "Which item would you like to check?"


def test_unknown_category_lists_available_ones():
    reply = answer("list all items in engine")
    assert "couldn't find a category" in reply
    assert "Electronics" in reply


def test_empty_message():
    assert answer("   ") == "Please type a question."


def test_unsupported_question_shows_help():
    assert "How many brake pads do we have?" in answer("tell me a joke")