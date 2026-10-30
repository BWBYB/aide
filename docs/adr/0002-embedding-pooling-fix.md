# ADR 0002：修正旧包的 last-token pooling（left padding 下标错误）

- 状态：已接受
- 日期：2026-10-30
- 关联里程碑：M0

## 现象

corpus-v2 的 Dense 索引建好后，用同一段 `embedding_text` 重新编码并与索引中的向量求余弦：

| chunk | 自相似度 |
|---|---|
| `book/afterword.md#0` | 0.274 |
| `book/chapter3.md#29` | 0.205 |
| `book/chapter7.md#30` | **1.000** |
| `personal/knowledge/profile.md#0` | **0.001** |

同一段文本与自己应得 1.0。部分 chunk 为 1.0、部分接近 0，说明向量与文本部分错位。

直接后果：用「吴禹的求职方向」检索个人资料，正确 chunk 连 Top-8 都进不去，最高分只有 0.37；
15 题新增题集的 E0 Hit@5 = 0.0000。这不是"题太难"，是索引坏了。

## 根因

旧包 `agentbook_rag/embedding.py`：

```python
def _last_token_pool(last_hidden_state, attention_mask):
    sequence_lengths = attention_mask.sum(dim=1) - 1
    return last_hidden_state[batch_indices, sequence_lengths]
```

`attention_mask.sum() - 1` 只有在 **right padding** 下才指向最后一个真实 token。
而 `QwenEmbedder.__init__` 明确用 `AutoTokenizer.from_pretrained(model_name, padding_side="left")`，
left padding 时真实 token 位于序列**末尾**，取 `sum()-1` 会落在 padding 区域，得到 pad 位置的隐状态。

## 触发条件（精确版）

**一个块只要在它所在的那个 batch 里不是最长，就会触发。**

batch 内长度不一致 → 短的那些被 left padding 补齐 → `sum()-1` 落到 pad 位置 → 取错隐状态。
只有"batch 内 8 条文本长度完全一致"时 `sum()-1` 才恰好等于末位，结果正确。

`chunk_markdown` 用固定 512 token 窗口，但**不是每个块都满 512**：文档最后一块、按标题切出的短节、
表格与代码段前后、以及个人资料这类短文档，都会产出不足 512 token 的块。
Markdown 正文按节聚合时，短块在**正文中段也会大量出现**，并不限于文件尾。

## 对 v1 基线的影响

对旧 v1 索引跑 `--verify-only`（`docs/status.md` 有记录）：抽样 9 条，**6 条失败**，
失败样本包括 `book/introduction.md#9`（496 字符）、`book/chapter2.md#10`（1899 字符）、
`book/chapter2.md#68`（301 字符）、`book/chapter3.md#17`（719 字符）等**正文中段块**，最差自相似度 0.0508。

需要挡掉一个错误读法：抽样是**专挑最短块**做的，属于有偏抽样，
因此 6/9 只能支持"**存在系统性错位**"，不能读成"67% 的块坏了"——**实际比例未测**。

结论：**v1 的 E3 Hit@5 = 0.857 是在一个部分错位的索引上测得的，不可作为对照基准**，
也不可用于"扩容前后"的差值比较。本文档早期的说法（"只有文件尾块与短文档才会触发，v1 占比很低，
所以评估数字看起来正常"）与实测矛盾，已作废并在此更正。

## 决策

**不改上游文件**（`docs/adr/0001`）。在 `knowledge/loader.py` 的 `load_embedding()` 里
于导入后覆盖模块级函数 `_last_token_pool`：

```python
def _left_padded_last_token_pool(last_hidden_state, attention_mask):
    return last_hidden_state[:, -1]
```

left padding 下每个样本的最后一个位置必然是真实 token，因此直接取末位对所有行都成立。

## 被否决的方案

1. **改旧包源码**：违反 ADR 0001 与 `AGENTS.md` 的"上游只读"约束。
2. **batch_size=1 回避 padding**：能绕开问题，但 567 次单条前向慢数倍，且没有修复根因，
   下次换模型或换分词器仍会复发。
3. **保留现状、只调整题目**：等于把索引缺陷合理化，会让后续所有检索与任务层结论失真。

## 后果

- 正面：向量与文本恢复一一对应；个人资料可被召回；`personal/` 前缀的 20 个 chunk 不再是"死数据"。
- 负面：上游包带 bug，属于外部依赖风险；已在本 ADR 记录，建议向上游提 issue。
- 验证方式：重建后对抽样 chunk 做自相似度检查，应全部为 1.000（见 `tests/test_corpus.py` 的 `test_embeddings_self_consistent`）。
