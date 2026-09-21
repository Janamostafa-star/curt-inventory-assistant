import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

# Anchor everything to the project folder, not to wherever the command is run from.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _resolve_db_path() -> str:
    path = Path(os.getenv("DB_PATH", "curt_inventory.db"))
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path)


DB_PATH = _resolve_db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    category TEXT NOT NULL,
    location TEXT NOT NULL
);
"""

# (name, quantity, category, location)
SEED_PARTS = [
    ("Brake Pads", 12, "Braking", "Mechanical Workshop"),
    ("Brake Disc", 7, "Braking", "Mechanical Workshop"),
    ("Brake Caliper", 4, "Braking", "Mechanical Workshop"),
    ("ECU", 2, "Electronics", "Electronics Cabinet"),
    ("Battery Pack", 4, "Electronics", "Battery Shelf"),
    ("Accelerator Sensor", 5, "Electronics", "Sensor Cabinet"),
    ("Data Logger", 2, "Electronics", "Electronics Cabinet"),
    ("Wiring Harness", 3, "Electronics", "Electronics Cabinet"),
    ("Tire Set", 6, "Suspension", "Tire Rack"),
    ("Suspension Arm", 10, "Suspension", "Rack C"),
    ("Damper", 8, "Suspension", "Rack C"),
    ("Wheel Hub", 8, "Drivetrain", "Workshop B"),
    ("Chain Sprocket", 5, "Drivetrain", "Workshop B"),
    ("Steering Wheel", 3, "Chassis", "Storage Room A"),
    ("Seat Harness", 6, "Chassis", "Storage Room A"),
]


def get_connection() -> sqlite3.Connection:
    """Open a new connection. Rows behave like dicts (row["name"])."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the parts table if it doesn't exist."""
    conn = get_connection()
    try:
        conn.execute(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def seed_db() -> None:
    """Insert the starter parts, but only if the table is empty."""
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT INTO parts (name, quantity, category, location) VALUES (?, ?, ?, ?)",
                SEED_PARTS,
            )
            conn.commit()
    finally:
        conn.close()


def setup_database() -> None:
    init_db()
    seed_db()


if __name__ == "__main__":
    setup_database()
    print(f"Database ready at {DB_PATH}")