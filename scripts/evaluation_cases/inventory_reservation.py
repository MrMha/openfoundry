from dataclasses import is_dataclass

import pytest
from inventory.models import Item
from inventory.store import Inventory


def test_item_dataclass_and_initial_stock() -> None:
    inv = Inventory()
    assert is_dataclass(Item)
    assert inv.stock("SKU-1") == 0


def test_add_accumulates_and_returns_item() -> None:
    inv = Inventory()
    assert inv.add("SKU-1", 5) == Item(sku="SKU-1", quantity=5)
    assert inv.add("SKU-1", 3) == Item(sku="SKU-1", quantity=8)
    assert inv.stock("SKU-1") == 8


@pytest.mark.parametrize(
    "sku,qty", [("", 1), ("   ", 2), ("SKU-1", 0), ("SKU-1", -4), ("SKU-1", True)]
)
def test_add_rejects_invalid_inputs(sku: str, qty: int) -> None:
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.add(sku, qty)


def test_reserve_deducts_and_is_idempotent_on_same_args() -> None:
    inv = Inventory()
    inv.add("SKU-1", 10)
    first = inv.reserve("SKU-1", 4, "req-1")
    second = inv.reserve("SKU-1", 4, "req-1")
    assert first == Item(sku="SKU-1", quantity=6)
    assert second == Item(sku="SKU-1", quantity=6)
    assert inv.stock("SKU-1") == 6


def test_reserve_same_id_different_args_raises_and_preserves_stock() -> None:
    inv = Inventory()
    inv.add("SKU-1", 10)
    inv.add("SKU-2", 10)
    inv.reserve("SKU-1", 4, "req-1")
    with pytest.raises(ValueError):
        inv.reserve("SKU-1", 2, "req-1")
    with pytest.raises(ValueError):
        inv.reserve("SKU-2", 4, "req-1")
    assert inv.stock("SKU-1") == 6
    assert inv.stock("SKU-2") == 10


def test_retry_after_other_operations_returns_current_stock() -> None:
    inv = Inventory()
    inv.add("SKU-1", 10)
    inv.reserve("SKU-1", 3, "r1")
    inv.add("SKU-1", 5)
    inv.reserve("SKU-1", 2, "r2")
    assert inv.reserve("SKU-1", 3, "r1") == Item(sku="SKU-1", quantity=10)
    assert inv.stock("SKU-1") == 10


def test_failed_oversold_reservation_does_not_consume_request_id() -> None:
    inv = Inventory()
    inv.add("SKU-1", 5)
    with pytest.raises(ValueError):
        inv.reserve("SKU-1", 6, "req-retry")
    assert inv.stock("SKU-1") == 5
    res = inv.reserve("SKU-1", 5, "req-retry")
    assert res == Item(sku="SKU-1", quantity=0)


@pytest.mark.parametrize(
    "sku,qty,req_id",
    [
        ("", 1, "r1"),
        ("SKU-1", 0, "r2"),
        ("SKU-1", -1, "r3"),
        ("SKU-1", 1, "  "),
        ("SKU-1", False, "r4"),
    ],
)
def test_failed_invalid_reservation_does_not_consume_request_id(
    sku: str, qty: int, req_id: str
) -> None:
    inv = Inventory()
    inv.add("SKU-1", 5)
    with pytest.raises(ValueError):
        inv.reserve(sku, qty, req_id)
    if req_id.strip():
        assert inv.reserve("SKU-1", 2, req_id) == Item(sku="SKU-1", quantity=3)
