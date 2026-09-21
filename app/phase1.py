"""Phase 1: rule-based assistant (no AI).

Flow: message -> parse_intent() -> {"intent", "entity"} -> answer() -> InventoryService
"""
import re

from app.service import InventoryService

service = InventoryService()

INTENT_STOCK = "stock_query"
INTENT_LOCATION = "location_query"
INTENT_CATEGORY = "category_query"
INTENT_UNKNOWN = "unknown"

# Order matters: category first, then location, then stock.
INTENT_PATTERNS = [
    (INTENT_CATEGORY, [
        r"(?:list|show|display)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?:items|parts)\s+(?:in|under|from|of)\s+(?P<entity>.+)",
        r"(?:list|show|display)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?P<entity>.+?)\s+(?:items|parts)$",
        r"(?:list|show|display)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?P<entity>.+)",
        r"what\s+(?:items|parts)\s+(?:do\s+we\s+have\s+)?(?:in|under)\s+(?P<entity>.+)",
        r"what\s+do\s+we\s+have\s+in\s+(?P<entity>.+)",
        r"(?:items|parts)\s+in\s+(?P<entity>.+)",
    ]),
    (INTENT_LOCATION, [
        r"where\s+(?:is|are|can\s+i\s+find|do\s+we\s+keep|do\s+we\s+store|would\s+i\s+find)\s+(?P<entity>.+)",
        r"(?:location|position)\s+of\s+(?P<entity>.+)",
        r"(?P<entity>.+?)\s+location$",
    ]),
    (INTENT_STOCK, [
        r"how\s+many\s+(?P<entity>.+)",
        r"how\s+much\s+(?P<entity>.+)",
        r"(?:quantity|count|stock|amount)\s+of\s+(?P<entity>.+)",
        r"(?:do\s+we\s+have|are\s+there)\s+(?:any\s+)?(?P<entity>.+)",
        r"is\s+(?:there\s+)?(?:any\s+)?(?P<entity>.+?)\s+in\s+stock",
    ]),
]

LEADING_WORDS = {"the", "a", "an", "any", "of", "our", "some", "all"}
TRAILING_WORDS = {
    "do", "does", "did", "we", "you", "have", "has", "got", "left", "remaining",
    "remain", "in", "stock", "available", "currently", "now", "please", "are",
    "is", "there", "the", "inventory", "stored", "kept", "located", "at",
    "category", "categories", "for", "still", "on", "hand",
}
PRONOUNS = {"it", "they", "them", "those", "that", "this", "these", "one", "ones"}
GENERIC_CATEGORY_WORDS = {"", "all", "everything", "items", "parts", "category", "categories"}


def normalize(message: str) -> str:
    text = message.lower().strip()
    text = re.sub(r"\bwhere's\b", "where is", text)
    text = re.sub(r"\bwhat's\b", "what is", text)
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    return " ".join(text.split())


def clean_entity(raw: str) -> str:
    """Strip filler words from both ends, e.g. 'brake pads do we have left' -> 'brake pads'."""
    words = raw.split()
    while words and words[0] in LEADING_WORDS:
        words.pop(0)
    while words and words[-1] in TRAILING_WORDS:
        words.pop()
    return " ".join(words)


def parse_intent(message: str) -> dict:
    """Return {"intent": ..., "entity": ...}. entity is '' when none was given."""
    text = normalize(message)
    for intent, patterns in INTENT_PATTERNS:
        for pattern in patterns:
            m = re.fullmatch(pattern, text)
            if m:
                entity = clean_entity(m.group("entity"))
                if intent == INTENT_CATEGORY and entity in GENERIC_CATEGORY_WORDS:
                    entity = ""
                if intent != INTENT_CATEGORY and entity in PRONOUNS:
                    entity = ""  # Phase 1 has no memory, so "it"/"they" means the item is missing
                if intent == INTENT_STOCK:
                    # "how many items in electronics" is really a category question
                    cat = re.fullmatch(r"(?:items|parts)\s+in\s+(?P<c>.+)", entity)
                    if cat:
                        return {"intent": INTENT_CATEGORY, "entity": clean_entity(cat.group("c"))}
                return {"intent": intent, "entity": entity}
    return {"intent": INTENT_UNKNOWN, "entity": ""}


HELP_MESSAGE = (
    "I can answer questions like:\n"
    "- How many brake pads do we have?\n"
    "- Where is the ECU?\n"
    "- List all items in Electronics."
)


def _did_you_mean(names: list[str]) -> str:
    if len(names) == 1:
        return f"Did you mean {names[0]}?"
    return "Did you mean " + ", ".join(names[:-1]) + " or " + names[-1] + "?"


def _answer_part(intent: str, entity: str) -> str:
    if not entity:
        return "Which item would you like to check?"

    result = service.find_part(entity)
    status = result["status"]

    if status == "not_found":
        return f"I couldn't find '{entity}' in the inventory."
    if status == "ambiguous":
        return f"'{entity}' matches more than one part. " + _did_you_mean(result["suggestions"])

    part = result["part"]
    note = f"Showing results for {part['name']}. " if status == "corrected" else ""
    if intent == INTENT_STOCK:
        return f"{note}{part['name']}: {part['quantity']} in stock (stored at {part['location']})."
    return f"{note}Location of {part['name']}: {part['location']}."


def _answer_category(entity: str) -> str:
    categories = ", ".join(service.get_categories())
    if not entity:
        return f"Which category would you like to see? Available categories: {categories}."

    result = service.find_category(entity)
    status = result["status"]

    if status == "not_found":
        return f"I couldn't find a category called '{entity}'. Available categories: {categories}."
    if status == "ambiguous":
        return f"'{entity}' matches more than one category. " + _did_you_mean(result["suggestions"])

    category = result["category"]
    note = f"Showing results for {category}.\n" if status == "corrected" else ""
    parts = service.get_by_category(category)
    lines = [f"- {p['name']}: {p['quantity']} ({p['location']})" for p in parts]
    return f"{note}Items in {category}:\n" + "\n".join(lines)


def answer(message: str) -> str:
    """Public entry point used by Streamlit."""
    if not message or not message.strip():
        return "Please type a question."

    parsed = parse_intent(message)
    intent, entity = parsed["intent"], parsed["entity"]

    if intent == INTENT_CATEGORY:
        return _answer_category(entity)
    if intent in (INTENT_STOCK, INTENT_LOCATION):
        return _answer_part(intent, entity)
    return HELP_MESSAGE


if __name__ == "__main__":
    print("CURT Inventory Assistant (Phase 1). Type 'quit' to exit.")
    while True:
        question = input("> ")
        if question.strip().lower() in {"quit", "exit"}:
            break
        print(answer(question))