#!/usr/bin/env python3
"""加载 skill：把正文打进上下文，并留下机器可读的一行。

用法：
    python tools/load_skill.py agent-state-bar loop-engineering --for M1

日志五列（制表符分隔）：时间 / skill / sha256 前 16 / 字节数 / 里程碑。
哈希是核心——它证明读过的是**这个版本**；skill 升级后哈希对不上，测试会红，
逼你回头复核引用还成不成立。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = Path.home() / ".skills-manager" / "skills"
LOG_PATH = REPO_ROOT / "docs" / "skills-load.log"

EXIT_OK = 0
EXIT_USAGE = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="加载 skill 并写入留痕日志")
    parser.add_argument("names", nargs="+", help="skill 名称")
    parser.add_argument("--for", dest="milestone", default="-", help="关联里程碑，如 M1")
    args = parser.parse_args()

    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    records: list[str] = []

    for name in args.names:
        path = SKILLS_DIR / name / "SKILL.md"
        if not path.is_file():
            print(f"[error] 找不到 {path}", file=sys.stderr)
            return EXIT_USAGE
        data = path.read_bytes()
        # 实质：真的把正文读进来（后续由调用方阅读并摘出结论）
        print(data.decode("utf-8"))
        digest = hashlib.sha256(data).hexdigest()[:16]
        records.append(f"{timestamp}\t{name}\t{digest}\t{len(data)}\t{args.milestone}")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.writelines(record + "\n" for record in records)

    print(f"[log] 追加 {len(records)} 行 → {LOG_PATH}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
