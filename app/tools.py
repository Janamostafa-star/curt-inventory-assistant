"""Tool layer for Phase 2.

The LLM never touches the database. It can only ask the backend to run the three
functions below, and every one of them goes through InventoryService.

Each tool returns a plain, JSON-serializable dict so the result can be sent
straight back to the model.
"""
import logging
from datetime import datetime, timezone

from app.service import InventoryService

logger = logging.getLogger("curt.tools")

service = InventoryService()

LOW_STOCK_THRESHOLD = 5   # quantity strictly below this counts as low stock
MAX_ARG_LENGTH = 100      # longest text we accept from the model as an argument

# Shortage flags raised in this process (in memory; the brief only asks for a log/print).
_flagged: dict[str, dict] = {}

RESOLVED = ("exact", "partial", "corrected")


# ---------- the three tools ----------

def check_stock(item_name: str) -> dict:
    """Quantity and storage location of one part."""
    result = service.find_part(item_name)
    if result["status"] in RESOLVED:
        part = result["part"]
        return {
            "found": True,
            "name": part["name"],
            "quantity": part["quantity"],
            "location": part["location"],
            "category": part["category"],
            "low_stock": part["quantity"] < LOW_STOCK_THRESHOLD,
            "match_type": result["status"],  # exact / partial / corrected (typo fixed)
        }
    return {
        "found": False,
        "reason": result["status"],          # not_found or ambiguous
        "suggestions": result["suggestions"],
    }


def list_by_category(category: str) -> dict:
    """Every part in one category."""
    result = service.find_category(category)
    if result["status"] in RESOLVED:
        name = result["category"]
        items = service.get_by_category(name)
        return {
            "found": True,
            "category": name,
            "match_type": result["status"],
            "count": len(items),
            "items": [
                {"name": p["name"], "quantity": p["quantity"], "location": p["location"]}
                for p in items
            ],
        }
    return {
        "found": False,
        "reason": result["status"],
        "suggestions": result["suggestions"],
        "available_categories": service.get_categories(),
    }


def flag_shortage(item_name: str) -> dict:
    """Log a 'low stock' flag for a part. Flagging the same part twice is harmless."""
    result = service.find_part(item_name)
    if result["status"] not in RESOLVED:
        return {"status": "error", "reason": result["status"], "suggestions": result["suggestions"]}

    part = result["part"]
    already = part["name"] in _flagged
    if not already:
        _flagged[part["name"]] = {
            "quantity": part["quantity"],
            "flagged_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.warning(
            "LOW STOCK FLAG: %s (quantity=%s, location=%s)",
            part["name"], part["quantity"], part["location"],
        )
    return {
        "status": "already_flagged" if already else "flagged",
        "name": part["name"],
        "quantity": part["quantity"],
    }


# ---------- registry + schemas (what the model is allowed to see) ----------

# tool name -> (function, name of its single text argument)
TOOL_REGISTRY = {
    "check_stock": (check_stock, "item_name"),
    "list_by_category": (list_by_category, "category"),
    "flag_shortage": (flag_shortage, "item_name"),
}

# Plain JSON-schema declarations. llm.py converts these to Gemini's types.
TOOL_DECLARATIONS = [
    {
        "name": "check_stock",
        "description": (
            "Get the current quantity and storage location of ONE part in the CURT "
            "inventory. Use for questions like 'how many X do we have' or 'where is X'. "
            "The result also says whether stock is low."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_name": {
                    "type": "string",
                    "description": "Name of the part, e.g. 'brake pads' or 'ECU'.",
                }
            },
            "required": ["item_name"],
        },
    },
    {
        "name": "list_by_category",
        "description": (
            "List all parts in one inventory category (for example Electronics, Braking, "
            "Suspension, Drivetrain, Chassis) with their quantities and locations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category name, e.g. 'Electronics'.",
                }
            },
            "required": ["category"],
        },
    },
    {
        "name": "flag_shortage",
        "description": (
            "Record a low-stock flag for ONE part so the team knows to reorder it. "
            "Only use this when stock is low and the user wants it flagged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_name": {
                    "type": "string",
                    "description": "Name of the part to flag, e.g. 'ECU'.",
                }
            },
            "required": ["item_name"],
        },
    },
]


# ---------- safe execution (the only door the LLM goes through) ----------

def execute_tool(name: str, args: dict | None) -> dict:
    """
    Run a tool requested by the model.
    - only tools in TOOL_REGISTRY can run
    - only the one expected argument is read (anything extra is ignored)
    - the argument must be a non-empty string of reasonable length
    - errors come back as data, never as a crash
    """
    entry = TOOL_REGISTRY.get(name)
    if entry is None:
        return {"error": f"Unknown tool '{name}'."}

    func, param = entry
    value = (args or {}).get(param)
    if not isinstance(value, str) or not value.strip():
        return {"error": f"Argument '{param}' must be a non-empty string."}
    if len(value) > MAX_ARG_LENGTH:
        return {"error": f"Argument '{param}' is too long (max {MAX_ARG_LENGTH} characters)."}

    try:
        return func(value.strip())
    except Exception:  # never let a tool failure crash the chat
        logger.exception("Tool %s failed", name)
        return {"error": "The tool failed while reading the inventory."}