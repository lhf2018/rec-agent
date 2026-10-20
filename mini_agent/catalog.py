from __future__ import annotations

import json
from pathlib import Path

from mini_agent.models import Merchant, Product, UserSnapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


class Catalog:
    def __init__(self) -> None:
        self.merchants: dict[str, Merchant] = {}
        self.users: dict[str, UserSnapshot] = {}
        self.tenant_id = "t_demo"
        self._load()

    def _load(self) -> None:
        catalog = json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))
        self.tenant_id = catalog.get("tenant_id", "t_demo")
        for raw in catalog["merchants"]:
            products = [Product(**p) for p in raw.get("products", [])]
            data = {k: v for k, v in raw.items() if k != "products"}
            m = Merchant(**data, products=products)
            self.merchants[m.merchant_id] = m

        users = json.loads((FIXTURES / "users.json").read_text(encoding="utf-8"))
        for raw in users["users"]:
            u = UserSnapshot(**raw)
            self.users[u.user_id] = u

    def get_user(self, user_id: str) -> UserSnapshot | None:
        return self.users.get(user_id)

    def get_merchant(self, merchant_id: str) -> Merchant | None:
        return self.merchants.get(merchant_id)

    def all_merchants(self) -> list[Merchant]:
        return list(self.merchants.values())

    def apply_not_interested(self, user_id: str, merchant_id: str) -> None:
        user = self.users.get(user_id)
        if not user:
            return
        if merchant_id not in user.hard.not_interested_merchants:
            user.hard.not_interested_merchants.append(merchant_id)


_catalog: Catalog | None = None


def get_catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog()
    return _catalog
