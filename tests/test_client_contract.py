"""M1b 守卫：provider 客户端不得自带重试。

职责边界见 ADR 0009。`src/aide/llm/` 尚未实现时本条 skip 并写明原因——
**不允许**在没有 client 的情况下默默通过：那条路会变成"守门从未生效"。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LLM_DIR = REPO_ROOT / "src" / "aide" / "llm"
#: 用"有没有 .py 文件"判定，而不是"目录在不在"——
#: 空目录会让守门扫到 0 个文件然后显示绿，这正是"守门从未生效"的形态
CLIENT_FILES = sorted(LLM_DIR.rglob("*.py")) if LLM_DIR.is_dir() else []

pytestmark = pytest.mark.skipif(
    not CLIENT_FILES,
    reason="src/aide/llm 下没有 .py（M1b 未开工）；实现后本守卫自动生效",
)

RETRY_HINTS = {"attempt", "retry", "retries", "backoff"}


@pytest.mark.unit
def test_client_has_no_retry_loop() -> None:
    """client 里出现重试循环即红：重试归 recovery.py（ADR 0009）。"""
    offenders: list[str] = []
    for path in CLIENT_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                source = ast.dump(node)
                if any(hint in source.lower() for hint in RETRY_HINTS):
                    offenders.append(f"{path.name}: 重试循环")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "sleep":
                    offenders.append(f"{path.name}: 客户端里 sleep（退避属 recovery）")
    assert not offenders, "客户端越界实现重试：" + "；".join(offenders)
