---
name: novel-reader
description: Use this skill when Claude Code must read, search, ask about, outline, analyze, distill style from, or build continuation packages for long TXT/Markdown novels with source evidence.
---

# Novel Reader

Default interface:

```bash
python ./bin/novel-reader do <book_id> "user request" --json
```

Use lower-level commands only when `do` returns `unknown`, fails, or the user explicitly asks for a specific command.

## Core Rules

- Default to Chinese output unless the user asks otherwise.
- Plot claims must cite chapter, chunk, and line references from Novel Reader evidence.
- If the book has an embedding index, prefer `--semantic` for search, Q&A, and continuation evidence; if semantic search fails, retry without it and say so.
- For future-plot questions such as "猜后续剧情", "预测发展", "结局可能", "伏笔会怎么回收", or "会不会黑化/背叛/死亡", use `predict`, not `continue`.
- Prediction output must include probability, evidence, counter-evidence or uncertainty, and must not claim to know the author's real future plot.
- Continuation prose must start from `write-next` or a continuation package; do not continue from memory alone.
- Style distillation must produce original-writing guidance, not direct imitation prompts for a specific author.
- Do not upload novel text. Local Qwen embedding is preferred when semantic search is needed.

## Governed Full Reading

When the user says anything like "精读完整本", "读完整本再分析", "完整拆解", "完整阅读", or asks for full-book conclusions:

1. Create a governed session:

```bash
python ./bin/novel-reader read-session <book_id> --goal full --mode balanced --json
```

2. Loop one batch at a time:

```bash
python ./bin/novel-reader read-next <session_id> --json
python ./bin/novel-reader submit-note <session_id> --chapter N --text "<structured note>" --json
```

3. Continue until:

```bash
python ./bin/novel-reader finalize-reading <session_id> --json
```

returns `full_scope_allowed=true`. The old `final_reports_allowed` field is deprecated and should be treated as a compatibility alias for `full_scope_allowed`.

Do not generate `--scope full` outline/map/analyze/style/continue output before finalize succeeds. If the user forces skipping, use `--scope partial` and clearly label the answer as partial-scope.

## Reading Depth

- `survey`: whole book L1 skim coverage.
- `balanced`: whole book L1 plus key chapters L2.
- `deep`: whole book L1, key chapters L2, anchor/focus chapters L3.

Use `balanced` by default. Use `deep` for continuation anchors, high-risk continuity work, or user-specified focus chapters.

## Normal Workflow

Import a file:

```bash
python ./bin/novel-reader ingest path/to/novel.txt
```

Routine requests:

```bash
python ./bin/novel-reader do <book_id> "这本书现在读到哪了" --json
python ./bin/novel-reader do <book_id> "找一下主角第一次失败的情节" --semantic --json
python ./bin/novel-reader do <book_id> "帮我分析战斗场景怎么写" --json
python ./bin/novel-reader predict <book_id> "后续剧情可能怎么发展？" --json
python ./bin/novel-reader do <book_id> "接第12章后面续写，短一点，偏悬疑" --semantic --json
```

Full-scope reports:

```bash
python ./bin/novel-reader outline <book_id> --scope full --json
python ./bin/novel-reader analyze <book_id> --scope full
python ./bin/novel-reader continue <book_id> --after-chapter N --scope full --json
```

## Answer Shape

For plot Q&A and analysis, include direct answer, evidence list, uncertainty note, and next reads/searches when needed.

For prediction, output a prediction package or summary with probabilities, source evidence, risk, watchlist, and a disclaimer that this is not author-confirmed future content.

For continuation, read the package constraints and self-checklist before writing original prose, then include a self-checklist after the prose.
