"""从模型回复里抽取候选代码片段。

拆出本模块的原因：`verifier.py` 已到 299 行（上限 300），且抽取逻辑
（围栏 / 散文剥离 / 行号占位）与判定逻辑（谓词 / 基线）是两个关注点。

**抽取只负责"找到可能的代码"，不负责判定通过**——判断由 `verifier` 的谓词做。
"""

from __future__ import annotations

import ast
import re
import textwrap
from typing import Any

MAX_STRIP_LINES = 10

_FENCE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)

#: ASCII 禁止符号——**故意不含全角**：中文散文里的「：，（）」不能挡剥离，
#: 否则"好的，代码如下："永远剥不掉，抽取对中文场景直接失效。
_CODE_SYMBOLS = frozenset("=:()[]{}")

#: 首词是这些关键字说明该行是代码，不可剥
_CODE_KEYWORDS = frozenset(
    """def class import from return if elif else for while with try except finally
    raise assert lambda yield async await pass break continue global nonlocal del""".split()
)


def text_of(artifact: Any) -> str:
    """取文本。字符串直用；对象取 `.text` 属性（兼容旧 artifact 形态）。"""
    if isinstance(artifact, str):
        return artifact
    text = getattr(artifact, "text", None)
    return text if isinstance(text, str) else ""


def parses(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


def is_prose_line(line: str) -> bool:
    """这一行是否算散文（可从边缘剥掉）。

    同时满足才算：空白行；不以空白开头；不含 ASCII 代码符号；首词不是关键字。
    判据写死：**语法错误的代码行绝不可剥**，否则坏产物会被旁边一行合法代码救活。
    """
    stripped = line.rstrip("\n")
    if not stripped.strip():
        return True
    if stripped[:1].isspace():
        return False
    if any(symbol in stripped for symbol in _CODE_SYMBOLS):
        return False
    first = stripped.split(None, 1)[0] if stripped.split() else ""
    return first not in _CODE_KEYWORDS


def _leading_prose(lines: list[str], limit: int = MAX_STRIP_LINES) -> int:
    count = 0
    for line in lines:
        if count >= limit or not is_prose_line(line):
            break
        count += 1
    return count


def _trailing_prose(lines: list[str], limit: int = MAX_STRIP_LINES) -> int:
    count = 0
    for line in reversed(lines):
        if count >= limit or not is_prose_line(line):
            break
        count += 1
    return count


def _strip_head(text: str, limit: int = MAX_STRIP_LINES) -> str | None:
    """剥掉顶部连续的散文行。

    **用等量空行占位**（不是直接删除），这样即使候选解析失败，
    `ast` 报的行号仍是原文行号。遇到第一个非散文行即停，不跳过。
    """
    lines = text.splitlines(keepends=True)
    count = _leading_prose(lines, limit)
    if count == 0:
        return None
    return "\n" * count + "".join(lines[count:])


def _strip_tail(text: str, limit: int = MAX_STRIP_LINES) -> str | None:
    """剥掉尾部连续的散文行（删末尾不影响前面的行号）。"""
    lines = text.splitlines(keepends=True)
    count = _trailing_prose(lines, limit)
    if count == 0:
        return None
    return "".join(lines[: len(lines) - count])


def _strip_both(text: str, limit: int = MAX_STRIP_LINES) -> str | None:
    """先剥头、在剥头的结果上再剥尾。专治双侧散文。"""
    lines = text.splitlines(keepends=True)
    head = _leading_prose(lines, limit)
    if head == 0:
        return None
    body = lines[head:]
    tail = _trailing_prose(body, limit)
    if tail == 0:
        return None
    return "\n" * head + "".join(body[: len(body) - tail])


def extract_candidates(artifact: Any) -> list[str]:
    """产出候选代码片段序列（按可信度排序，去重）。纯函数。

    覆盖形态：裸代码、围栏（含多围栏/缩进）、前置说明、尾随说明、双侧说明。
    候选之间**不合并**：调用方必须让同一个候选独自通过全部谓词。
    """
    text = text_of(artifact)
    if not text.strip():
        return [text]
    if parses(text):
        return [text]

    ordered: list[str] = []

    def add(candidate: str) -> None:
        if candidate.strip() and candidate not in ordered:
            ordered.append(candidate)

    for match in _FENCE_RE.finditer(text):
        # 围栏内容同样按原文行号偏移：前面有几行就补几个空行
        offset = text[: match.start(2)].count("\n")
        add("\n" * offset + textwrap.dedent(match.group(2)))

    head = _strip_head(text)
    if head is not None:
        add(head)

    tail = _strip_tail(text)
    if tail is not None:
        add(tail)

    combo = _strip_both(text)
    if combo is not None:
        add(combo)

    if not ordered:
        add(text)  # 全军覆没：返回原文，让 parses 照常报「第 N 行」
    return ordered


def extract_code(artifact: Any) -> str:
    """兼容旧调用：返回最可信的候选。"""
    return extract_candidates(artifact)[0]
