"""入口断言：`make run` 落的轨迹可用，`make replay` 能读回并逐项打印。

这两条存在的理由：清单里写了 `make run` / `make replay`，但目标从未定义过——
一条从未执行过的命令等于没有验收。现在用替身把入口跑通，
接真实 provider 时只换构造替身那一行。
"""

from __future__ import annotations

import json

import pytest

from aide.cli import main
from aide.runtime.trace import load_trace


@pytest.mark.unit
def test_run_cli_writes_trace(tmp_path, monkeypatch, capsys) -> None:
    """run 必须落轨迹，且字段齐全（prefix_sha256 / 状态栏 / tokens / cost）。"""
    trace = tmp_path / "trace.jsonl"
    assert main(["run", "--task", "写一个脚本", "--trace", str(trace)]) == 0

    entries = load_trace(trace)
    assert len(entries) == 2, "替身应产生两轮"
    first = entries[0]
    for key in ("turn", "prefix_sha256", "state_bar", "tool_calls", "usage", "cost_usd"):
        assert key in first, f"轨迹缺少字段：{key}"
    assert first["state_bar"].startswith("<agent_status>"), "状态栏没有进轨迹"
    assert first["tool_calls"][0]["name"] == "write_file"
    assert first["usage"]["total_tokens"] == 120

    # 前缀在两轮之间必须一致（A 组断言在真实入口上的复核）
    assert entries[0]["prefix_sha256"] == entries[1]["prefix_sha256"]

    out = capsys.readouterr()
    assert "[stop] completed" in out.out


@pytest.mark.unit
def test_replay_reads_trace(tmp_path, capsys) -> None:
    """replay 必须能读回轨迹并逐项打印——这是"轨迹可追溯"的唯一验证方式。"""
    trace = tmp_path / "trace.jsonl"
    main(["run", "--task", "写一个脚本", "--trace", str(trace)])
    capsys.readouterr()

    assert main(["replay", "--trace", str(trace)]) == 0
    out = capsys.readouterr().out

    entries = load_trace(trace)
    assert f"prefix_sha256 : {entries[0]['prefix_sha256']}" in out
    assert "state_bar     : time:" in out
    assert "tokens        : 120" in out
    assert "cost_usd      : None" in out, "未配置价目表时应显式打印 None 而不是省略"
    assert "[replay] 2 轮" in out


@pytest.mark.unit
def test_max_turns_env_yields_structured_stop(tmp_path, monkeypatch, capsys) -> None:
    """AIDE_MAX_TURNS=1 必须走出「轮次耗尽」，而不是静默返回空答案。"""
    monkeypatch.setenv("AIDE_MAX_TURNS", "1")
    trace = tmp_path / "one.jsonl"
    assert main(["run", "--task", "写一个脚本", "--trace", str(trace)]) == 0

    out = capsys.readouterr().out
    assert "turn_limit" in out and "轮次耗尽" in out
    assert len(load_trace(trace)) == 1
