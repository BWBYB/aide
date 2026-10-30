"""Skill 加载留痕的机器校验。

两条断言，价值不同：
- `test_declared_skills_logged`：验收清单声明的 skill 必须真的加载过
- `test_logged_skill_version_matches`：**skill 升级后哈希对不上就变红**，
  逼你回头复核那 35 条引用还成不成立。这条才是钩子，不是存档。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG = REPO_ROOT / "docs" / "skills-load.log"
SKILLS_DIR = Path.home() / ".skills-manager" / "skills"

#: 与 docs/acceptance/Mx-*.md 的 Skill 留痕段保持同步；改清单就改这里
DECLARED: dict[str, set[str]] = {
    "M1": {
        "context-engineering",
        "kv-cache-design",
        "agent-state-bar",
        "context-compression",
        "error-recovery",
        "coding-agent-harness",
    },
}


def _records() -> list[tuple[str, str, str, str, str]]:
    if not LOG.is_file():
        return []
    rows: list[tuple[str, str, str, str, str]] = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        assert len(parts) == 5, f"日志行格式错误（应有 5 列）：{line!r}"
        rows.append(tuple(parts))  # type: ignore[arg-type]
    return rows


def _logged_by_milestone() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for _ts, name, _sha, _size, milestone in _records():
        result.setdefault(milestone, set()).add(name)
    return result


@pytest.mark.unit
def test_declared_skills_logged() -> None:
    """声明的 skill 必须都在日志里，且带对应里程碑。"""
    logged = _logged_by_milestone()
    assert LOG.is_file(), f"日志不存在：{LOG}（先跑 make skill-load）"
    for milestone, names in DECLARED.items():
        missing = names - logged.get(milestone, set())
        assert not missing, f"{milestone} 未留痕：{sorted(missing)}"


@pytest.mark.unit
def test_logged_skill_version_matches() -> None:
    """日志里的哈希与当前 SKILL.md 一致；不一致说明 skill 升级过，需重读复核。"""
    mismatches: list[str] = []
    checked: set[str] = set()
    for _ts, name, sha, size, _milestone in _records():
        path = SKILLS_DIR / name / "SKILL.md"
        if not path.is_file():
            mismatches.append(f"{name}: 文件已不存在")
            continue
        data = path.read_bytes()
        current = hashlib.sha256(data).hexdigest()[:16]
        if current != sha:
            mismatches.append(f"{name}: 日志 {sha} / 当前 {current}")
        if str(len(data)) != size:
            mismatches.append(f"{name}: 日志字节数 {size} / 当前 {len(data)}")
        checked.add(name)
    assert not mismatches, "skill 版本已变，需重读并复核引用：\n" + "\n".join(mismatches)
    assert checked, "日志里没有任何记录"
