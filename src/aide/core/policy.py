"""执行策略：纯函数 `evaluate(action) -> Decision`。

M1 只判定，不执行任何真实操作；M2 接工具时执行器调用同一个函数，
一次实现两处受益。这是把「对系统的依赖」降为「对参数的依赖」。

**symlink 解析通过参数注入**：`realpath` 会读文件系统，若直接调用，
`evaluate()` 就不再是纯函数——测试要构造真实目录、受平台差异影响
（macOS 的 /tmp 是 /private/tmp 的符号链接，这类差异会直接咬人）。
注入后生产用真的、测试用假的，规则本身仍可确定性验证。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

Resolver = Callable[[str], str]


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEED_APPROVAL = "need_approval"


@dataclass(frozen=True)
class Action:
    """一次待判定的动作。只描述意图，不含执行结果。"""

    type: str  # read / write / exec
    path: str | None = None
    command: str | None = None
    read_first: bool = False  # 覆盖前是否已读过目标文件


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


#: 破坏性命令规则表（coding-agent-harness:45）。
#: 带编号是为了让「规则数 == 测试覆盖数」可核对——新增一条规则必须同时加一行测试。
DANGEROUS_RULES: tuple[tuple[str, re.Pattern[str], "Verdict"], ...] = (
    # 注意结尾用 (\s|$) 而不是 \b：`/` 是非单词字符，`\b` 在其后不成立
    ("52", re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|$)"), Verdict.DENY),
    # 只匹配裸 `*`：`rm -rf *.log` 是删产物，与删光/删根不是一个量级
    ("53", re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+\*(\s|$)"), Verdict.DENY),
    ("54", re.compile(r"\bdrop\s+database\b", re.IGNORECASE), Verdict.NEED_APPROVAL),
    ("55", re.compile(r"\btruncate\s+table\b", re.IGNORECASE), Verdict.NEED_APPROVAL),
    ("56", re.compile(r"\bgit\s+reset\s+--hard\b"), Verdict.NEED_APPROVAL),
)


def _inside(path: str, root: str) -> bool:
    """path 是否在 root 之内（按路径分量比较，避免 /a/bc 被当成 /a/b 的子路径）。"""
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:  # 不同盘符等
        return False


def evaluate(
    action: Action,
    *,
    sandbox_root: str,
    readonly_roots: Sequence[str] = (),
    resolve: Resolver = os.path.realpath,
) -> Decision:
    """判定动作。纯函数：同样的输入永远得到同样的结论。"""
    if action.type == "exec":
        command = action.command or ""
        for rule_id, pattern, verdict in DANGEROUS_RULES:
            if pattern.search(command):
                return Decision(verdict, f"命中破坏性规则 {rule_id}：{command.strip()}")
        return Decision(Verdict.NEED_APPROVAL, "命令执行一概需要审批（最小权限默认）")

    if action.type != "write":
        return Decision(Verdict.ALLOW, "只读动作，无副作用")

    if not action.path:
        return Decision(Verdict.DENY, "写动作缺少目标路径")

    resolved = resolve(action.path)
    if not _inside(resolved, sandbox_root):
        return Decision(
            Verdict.DENY,
            f"路径逃逸：{action.path} 解析后为 {resolved}，不在 sandbox {sandbox_root} 内",
        )

    for root in readonly_roots:
        if _inside(resolved, root):
            return Decision(Verdict.DENY, f"只读区：{root} 不允许写入")

    if not action.read_first:
        return Decision(Verdict.NEED_APPROVAL, "覆盖未读文件：需要先读或人工审批")

    return Decision(Verdict.ALLOW, "sandbox 内且已读过目标文件")
