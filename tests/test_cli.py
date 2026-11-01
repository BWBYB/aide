"""入口断言：`make run` 落的轨迹可用，`make replay` 能读回并逐项打印。

这两条存在的理由：清单里写了 `make run` / `make replay`，但目标从未定义过——
一条从未执行过的命令等于没有验收。现在用替身把入口跑通，
接真实 provider 时只换构造替身那一行。
"""

from __future__ import annotations

import json
from pathlib import Path

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
    for key in (
        "turn", "prefix_sha256", "state_bar", "tool_calls",
        "observations", "usage", "cost_usd", "model",
    ):
        assert key in first, f"轨迹缺少字段：{key}"
    assert first["state_bar"].startswith("<agent_status>"), "状态栏没有进轨迹"
    assert first["tool_calls"][0]["name"] == "write_file"
    # observation 是 ReAct 里模型唯一的外部信息源，必须落盘
    assert first["observations"][0]["content"] == "已写入 /sandbox/out.py"
    assert first["observations"][0]["truncated"] is False
    assert first["model"] == "fake-provider", "轨迹缺模型名，成本无法按模型归组"
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
    assert "observations  : [" in out and "已写入" in out
    assert "model         : fake-provider" in out
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


@pytest.mark.unit
def test_default_trace_path_is_timestamped(tmp_path, monkeypatch) -> None:
    """默认轨迹名必须唯一：固定 latest.jsonl 会让"跑两次丢一次"。"""
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--task", "写一个脚本"]) == 0
    files = sorted(p for p in (tmp_path / "traces").glob("*.jsonl") if p.name != "latest.jsonl")
    assert len(files) == 1
    assert files[0].name != "latest.jsonl"
    # 稳定入口：latest 指向时间戳文件（软链，不是第二份数据）
    latest = tmp_path / "traces" / "latest.jsonl"
    assert latest.is_symlink() and latest.resolve() == files[0].resolve()


@pytest.mark.unit
def test_make_run_does_not_hardcode_trace_path() -> None:
    """Makefile 的 run 目标不得写死 --trace。

    这条防的是"测试路径 ≠ 使用路径"：CLI 默认值已改成时间戳，
    但 make run 每次都显式传参时，那段默认逻辑从未被执行——
    测试绿、验收命令行为仍错。
    """
    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
    recipe = [
        line for line in makefile.splitlines()
        if "aide.cli run" in line
    ]
    assert recipe, "Makefile 里找不到 run 目标"
    for line in recipe:
        assert "latest.jsonl" not in line, f"run 目标写死了轨迹路径：{line}"
        assert "$(if $(TRACE)" in line, f"run 目标应仅在 TRACE 指定时传 --trace：{line}"
