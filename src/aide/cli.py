"""命令行入口：`make run` / `make replay`。

**当前只接替身**：M1a 阶段先证明「入口能跑、轨迹可回放」这四件与模型无关的事：
CLI 参数被读到、轨迹字段齐全、`AIDE_MAX_TURNS` 能走出轮次耗尽、回放能读回轨迹。
接真实 provider 是 M1b 的事，届时只换构造替身那一行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .core.context import Prefix, ToolSpec
from .core.limits import Limits
from .core.loop import StopReason, run_loop
from .core.message import ToolCall
from .core.verifier import PYTHON_FILE_BASELINE
from .runtime.trace import TraceRecorder, load_trace

TOOLS = (
    ToolSpec("read_file", "读取文件", {"type": "object", "properties": {"path": {"type": "string"}}}),
    ToolSpec(
        "write_file",
        "写入文件",
        {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
    ),
)
PREFIX = Prefix(system_prompt="你是严谨的个人助理。不确定时先读再写。", tools=TOOLS)


def _fake_provider(task: str):
    """替身：一次工具调用 + 一次可过验收的产出。真实 provider 见 M1b。"""
    from .runtime.fakes import FakeProvider, ModelResponse

    return FakeProvider(
        [
            ModelResponse(
                content=None,
                tool_calls=(ToolCall("c1", "write_file", {"path": "/sandbox/out.py", "read_first": True}),),
                usage={"total_tokens": 120},
            ),
            ModelResponse(content="print('hello')\n", usage={"total_tokens": 80}),
        ]
    )


def _limits(args: argparse.Namespace) -> Limits:
    max_turns = args.max_turns or int(os.environ.get("AIDE_MAX_TURNS", "8"))
    return Limits(
        max_turns=max_turns,
        max_tokens=int(os.environ.get("AIDE_MAX_TOKENS", "200000")),
        max_seconds=float(os.environ.get("AIDE_MAX_SECONDS", "600")),
        context_window=int(os.environ.get("AIDE_CONTEXT_WINDOW", "128000")),
    )


def cmd_run(args: argparse.Namespace) -> int:
    provider = _fake_provider(args.task)
    recorder = TraceRecorder(provider)
    print("[provider] 替身 FakeProvider（真实模型接入见 M1b）", file=sys.stderr)

    result = run_loop(
        recorder,
        PREFIX,
        args.task,
        limits=_limits(args),
        clock=datetime.now,
        verifier_baseline=PYTHON_FILE_BASELINE,
        sandbox_root="/sandbox",
        tool_executor=lambda call: f"已写入 {call.arguments.get('path')}",
    )

    for entry in recorder.entries:
        calls = ", ".join(call["name"] for call in entry.tool_calls) or "-"
        print(
            f"[turn {entry.turn}] prefix={entry.prefix_sha256} "
            f"tools={calls} tokens={entry.usage.get('total_tokens', 0)}"
        )

    trace_path = Path(args.trace).resolve()
    recorder.save(trace_path)
    print(f"[stop] {result.stop.reason.value}: {result.stop.detail}")
    print(f"[trace] {trace_path}")
    if result.stop.reason is StopReason.TURN_LIMIT:
        print("[note] 轮次耗尽属于结构化终止，不是异常")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    entries = load_trace(Path(args.trace).resolve())
    for entry in entries:
        print(f"--- turn {entry['turn']} ---")
        print(f"prefix_sha256 : {entry['prefix_sha256']}")
        print(f"state_bar     : {entry['state_bar'].splitlines()[1] if entry['state_bar'] else '-'}")
        print(f"tool_calls    : {json.dumps(entry['tool_calls'], ensure_ascii=False)}")
        print(f"tokens        : {entry['usage'].get('total_tokens', 0)}")
        print(f"cost_usd      : {entry['cost_usd']}")
        print(f"response      : {(entry['response_text'] or '')[:60]}")
    print(f"[replay] {len(entries)} 轮")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aide")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="跑一次循环并落轨迹")
    run_p.add_argument("--task", required=True)
    run_p.add_argument("--max-turns", type=int, default=None)
    run_p.add_argument("--trace", default="traces/latest.jsonl")
    run_p.set_defaults(func=cmd_run)

    replay_p = sub.add_parser("replay", help="回放轨迹")
    replay_p.add_argument("--trace", default="traces/latest.jsonl")
    replay_p.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
