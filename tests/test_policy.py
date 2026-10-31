"""E 组：执行策略纯函数（5 条断言）。

全部不触碰真实文件系统：`resolve` 作为参数注入，symlink 规则用假解析器验证，
因此不依赖平台（macOS 的 /tmp 是 /private/tmp 的符号链接）。
"""

from __future__ import annotations

import pytest

from aide.core.policy import DANGEROUS_RULES, Action, Verdict, evaluate

SANDBOX = "/sandbox"
READONLY = ("/sandbox/book",)


def fake_resolver(mapping: dict[str, str]):
    def resolve(path: str) -> str:
        return mapping.get(path, path)

    return resolve


@pytest.mark.unit
def test_policy_denies_path_escape() -> None:
    """写操作不能逃出 sandbox root。"""
    denied = evaluate(Action("write", path="/etc/passwd"), sandbox_root=SANDBOX)
    assert denied.verdict is Verdict.DENY
    assert denied.allowed is False
    assert "逃逸" in denied.reason

    # 同前缀但不同目录：/sandbox2 不是 /sandbox 的子路径
    sibling = evaluate(Action("write", path="/sandbox2/x"), sandbox_root=SANDBOX)
    assert sibling.verdict is Verdict.DENY, "前缀匹配不是路径包含"

    ok = evaluate(
        Action("write", path="/sandbox/notes.md", read_first=True), sandbox_root=SANDBOX
    )
    assert ok.verdict is Verdict.ALLOW


@pytest.mark.unit
def test_policy_denies_write_to_readonly_root() -> None:
    """只读区（书稿仓库、旧 RAG 仓库）禁止写入。"""
    decision = evaluate(
        Action("write", path="/sandbox/book/chapter1.md", read_first=True),
        sandbox_root=SANDBOX,
        readonly_roots=READONLY,
    )
    assert decision.verdict is Verdict.DENY
    assert "只读" in decision.reason


@pytest.mark.unit
def test_policy_requires_approval_for_exec() -> None:
    """命令执行一律需要审批（最小权限默认）。"""
    decision = evaluate(Action("exec", command="ls -la"), sandbox_root=SANDBOX)
    assert decision.verdict is Verdict.NEED_APPROVAL
    assert "审批" in decision.reason


@pytest.mark.unit
def test_policy_blocks_destructive_pattern() -> None:
    """破坏性捷径：删根目录直接拒；其余破坏性命令至少需要审批。"""
    denied = evaluate(Action("exec", command="rm -rf /"), sandbox_root=SANDBOX)
    assert denied.verdict is Verdict.DENY, "删根目录不得只要求审批"

    # 规则表逐条的正例见下方参数化测试

    # 覆盖未读文件：不是禁用，而是要求先读或人工审批
    blind = evaluate(Action("write", path="/sandbox/x.py"), sandbox_root=SANDBOX)
    assert blind.verdict is Verdict.NEED_APPROVAL


@pytest.mark.unit
def test_policy_denies_symlink_escape() -> None:
    """符号链接解析后逃出 sandbox 必须拒绝。

    `resolve` 注入而非直接调 `os.path.realpath`：否则 evaluate 就不再是纯函数，
    测试要造真实目录、受平台差异影响。
    """
    resolve = fake_resolver({"/sandbox/link": "/etc/passwd"})
    decision = evaluate(
        Action("write", path="/sandbox/link", read_first=True),
        sandbox_root=SANDBOX,
        resolve=resolve,
    )
    assert decision.verdict is Verdict.DENY
    assert "/etc/passwd" in decision.reason

    # 假解析器不改变普通路径：同一函数仍放行正常写入（证明测的是规则而非环境）
    normal = evaluate(
        Action("write", path="/sandbox/ok.md", read_first=True),
        sandbox_root=SANDBOX,
        resolve=resolve,
    )
    assert normal.verdict is Verdict.ALLOW


#: 规则表 → 正例。`rule` 列必须与 policy.DANGEROUS_RULES 的编号一一对应。
DANGER_CASES = [
    ("52", "rm -rf /", Verdict.DENY),
    ("52", "rm -rf ~", Verdict.DENY),
    ("52", "rm -rf $HOME", Verdict.DENY),
    ("53", "rm -rf *", Verdict.DENY),
    ("53", "rm -rf *.log", Verdict.NEED_APPROVAL),
    ("54", "drop database prod", Verdict.NEED_APPROVAL),
    ("55", "truncate table users", Verdict.NEED_APPROVAL),
    ("56", "git reset --hard", Verdict.NEED_APPROVAL),
]


@pytest.mark.unit
@pytest.mark.parametrize("rule,command,expected", DANGER_CASES)
def test_every_dangerous_rule_has_a_positive_case(
    rule: str, command: str, expected: Verdict
) -> None:
    """规则表里每条规则至少一个正例；新增规则必须同时加一行，否则不覆盖。"""
    decision = evaluate(Action("exec", command=command), sandbox_root=SANDBOX)
    assert decision.verdict is expected, f"规则 {rule}: {command} -> {decision.verdict}"


@pytest.mark.unit
def test_dangerous_rule_table_is_fully_covered() -> None:
    """规则表的编号集合必须等于测试覆盖的编号集合。"""
    table_ids = {rule_id for rule_id, _pattern, _verdict in DANGEROUS_RULES}
    covered_ids = {rule for rule, _cmd, _expected in DANGER_CASES}
    assert table_ids == covered_ids, f"未覆盖的规则：{sorted(table_ids - covered_ids)}"
