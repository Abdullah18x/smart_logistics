"""Contracts for the events Inventory publishes on ``inventory.events``."""

from inventory.models import StockMovementType
from sl_platform.contracts import (
    array,
    enum,
    event,
    extend,
    integer,
    nullable,
    obj,
    register,
    string,
    uuid,
)

LINE = obj({"sku_id": uuid(), "quantity": integer(minimum=1)}, required=("sku_id", "quantity"))

HOLD = event(
    "stock hold",
    {
        "shipment_id": uuid(),
        "warehouse_id": uuid(),
        "lines": array(
            obj(
                {"sku_id": uuid(), "quantity": integer(minimum=1)}, required=("sku_id", "quantity")
            ),
            minItems=1,
        ),
    },
    required=("shipment_id", "warehouse_id", "lines"),
    description="Stock held, committed or released for one shipment.",
)

CONTRACTS = {
    "stock.reserved": extend(HOLD, "stock.reserved"),
    "stock.committed": extend(HOLD, "stock.committed"),
    "stock.released": extend(HOLD, "stock.released", reason=nullable(string())),
    "stock.adjusted": event(
        "stock.adjusted",
        {
            "inventory_item_id": uuid(),
            "warehouse_id": uuid(),
            "sku_id": uuid(),
            "type": enum(*(t.value for t in StockMovementType)),
            "quantity_delta": integer(),
            "on_hand_qty": integer(minimum=0),
        },
        required=("inventory_item_id", "warehouse_id", "sku_id", "type", "quantity_delta"),
        description="A manual stock movement: receipt, count correction, write-off.",
    ),
}

# Known to this process from import: add_event checks every payload against them.
register(CONTRACTS)
