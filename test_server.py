"""
Quick smoke test for the CURT Inventory Assistant FastAPI backend.

Run the server first:
    uvicorn app.main:app --reload

Then, in a second terminal:
    python test_server.py
"""

import uuid

import requests

BASE_URL = "http://localhost:8000"


def check_health():
    print("=== /health ===")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        print(f"Status: {r.status_code}")
        print(f"Body:   {r.json()}")
    except requests.exceptions.ConnectionError:
        print("Could not connect. Is `uvicorn app.main:app --reload` running?")
        raise SystemExit(1)
    print()


def check_inventory():
    print("=== GET /inventory ===")
    r = requests.get(f"{BASE_URL}/inventory", timeout=5)
    print(f"Status: {r.status_code}")
    data = r.json()
    print(f"Items returned: {len(data) if isinstance(data, list) else 'N/A'}")
    if isinstance(data, list) and data:
        print(f"Sample item: {data[0]}")
    print()


def check_chat():
    print("=== POST /chat ===")
    session_id = str(uuid.uuid4())

    questions = [
        "How many brake pads do we have?",
        "Where are they stored?",  # follow-up, tests memory
        "List all items in Electronics",
        "How many flux capacitors do we have?",  # nonexistent item edge case
    ]

    for q in questions:
        print(f"--- Q: {q}")
        r = requests.post(
            f"{BASE_URL}/chat",
            json={"session_id": session_id, "message": q},
            timeout=30,
        )
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"Response:   {data.get('response')}")
            print(f"Tool calls: {data.get('tool_calls')}")
        else:
            print(f"Error body: {r.text}")
        print()


if __name__ == "__main__":
    check_health()
    check_inventory()
    check_chat()
    print("Done.")