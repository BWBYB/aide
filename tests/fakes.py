"""测试替身：实现已移到 src/aide/runtime/fakes.py，这里只做转发。

移过去的理由：CLI 也要用替身把入口跑通，产品侧不能依赖 tests/。
"""

from aide.runtime.fakes import *  # noqa: F401,F403
