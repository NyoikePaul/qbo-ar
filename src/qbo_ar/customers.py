"""Customer lookups."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import Customer
from .query import Query, paginate

CHUNK = 50


def fetch_customers(client: Any, ids: Iterable[str]) -> dict[str, Customer]:
    unique = sorted({i for i in ids if i})
    found: dict[str, Customer] = {}
    for start in range(0, len(unique), CHUNK):
        q = Query("Customer").where("Id", "IN", unique[start : start + CHUNK])
        for row in paginate(client, q):
            customer = Customer.from_api(row)
            found[customer.id] = customer
    return found
