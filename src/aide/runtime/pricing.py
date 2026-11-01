"""价目表：配置驱动 + 版本号（ADR 0010）。

只做一件事：给定 `(price_version, model, usage)` 算出金额。
金额算不出来时返回 `None`，**绝不猜**——猜错的价格比没有价格更糟。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PricingError(ValueError):
    """价目表格式非法。"""


@dataclass(frozen=True)
class PriceBook:
    version: str
    #: model -> (输入每千 token 单价, 输出每千 token 单价)
    models: dict[str, tuple[float, float]]

    def cost(self, model: str, usage: dict[str, int]) -> float | None:
        """返回美元金额；模型不在表内或缺 usage 时返回 None。"""
        if model not in self.models:
            return None
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if prompt is None or completion is None:
            return None
        price_in, price_out = self.models[model]
        return round(prompt / 1000 * price_in + completion / 1000 * price_out, 6)


def load_prices(path: Path) -> PriceBook:
    if not path.is_file():
        raise PricingError(f"价目表不存在：{path}")
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    version = raw.get("version")
    if not version:
        raise PricingError("价目表缺少 version：没有版本号就没法回溯历史成本")
    models: dict[str, tuple[float, float]] = {}
    for name, entry in (raw.get("models") or {}).items():
        try:
            models[name] = (float(entry["in_per_1k"]), float(entry["out_per_1k"]))
        except (KeyError, TypeError, ValueError) as error:
            raise PricingError(f"价目表条目非法：{name}（{error}）") from error
    return PriceBook(version=str(version), models=models)
