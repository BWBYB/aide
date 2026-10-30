"""合并多个 JSONL 题集/改写文件，按 id 去重并保持输入顺序。

用途：把旧冻结测试集（21 题）与 M0 新增题集（15 题）合成 36 题，
两套 gold 互补，覆盖全书 + 个人资料。

    python knowledge/merge_sets.py out.jsonl a.jsonl b.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def merge(paths: list[Path], output: Path) -> int:
    seen: dict[str, dict] = {}
    order: list[str] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{path}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            key = record.get("id") or record.get("question_id")
            if not key:
                raise ValueError(f"{path}:{line_number} 缺少 id 字段")
            if key in seen:
                if seen[key] != record:
                    raise ValueError(f"id 冲突且内容不同：{key}（来自 {path}）")
                continue
            seen[key] = record
            order.append(key)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for key in order:
            handle.write(json.dumps(seen[key], ensure_ascii=False) + "\n")
    return len(order)


def main() -> int:
    parser = argparse.ArgumentParser(description="合并 JSONL 题集/改写文件")
    parser.add_argument("output", type=Path)
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()
    count = merge(args.inputs, args.output)
    print(f"[merge] {count} 条 -> {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as error:
        print(f"[error] {error}", file=sys.stderr)
        raise SystemExit(2)
