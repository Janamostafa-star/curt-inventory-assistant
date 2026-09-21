import pytest

from app import db
from app.service import InventoryService


@pytest.fixture
def service(tmp_path, monkeypatch):
    # use a throwaway database so tests never touch the real one
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.setup_database()
    return InventoryService()


def test_get_part_is_case_insensitive(service):
    assert service.get_part("brake pads")["quantity"] == 12


def test_get_part_unknown_returns_none(service):
    assert service.get_part("turbocharger") is None


def test_get_by_category(service):
    assert len(service.get_by_category("Electronics")) == 5


def test_update_quantity(service):
    assert service.update_quantity("Brake Pads", -2)["quantity"] == 10


def test_update_quantity_cannot_go_negative(service):
    with pytest.raises(ValueError):
        service.update_quantity("ECU", -99)


def test_find_part_misspelling(service):
    result = service.find_part("break pad")
    assert result["status"] == "corrected"
    assert result["part"]["name"] == "Brake Pads"


def test_find_part_partial(service):
    result = service.find_part("pads")
    assert result["status"] == "partial"
    assert result["part"]["name"] == "Brake Pads"


def test_find_part_ambiguous(service):
    result = service.find_part("brake")
    assert result["status"] == "ambiguous"
    assert len(result["suggestions"]) == 3


def test_find_part_not_found(service):
    assert service.find_part("turbocharger")["status"] == "not_found"


def test_find_category_fuzzy(service):
    assert service.find_category("electronic")["category"] == "Electronics"