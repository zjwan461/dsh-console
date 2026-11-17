from __future__ import annotations

from typing import Any

from fastapi import Query


class Page:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def paginated(items: list[Any], total: int, page: Page) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
    }
