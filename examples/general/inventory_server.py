"""A read-only MCP inventory service. Run with a Python environment containing mcp."""

from __future__ import annotations

import csv
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("flora-inventory-example")
INVENTORY = Path(__file__).with_name("inventory.csv")


def records():
    with INVENTORY.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


@server.tool()
def lookup_item(sku: str) -> dict:
    """Find a product by exact SKU in the configured local inventory."""
    for item in records():
        if item["sku"] == sku:
            return {"found": True, "item": item}
    return {"found": False, "sku": sku}


@server.tool()
def list_stock(limit: int = 20) -> dict:
    """List the first inventory records; limit must be 1 through 100."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    rows = records()
    return {"items": rows[:limit], "total": len(rows), "truncated": len(rows) > limit}


if __name__ == "__main__":
    server.run(transport="stdio")
