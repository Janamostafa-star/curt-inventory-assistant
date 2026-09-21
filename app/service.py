import difflib
import re

from app.db import get_connection

FUZZY_CUTOFF = 0.6   # minimum similarity for a fuzzy match
CLEAR_MARGIN = 0.1   # best fuzzy match must beat the runner-up by this much


# ---------- name matching helpers ----------

def _singular(word: str) -> str:
    """Very simple plural handling: 'pads' -> 'pad'."""
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _key(text: str) -> str:
    """Normalize text for comparison: lowercase, no punctuation, singular words."""
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(_singular(w) for w in text.split())


def _match(query: str, candidates: list[str]) -> tuple[str, list[str]]:
    """
    Match a user's text against a list of canonical names.
    Returns (status, names) where status is one of:
      exact      - same name (ignoring case/plural)
      partial    - query words all appear in exactly one name ("pads")
      corrected  - one clearly best fuzzy match ("break pad")
      ambiguous  - several plausible names, caller should ask the user
      not_found  - nothing close enough
    """
    q = _key(query)
    if not q:
        return "not_found", []

    name_by_key = {_key(c): c for c in candidates}

    # 1) exact
    if q in name_by_key:
        return "exact", [name_by_key[q]]

    # 2) partial: every word of the query appears in the name
    q_words = set(q.split())
    partial = [name for k, name in name_by_key.items() if q_words <= set(k.split())]
    if len(partial) == 1:
        return "partial", partial
    if len(partial) > 1:
        return "ambiguous", partial

    # 3) fuzzy
    close = difflib.get_close_matches(q, list(name_by_key), n=3, cutoff=FUZZY_CUTOFF)
    if not close:
        return "not_found", []
    scores = [difflib.SequenceMatcher(None, q, k).ratio() for k in close]
    if len(close) == 1 or scores[0] - scores[1] >= CLEAR_MARGIN:
        return "corrected", [name_by_key[close[0]]]
    return "ambiguous", [name_by_key[k] for k in close]


# ---------- the service ----------

class InventoryService:
    """The ONLY place in the project that runs SQL."""

    COLUMNS = "id, name, quantity, category, location"

    # --- small private helpers ---
    def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = get_connection()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def _fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._fetch_all(sql, params)
        return rows[0] if rows else None

    # --- required methods ---
    def get_part(self, name: str) -> dict | None:
        """Exact (case-insensitive) lookup. Returns None if not found."""
        return self._fetch_one(
            f"SELECT {self.COLUMNS} FROM parts WHERE LOWER(name) = LOWER(?)",
            (name.strip(),),
        )

    def part_exists(self, name: str) -> bool:
        return self.get_part(name) is not None

    def get_all_parts(self) -> list[dict]:
        return self._fetch_all(f"SELECT {self.COLUMNS} FROM parts ORDER BY category, name")

    def get_by_category(self, category: str) -> list[dict]:
        """Exact (case-insensitive) category lookup."""
        return self._fetch_all(
            f"SELECT {self.COLUMNS} FROM parts WHERE LOWER(category) = LOWER(?) ORDER BY name",
            (category.strip(),),
        )

    def update_quantity(self, name: str, delta: int) -> dict:
        """Add delta (can be negative) to a part's quantity. Never goes below zero."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id, quantity FROM parts WHERE LOWER(name) = LOWER(?)",
                (name.strip(),),
            ).fetchone()
            if row is None:
                raise KeyError(f"Part '{name}' does not exist")
            new_quantity = row["quantity"] + delta
            if new_quantity < 0:
                raise ValueError("Quantity cannot go below zero")
            conn.execute("UPDATE parts SET quantity = ? WHERE id = ?", (new_quantity, row["id"]))
            conn.commit()
        finally:
            conn.close()
        return self.get_part(name)

    # --- extra methods used by both phases ---
    def get_categories(self) -> list[str]:
        rows = self._fetch_all("SELECT DISTINCT category FROM parts ORDER BY category")
        return [r["category"] for r in rows]

    def find_part(self, text: str) -> dict:
        """
        Resolve messy user text to a real part.
        Returns {"status": ..., "part": dict | None, "suggestions": [names]}
        """
        names = [p["name"] for p in self.get_all_parts()]
        status, matched = _match(text, names)
        if status in ("exact", "partial", "corrected"):
            return {"status": status, "part": self.get_part(matched[0]), "suggestions": []}
        return {"status": status, "part": None, "suggestions": matched}

    def find_category(self, text: str) -> dict:
        """Same idea for categories. Returns {"status", "category", "suggestions"}."""
        status, matched = _match(text, self.get_categories())
        if status in ("exact", "partial", "corrected"):
            return {"status": status, "category": matched[0], "suggestions": []}
        return {"status": status, "category": None, "suggestions": matched}