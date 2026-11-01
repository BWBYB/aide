import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "knowledge", REPO_ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


@pytest.fixture
def tmp_dir():
    """标准库临时目录，绕开 pytest 的 tmp 工厂。

    某些沙箱环境会拦截 pytest 创建 `/private/var/.../pytest-of-root`，
    导致所有用 `tmp_path` 的用例无法运行。`tempfile` 走的是另一条路径。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as path:
        yield Path(path)
