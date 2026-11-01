# M1b 计划：接真实 provider

> **当前状态**：主线 `d49162a` = M1a（替身）+ 旧包边界守卫，**不接真实模型**。
> 真实 provider 的完整实现保存在分支 `archive/m1b-provider`（`5da5807`），已验证可跑，未启用。

## 0. 撤回记录

### 撤了什么

| 提交 | 内容 | 处置 |
|---|---|---|
| `d49162a` | 禁止 AIDE 触达旧包中会调用 LLM 的模块 | **保留**：这是唯一被明确要求的事 |
| `bbf73af` | `.env` 自动加载、`make probe`、真实单价 | 撤回 |
| `5da5807` | 真实 provider 接线、工具形状、重试 | 撤回 |

### 为什么撤

要求只有一句：**AIDE 不调用老版本**。`d49162a` 已经做到并有测试守着；
后面两笔是「让 AIDE 用好新 API」，属于审查者自行扩大的范围——技术上成立，但不是被要求的事。

### 代码去哪了

```
archive/m1b-provider   →  5da5807
```

不是丢弃。重做时可直接取：

```bash
git cherry-pick 5da5807            # 整段取回
git checkout archive/m1b-provider -- src/aide/llm/client.py   # 只取某个文件
```

### 撤回后的状态

```
main = d49162a   100 passed, 2 skipped
tools/           只剩 load_skill.py（probe_llm.py 随提交撤回）
Makefile         不再 -include .env
configs/prices.json  回到 placeholder 版本
```

`.env` 文件仍在磁盘（gitignore 挡住），当前**不参与任何运行**。

---

## 1. 边界：M1b 做什么、不做什么

| 做 | 不做 |
|---|---|
| 非流式单轮调用 | 流式响应（策略①已定，但无流可续） |
| 单厂商（当前中转站的 gpt-6.1-terra） | 多厂商 failover（`neutralize_request` 已是预备件） |
| 429 / 5xx / 连接中断可重试，4xx 不可 | reasoning_content 逐个厂商回传（需 contract 档） |
| usage 解析进轨迹 | context-editing 微压缩（ADR 0006 已记） |
| 成本按价目表 + 版本号计算 | 成本与账单对账（中转站账单不分项目） |
| 超时接 D 组故障分类 | 真实工具执行（属 M2） |

**一条硬边界**：M1b 结束时，如果 `test_reasoning_echo_matches_provider_contract` 仍是 skip，
那 M1b 与 M1a 的区别就只剩「多了几百行」——这句话要写进完成条件。

---

## 2. 三个前置（不先修，M1b 做了也白做）

### 2.1 验收基线太松 —— P0，必须先修

**证据**（撤回前实测）：

```
$ make run TASK="回答一个字：好"
[turn 1] tokens=166
[stop] completed: 验收通过：python-file
```

模型只回了一个「好」字，验收通过——因为「好」是合法的 Python 标识符，`ast.parse` 不报错。

**后果**：接上真实模型后，「完成」判定会被任意一行合法表达式通过。模型编一句话就能收工，
这比没有验证器更危险：它看起来验证过。

**修法**（二选一，建议 A）：

- **A**：基线要求「至少一个可执行语句」——AST 里必须出现 `Call` / `Assign` / `Import` / `If` 等，
  纯 `Name` / 纯常量 / 空文件一律拒。
- **B**：基线要求「产物文件真的存在」，即执行器必须落盘（依赖 M2 的真实工具，M1b 做不到）。

**先写的断言**：

```
test_baseline_rejects_bare_identifier     "好" → False
test_baseline_rejects_empty_and_comment   "" / "# 注释" → False
test_baseline_accepts_real_script         "print(1)\n" → True
```

出处：`loop-engineering:17-19`（完成必须经独立验证，不能由模型自证）。

### 2.2 工具定义形状 —— 不修必 400

`Prefix.tools_payload()` 输出 `{"name","description","parameters"}`，
OpenAI 兼容协议要求 `{"type":"function","function":{...}}`。

M1a 阶段所有前缀断言都绿，因为它们只比较「轮次之间是否一致」，不检查形状。

**包壳的位置必须是 `context.py`，不能是 `client.py`**：`request["tools"]` 要等于真正发出去的字节，
否则轨迹里的 `prefix_sha256` 代表不了服务端看到的前缀，缓存命中也无从判断。

### 2.3 端点抖动 —— 不修三次挂两次

**实测**（同一台中转站，连续三次 `make run`）：两次 `SSL: UNEXPECTED_EOF` / `handshake timed out`。
控制实验：同一进程里裸 httpx 失败、`Client` 成功，反向也出现过——是端点随机断连，不是客户端代码。

成功时单次延迟 **11–16 秒**，一个 10 轮的 ReAct 循环就是 2–3 分钟。

**两个连带缺陷**：

- `classify("ConnectError")` → 归入 `UNKNOWN` → 不重试。关键词表写的是 `"connection"`，而 httpx 的类名是 `ConnectError`。
- `ProviderError` 的 429 与 400 类名相同，只看类名会把 429 一起放弃。

---

## 3. 五个阶段

每阶段都遵守同一条规则：**先写断言，再写实现**；实现不出来说明断言写早了。

### S1 · 工具形状（无网络，半天）

| 项 | 内容 |
|---|---|
| 改 | `core/context.py` 的 `tools_payload()` 包一层 `type=function` |
| 同步改 | `test_tool_definitions_never_mutated_midrun` 的取值路径 → `function.name`（同名同义不变，只改取值） |
| 新增断言 | `test_tool_schema_is_openai_function_shape` |
| 拒绝条件 | 形状不对但测试仍绿 = 不通过 |

### S2 · 配置与探测（不接线，半天）

| 项 | 内容 |
|---|---|
| 改 | `Makefile` 加 `-include .env` + `export`；新增 `tools/probe_llm.py` + `make probe` |
| 改 | `configs/prices.json` 填真实单价，`version` 换成日期 |
| 新增断言 | 无（探测脚本不参与测试） |
| 拒绝条件 | probe 打印出密钥 = 不通过；`.env` 未进 `.gitignore` = 不通过 |

`make run` 此阶段仍走替身。**探测与接线分开**，是为了让失败可归因：连不上和接错了是两件事。

### S3 · 接线（一天）

| 项 | 内容 |
|---|---|
| 改 | `cli.py`：`.env` 三项齐全 → `Client`；三项全空 → 替身；**只填一半 → exit 2** |
| 改 | 成本：价目表按 `version` 加载，模型不在表内时 `cost_usd=None` 并告警，不猜价 |
| 改 | 执行器：真实路径回答「工具未接入（M2），本次未执行」，替身路径保持「已写入」 |
| 新增断言 | 半填报错 / 三项齐全选真 provider 且成本算得出 / 模型缺价返回 None / 测试进程不得连真实模型（autouse 清空 `AIDE_*`） |
| 拒绝条件 | 只填一半却静默退回替身 = 不通过（会让人以为在用真模型） |

### S4 · 韧性（半天）

| 项 | 内容 |
|---|---|
| 改 | `loop.py`：`provider.complete` → `execute_with_retry(..., max_attempts=3)` |
| 改 | `recovery.classify`：识别 `ConnectError` / `RemoteProtocolError` / `SSLError`；优先采信结构化异常的 `retryable` 标记 |
| 新增断言 | 连接类可重试 / 4xx 与未知仍不可重试 / 异常自带标记优先 / 环上真的重试 / 不可重试只调一次 |
| 变异 | `PROVIDER_MAX_ATTEMPTS` 改 1 → 重试那条必须红 |
| 拒绝条件 | 放宽关键词后 `SomethingWeird` 变成可重试 = 不通过 |

**已知缺口（接受）**：重试对轨迹不可见。`TraceRecorder` 只在成功时写一条，失败的尝试不留痕。
若要可追溯，需把 `EventLog` 接进轨迹——留到 M5 有失败归因需求时再做。

### S5 · contract（需真实 key，半天）

| 项 | 内容 |
|---|---|
| 改 | 无（跑 `make test-contract`） |
| 目标 | `test_reasoning_echo_matches_provider_contract` 从 skip 变 pass |
| 前提 | 该厂商必须「不回传 reasoning 就 400」；宽松厂商会让这条测试通过得毫无信息量 |
| 拒绝条件 | M1b 结束时它仍 skip = M1b 未完成（除非在 ADR 里写明为什么这个厂商验不了） |

---

## 4. 新增断言总表（12 条）

| # | 断言 | 阶段 | 出处 |
|---|---|---|---|
| 1 | `test_baseline_rejects_bare_identifier` | 前置 | loop-engineering:17-19 |
| 2 | `test_baseline_rejects_empty_and_comment` | 前置 | 同上 |
| 3 | `test_baseline_accepts_real_script` | 前置 | 同上 |
| 4 | `test_tool_schema_is_openai_function_shape` | S1 | 自定（协议约束） |
| 5 | `test_partial_env_is_a_usage_error` | S3 | 自定 |
| 6 | `test_full_env_selects_real_provider_with_price` | S3 | 自定 |
| 7 | `test_model_missing_from_price_book_yields_none` | S3 | ADR 0010 |
| 8 | `test_tests_never_hit_real_provider` | S3 | 自定（autouse fixture 的自检） |
| 9 | `test_connection_layer_errors_are_retryable` | S4 | error-recovery:19-24 |
| 10 | `test_structured_error_flag_wins_over_typename` | S4 | 同上 |
| 11 | `test_loop_retries_transient_connect_error` | S4 | 自定 |
| 12 | `test_loop_does_not_retry_non_retryable_error` | S4 | error-recovery:19-24 |

清单口径：`docs/acceptance/M1-harness-core.md` 的 M1b 附录同步这 12 条，
`make check-spec-diff` 与 `make check-provenance` 必须继续通过。

---

## 5. 风险登记

| 风险 | 证据 | 应对 |
|---|---|---|
| **usage 被中转站篡改** | 未核实；这是 `AIDE_RUN_BUDGET_USD` 唯一的数据来源 | 第一次调用后拿官方量级对账（一句「hi」应是个位数到几十 token）；异常则禁用预算护栏并记录 |
| **responses 端点被注入 4380 token** | 实测同一条 `hi`：chat 输入 7，responses 输入 4387 | 只用 chat/completions；改端点需重新验证成本 |
| **成本与账单不可对账** | 中转站账单不按项目拆分 | 轨迹里的 `cost_usd` 定位为"内部估算"，ADR 0010 写明 |
| **隐私** | 每轮自动注入检索结果，可能含 `personal/` 语料 | 用户已明确不在意；若要收紧，用不含个人资料的 TASK 验证链路 |
| **延迟高** | 单次 11–16 秒 | 预算闸门 `max_seconds` 按此设；不要在 M1b 做长任务测试 |
| **重试不可见** | 轨迹只记成功那次 | 接受；M5 需要失败归因时再接 `EventLog` |
| **`git revert` 在本机不稳定** | 两次卡在 `sequencer` 残留 | 回退优先用 `reset --hard` + 归档分支，或 `restore` 单文件 |

---

## 6. 验收判据

M1b 勾选前必须全部满足：

```bash
# 1. 全量测试（含 12 条新断言）
make test                        # 预期 112 passed, 2 skipped（contract 仍 skip 则为 111+1s）

# 2. 真实调用跑通，轨迹字段齐全
make run TASK="用一句话说明你能做什么"
# 预期：[provider] 真实模型 …；[turn 1] …；轨迹含 model / usage / cost_usd / price_version

# 3. 回放能看到因果
make replay TRACE=traces/latest.jsonl
# 预期：observations 与 response 都在

# 4. 两个守门目标
make check-spec-diff             # 旧有今无双空
make check-provenance            # 出处覆盖：完整

# 5. contract 真跑（不是 skip）
make test-contract
```

**拒绝条件**：

- 任一条新断言可以用空实现通过；
- `cost_usd` 恒为 `None` 却没有任何告警；
- contract 仍 skip 且 ADR 里没有写明原因；
- 测试进程里出现真实网络请求（用 `make test` 的耗时判断：秒级完成才算没联网）。

---

## 7. 与 M1a 的关系

| | M1a（已验收） | M1b（本计划） |
|---|---|---|
| provider | FakeProvider | 真实模型 |
| 断言数 | 56（55 绿 + 1 skip） | +12 |
| `cost_usd` | 恒 `None` | 真实值（含 `price_version`） |
| `observations` | 替身产生 | 仍由替身/诚实执行器产生（真实工具是 M2） |
| 完成判定 | 替身产出必过基线 | **依赖 2.1 修好的基线** |

**M1b 完成时仍不能做的事**（写进 status，避免被读成"能用了"）：
真实工具执行（M2）、流式（M1.5）、多厂商（未定）。
