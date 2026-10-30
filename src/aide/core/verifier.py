"""完成判定：`verify(artifact, baseline)`。

关键设计（见 ADR 0004 同一手法）：**基线是参数，不是去任务注册表里查**。
这样 G 组在 E 组（任务注册）之前就能自足地测，不会形成依赖环。

另一条硬要求（loop-engineering:18）：验证不可靠时，循环转得越快，
只是把劣质产出更快地标记为完成。所以基线必须能**拒绝**明显不合格的产物，
而不只是"看起来通过了"。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: 失败原因必须可定位：说清哪一项不满足、在哪一行
Failure = str
Predicate = Callable[[Any], Failure | None]


@dataclass(frozen=True)
class Baseline:
    """一组验收谓词。谓词返回 None 表示通过，返回字符串表示失败原因。"""

    name: str
    predicates: tuple[Predicate, ...]

    def check(self, artifact: Any) -> tuple[bool, tuple[Failure, ...]]:
        failures: list[Failure] = []
        for predicate in self.predicates:
            outcome = predicate(artifact)
            if outcome:
                failures.append(outcome)
        return (not failures, tuple(failures))


@dataclass(frozen=True)
class Verification:
    ok: bool
    baseline: str
    failures: tuple[Failure, ...]

    def reason(self) -> str:
        return "通过" if self.ok else "；".join(self.failures)


def _text_of(artifact: Any) -> str:
    if isinstance(artifact, str):
        return artifact
    text = getattr(artifact, "text", None)
    return text if isinstance(text, str) else ""


def non_empty(artifact: Any) -> Failure | None:
    if not _text_of(artifact).strip():
        return "内容为空：没有可交付的文本"
    return None


def parses_as_python(artifact: Any) -> Failure | None:
    """语法必须可解析；错误信息带行号，便于模型定位。"""
    text = _text_of(artifact)
    if not text.strip():
        return None  # 空内容由 non_empty 负责，避免同一问题报两次
    try:
        ast.parse(text)
    except SyntaxError as error:
        return f"语法错误：第 {error.lineno} 行 {error.msg}"
    return None


def has_executable_statement(artifact: Any) -> Failure | None:
    """挡掉"只改注释/只写文档字符串"这类假修复。"""
    text = _text_of(artifact)
    if not text.strip():
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None  # 语法错误已由上一个谓词报出
    meaningful = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    if not meaningful:
        return "只有注释或字符串字面量，没有任何可执行语句（看起来是空改动）"
    return None


#: Python 文件的最小验收基线：非空 + 可解析 + 有实际语句
PYTHON_FILE_BASELINE = Baseline(
    name="python-file",
    predicates=(non_empty, parses_as_python, has_executable_statement),
)


def verify(artifact: Any, baseline: Baseline) -> Verification:
    """按基线判定产物。纯函数：不读文件系统、不调模型。"""
    ok, failures = baseline.check(artifact)
    return Verification(ok=ok, baseline=baseline.name, failures=failures)


def verify_all(artifact: Any, baselines: Sequence[Baseline]) -> Verification:
    """多基线合并判定：任一不过即整体不过，失败原因全部保留。

    **空基线不得默认通过**：没有验收标准时，"通过"这个结论没有依据。
    这是"空实现能过"的变体——循环体不执行 → failures 为空 → ok=True，
    与 `verify(x, None)` 走 VERIFICATION_UNAVAILABLE 的语义相反。
    """
    if not baselines:
        return Verification(
            ok=False,
            baseline="<none>",
            failures=("没有可用的验收基线：无法判定完成",),
        )

    failures: tuple[Failure, ...] = ()
    for baseline in baselines:
        result = verify(artifact, baseline)
        if not result.ok:
            failures += tuple(f"{result.baseline}: {item}" for item in result.failures)
    return Verification(
        ok=not failures,
        baseline="+".join(item.name for item in baselines),
        failures=failures,
    )
