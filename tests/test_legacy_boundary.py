"""边界守卫：AIDE 不得触达旧包里会调用 LLM 的模块。

为什么需要这条守卫：
2026-10-30 19:15 的 E4 改写是由旧包的 `rewrite.py` 执行的，它读的是旧项目的
`OPENAI_*` 配置，AIDE 的轨迹里没有 model / usage / cost 的任何记录 —— 模型调了、
钱花了、账上什么都没有。AIDE 只要 import 到那几个模块，同样的"无声调用"就会重演。

判据不是维护一张名单（名单会过期），而是从旧包**现场算出**「直接或间接 import
openai 的模块集合」，再断言 AIDE 一个都没碰。上游哪天新增一个调模型的模块，
这里的集合会自动变大。

见 docs/adr/0001：复用旧包的检索代码，不碰它的模型调用。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    REPO_ROOT / "knowledge",
    REPO_ROOT / "src" / "aide",
    REPO_ROOT / "tools",
)


def _legacy_targets(tree: ast.AST) -> set[str]:
    """从 `from agentbook_rag.qa import x` 这类语句里取出被引用的模块名。

    覆盖四种写法：`from .qa import`、`from agentbook_rag.qa import`、
    `from agentbook_rag import qa`、`import agentbook_rag.qa`。
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:  # 相对导入：from .qa import x
                found.add(module.split(".")[0])
            elif module == "agentbook_rag":  # from agentbook_rag import qa
                found.update(alias.name for alias in node.names)
            elif module.startswith("agentbook_rag."):
                found.add(module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agentbook_rag."):
                    found.add(alias.name.split(".")[1])
    return {name for name in found if name}


def _imports_openai(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "openai" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "openai":
                return True
    return False


def _llm_module_closure(package: Path) -> set[str]:
    """旧包里直接或间接 import openai 的模块名集合（不动旧包一个字节）。"""
    direct: set[str] = set()
    edges: dict[str, set[str]] = {}
    for path in sorted((package / "agentbook_rag").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        edges[path.stem] = _legacy_targets(tree)
        if _imports_openai(tree):
            direct.add(path.stem)

    # 传递闭包：import 了直接调用者，同样算触达模型
    changed = True
    while changed:
        changed = False
        for name, targets in edges.items():
            if name not in direct and targets & direct:
                direct.add(name)
                changed = True
    return direct


@pytest.fixture(scope="module")
def legacy_llm_modules() -> set[str]:
    try:
        from knowledge.loader import RagHomeMissing, rag_package_root
    except ImportError:  # 非包上下文（conftest 已把 knowledge/ 放进 sys.path）
        from loader import RagHomeMissing, rag_package_root  # type: ignore[no-redef]

    try:
        package = rag_package_root()
    except RagHomeMissing as exc:
        pytest.skip(f"旧包不可用，边界守卫无从计算：{exc}")

    modules = _llm_module_closure(package)
    # 空集合守卫：集合为空时下面那条"一个都没碰"恒真，守门等于不存在
    assert modules, (
        f"在 {package} 里没有找到任何 import openai 的模块：要么路径变了，"
        "要么守卫扫错了地方，此时它拦不住任何东西"
    )
    return modules


def test_aide_never_imports_llm_calling_legacy_modules(
    legacy_llm_modules: set[str],
) -> None:
    """AIDE 一行代码都不许 import 旧包里会调模型的模块。"""
    scanned = 0
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        files = sorted(root.rglob("*.py"))
        scanned += len(files)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hit = _legacy_targets(tree) & legacy_llm_modules
            if hit:
                offenders.append(f"{path.relative_to(REPO_ROOT)} → {sorted(hit)}")

    # 扫到 0 个文件时下面的断言恒真（全称断言在空集上永远成立）
    assert scanned > 0, f"三个扫描目录里一个 .py 都没找到：{SCAN_ROOTS}"

    assert not offenders, (
        "以下文件引入了会调用 LLM 的旧模块（它们的调用不走 llm/client.py，"
        "不记 model / usage / cost）：\n  " + "\n  ".join(offenders)
    )


def test_legacy_boundary_scanner_can_fail(legacy_llm_modules: set[str]) -> None:
    """守卫本身要能被证伪：否则它只是一句恒真的宣言。"""
    target = sorted(legacy_llm_modules)[0]
    samples = (
        f"from agentbook_rag.{target} import anything\n",
        f"from agentbook_rag import {target}\n",
        f"import agentbook_rag.{target}\n",
    )
    for source in samples:
        assert _legacy_targets(ast.parse(source)) & legacy_llm_modules == {target}, source
