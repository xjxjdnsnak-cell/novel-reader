---
name: novel-reader
description: Read, search, ask about, outline, analyze, distill style from, and build continuation packages for long TXT/Markdown novels with source-grounded evidence.
compatibility: opencode
---

# Novel Reader

Default interface:

```bash
python ./bin/novel-reader do <book_id> "user request" --json
```

Use lower-level commands only when `do` returns `unknown`, fails, or the user explicitly asks for a specific command.

## Rules

- Default to Chinese output.
- Plot claims must cite chapter, chunk, and line references.
- If the book has an embedding index, prefer `--semantic` for search, Q&A, and continuation evidence. If semantic search fails, say so and retry without it.
- For future-plot questions such as "猜后续剧情", "预测发展", "结局可能", "伏笔会怎么回收", or "会不会黑化/背叛/死亡", use `predict`, not `continue`.
- Prediction output must include probability, evidence, uncertainty, and must not claim to know the author's real future plot.
- Continuation writing must start from `write-next` or a continuation package.
- Style distillation must be original-writing guidance, not direct imitation instructions.
- Do not upload novel text; prefer local Qwen embedding for semantic search.

## Governed Full Reading

When the user asks to "精读完整本", "读完整本再分析", "完整拆解", "完整阅读", or requests full-book conclusions:

```bash
python ./bin/novel-reader read-session <book_id> --goal full --mode balanced --json
python ./bin/novel-reader read-next <session_id> --json
python ./bin/novel-reader submit-note <session_id> --chapter N --text "<structured note>" --json
python ./bin/novel-reader finalize-reading <session_id> --json
```

Call `read-next` and `submit-note` repeatedly until `full_scope_allowed=true`. The old `final_reports_allowed` field is deprecated and should be treated as a compatibility alias for `full_scope_allowed`.

Do not generate `--scope full` outline/map/analyze/style/continue output before `finalize-reading` succeeds. If the user forces skipping, use `--scope partial` and label the result as partial-scope.

## Reading Depth

- `survey`: whole book L1 skim coverage.
- `balanced`: whole book L1 plus key chapters L2.
- `deep`: whole book L1, key chapters L2, anchor/focus chapters L3.

Use `balanced` by default. Use `deep` for continuation anchors or high-risk continuity work.

## Normal Commands

```bash
python ./bin/novel-reader ingest path/to/novel.txt
python ./bin/novel-reader do <book_id> "这本书现在读到哪了" --json
python ./bin/novel-reader do <book_id> "找一下主角第一次失败的情节" --semantic --json
python ./bin/novel-reader do <book_id> "帮我分析战斗场景怎么写" --json
python ./bin/novel-reader predict <book_id> "后续剧情可能怎么发展？" --json
python ./bin/novel-reader write-next <book_id> --after-chapter 12 --outline "用户大纲" --json
```

## Output Discipline

For plot questions and analysis, answer with a direct answer, evidence list, uncertainty note, and next reads/searches when useful. For prediction, include probabilities, evidence, risk, watchlist, and a disclaimer. For continuation, read the package constraints and self-checklist before writing original prose.
