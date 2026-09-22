import pytest

from app import db, tools


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    # throwaway database + empty flag log, so tests never touch real data
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.setup_database()
    monkeypatch.setattr(tools, "_flagged", {})


# ---------- check_stock ----------

def test_check_stock_found():
    result = tools.check_stock("brake pads")
    assert result["found"] is True
    assert result["name"] == "Brake Pads"
    assert result["quantity"] == 12
    assert result["location"] == "Mechanical Workshop"
    assert result["low_stock"] is False


def test_check_stock_reports_low_stock():
    assert tools.check_stock("ECU")["low_stock"] is True


def test_check_stock_fixes_typo_and_says_so():
    result = tools.check_stock("break pad")
    assert result["found"] is True
    assert result["name"] == "Brake Pads"
    assert result["match_type"] == "corrected"


def test_check_stock_unknown_item():
    result = tools.check_stock("turbocharger")
    assert result == {"found": False, "reason": "not_found", "suggestions": []}


def test_check_stock_ambiguous_item():
    result = tools.check_stock("brake")
    assert result["found"] is False
    assert result["reason"] == "ambiguous"
    assert len(result["suggestions"]) == 3


# ---------- list_by_category ----------

def test_list_by_category():
    result = tools.list_by_category("Electronics")
    assert result["found"] is True
    assert result["count"] == 5
    assert {"name", "quantity", "location"} <= set(result["items"][0])


def test_list_by_category_fuzzy():
    assert tools.list_by_category("electronic")["category"] == "Electronics"


def test_list_by_category_unknown_lists_available():
    result = tools.list_by_category("engine")
    assert result["found"] is False
    assert "Electronics" in result["available_categories"]


# ---------- flag_shortage ----------

def test_flag_shortage_then_repeat_is_harmless():
    assert tools.flag_shortage("ECU")["status"] == "flagged"
    assert tools.flag_shortage("ecu")["status"] == "already_flagged"


def test_flag_shortage_unknown_item():
    assert tools.flag_shortage("turbocharger")["status"] == "error"


# ---------- execute_tool: the only door the model goes through ----------

def test_execute_tool_runs_a_registered_tool():
    assert tools.execute_tool("check_stock", {"item_name": "ECU"})["quantity"] == 2


def test_execute_tool_rejects_unknown_tool():
    assert "Unknown tool" in tools.execute_tool("drop_table", {"item_name": "x"})["error"]


@pytest.mark.parametrize("args", [None, {}, {"item_name": ""}, {"item_name": "   "}, {"item_name": 42}])
def test_execute_tool_rejects_bad_arguments(args):
    assert "error" in tools.execute_tool("check_stock", args)


def test_execute_tool_rejects_oversized_argument():
    result = tools.execute_tool("check_stock", {"item_name": "a" * 101})
    assert "too long" in result["error"]


def test_execute_tool_ignores_extra_arguments():
    result = tools.execute_tool("check_stock", {"item_name": "ECU", "sql": "DROP TABLE parts"})
    assert result["found"] is True


def test_execute_tool_survives_a_crashing_tool(monkeypatch):
    def boom(_):
        raise RuntimeError("database exploded")

    monkeypatch.setitem(tools.TOOL_REGISTRY, "check_stock", (boom, "item_name"))
    assert "error" in tools.execute_tool("check_stock", {"item_name": "ECU"})


def test_declarations_match_registry():
    declared = {d["name"] for d in tools.TOOL_DECLARATIONS}
    assert declared == set(tools.TOOL_REGISTRY)