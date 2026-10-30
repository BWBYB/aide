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

## 为什么 v1 索引没暴露

`chunk_markdown` 用固定 512 token 窗口，每篇文档的**中间块几乎都是满 512 token**。
一个 batch 内 8 条文本长度全相同时不产生 padding，`sum()-1` 恰好等于末位，结果正确。
只有文件尾块与短文档（个人资料全部属于此类）才会触发，v1 的 181 个 chunk 里这类占比很低，
所以评估数字看起来正常，问题被掩盖。

语料扩到 567 个 chunk 并纳入大量短个人资料后，错位比例显著上升，才在自相似度检查中暴露。

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
