"""完成判定：`verify(artifact, baseline)`。

关键设计（见 ADR 0004 同一手法）：**基线是参数，不是去任务注册表里查**。
这样 G 组在 E 组（任务注册）之前就能自足地测，不会形成依赖环。

另一条硬要求（loop-engineering:18）：验证不可靠时，循环转得越快，
只是把劣质产出更快地标记为完成。所以基线必须能**拒绝**明显不合格的产物，
而不只是"看起来通过了"。

抽取逻辑已拆到 `code_extract.py`；本模块只做判定。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .code_extract import extract_candidates, extract_code, text_of

#: 失败原因必须可定位：说清哪一项不满足、在哪一行
Failure = str
Predicate = Callable[[Any], Failure | None]
Extractor = Callable[[Any], list[str]]

_text_of = text_of  # 旧内部名，保持兼容


@dataclass(frozen=True)
class Baseline:
    """一组验收谓词 + 可选的候选抽取器。

    谓词返回 None 表示通过，返回字符串表示失败原因。
    `extractor` 非空时，`verify()` 会逐个候选试，**同一候选必须过完所有谓词**。
    """

    name: str
    predicates: tuple[Predicate, ...]
    extractor: Extractor | None = None

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
    """产物里必须真的"做了点什么"。

    判据（写死，不要放宽）：
    - 非 `Expr` 的语句节点算数：FunctionDef / Assign / Import / If / Try / Raise / Pass 等；
    - `Expr` 只有在 value 是 `Call` 时算数（`print(1)` 有副作用）；
    - **其余裸表达式一律不算**：`Expr(Name)`、`Expr(Constant)`、`Expr(BinOp)`、`Expr(List)`。
      所以 `"好"`、`"x"`、`"1 + 1"`、`"[1, 2]"` 都不通过——
      模型回一句「好的」不得被判成完成。
    """
    text = _text_of(artifact)
    if not text.strip():
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None  # 语法错误已由上一个谓词报出

    for node in tree.body:
        if not isinstance(node, ast.Expr):
            return None
        if isinstance(node.value, ast.Call):
            return None
    return "没有可执行语句：注释与裸表达式（标识符/常量/运算）都不算产出，至少要有语句或函数调用"


#: Python 文件的最小验收基线：候选序列 → 非空 + 可解析 + 有实际语句
PYTHON_FILE_BASELINE = Baseline(
    name="python-file",
    predicates=(non_empty, parses_as_python, has_executable_statement),
    extractor=extract_candidates,
)


def verify(artifact: Any, baseline: Baseline) -> Verification:
    """按基线判定产物。纯函数：不读文件系统、不调模型。

    **候选之间不合并**：一个候选必须自己过完所有谓词。曾允许跨候选合并，
    导致「裸标识符围栏 + 语法错误围栏」被判通过——`parses` 命中一块、
    可执行语句谓词在另一块上因 SyntaxError 返回 None（免检），拼起来就"全过"。

    全不通过时，用**失败谓词最少**的候选报错（并列取靠前），
    这样报错指向最有希望的那份，且行号仍是原文行号（抽取时用空行占位）。
    """
    candidates = baseline.extractor(artifact) if baseline.extractor else [artifact]
    best: tuple[tuple[Failure, ...], str] | None = None
    for candidate in candidates:
        ok, failures = baseline.check(candidate)
        if ok:
            return Verification(ok=True, baseline=baseline.name, failures=())
        if best is None or len(failures) < len(best[0]):
            best = (failures, candidate)
    assert best is not None  # extractor 至少返回一个候选（含原文）
    return Verification(ok=False, baseline=baseline.name, failures=best[0])


def verify_all(artifact: Any, baselines: Sequence[Baseline]) -> Verification:
    """多基线合并判定：任一不过即整体不过，失败原因全部保留。

    **空基线不得默认通过**：没有验收标准时，"通过"这个结论没有依据。
    这是"空实现能过"的变体——循环体不执行 → failures 为空 → ok=True，
    与 `verify(x, None)` 走 `VERIFICATION_UNAVAILABLE` 的语义相反。
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
