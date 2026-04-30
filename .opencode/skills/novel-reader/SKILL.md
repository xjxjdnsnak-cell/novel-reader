---
name: novel-reader
description: Read, outline, ask about, analyze, distill language style, and build continuation packages from complete long TXT/Markdown novels with a local index and source-grounded evidence.
compatibility: opencode
---

# Novel Reader

Use this skill when a novel is too large for a single model context window. The bundled CLI splits TXT/Markdown novels into chapters and chunks, builds a local SQLite index, tracks chapter-summary coverage, and prepares evidence packets for plot Q&A.

## Rules

- Default to Chinese output.
- Do not say the whole novel is fully read unless `novel-reader status` shows 100% summary coverage.
- For plot Q&A, call `/novel-ask` or run `python ./bin/novel-reader ask ...` before answering.
- Use chunk IDs, chapter numbers, and line ranges for factual claims.
- If evidence is thin or coverage is incomplete, state that clearly.
- Embedding is optional and opt-in; do not send text to an external service unless the user configured it.
- Style distillation must produce an original-writing transfer guide, not direct imitation instructions for a specific author.
- Continuation writing must start from a `/novel-continue` package, not from memory alone.

## Workflow

1. Import the book with `/novel-ingest path/to/book.txt`.
2. Check progress with `/novel-status <book_id>`.
3. Read chapters with `python ./bin/novel-reader read <book_id> --chapter N`.
4. After each chapter, record a summary with `python ./bin/novel-reader note <book_id> --chapter N --text "<summary>"`.
5. Generate durable artifacts with `/novel-outline <book_id> --write`, `/novel-map <book_id>`, and `/novel-analyze <book_id>`.
6. Distill language style with `/novel-style <book_id>` or `/novel-style <book_id> --scene 战斗`.
7. Build continuation packages with `/novel-continue <book_id> --after-chapter 12 --outline "..."`.

## Summary Shape

```markdown
## 第 N 章：标题
- 事件：
- 人物与动机：
- 冲突：
- 情节因果：
- 伏笔/回收：
- 设定/地点/势力：
- 时间线：
- 写作观察：
- 证据块：
```

## Answer Shape

For plot questions, answer with:

- direct answer
- evidence list with chapter/chunk/line references
- uncertainty note if the relevant chapters are not fully covered
- suggested next reads or searches when needed

## Style Distillation

For language style requests:

- run `/novel-style <book_id>` for full-book style evidence
- run `/novel-style <book_id> --scene 战斗` for a scene-specific guide
- cite chunk, chapter, and line references
- include language profile, scene style, original-writing transfer guide, and forbidden list
- do not reuse source excerpts as new prose and do not generate "write like this author" prompts

## Continuation Writing

For continuation requests:

- run `/novel-continue <book_id> --after-chapter N` or `/novel-continue <book_id> --after-chunk c0001-001`
- combine with `--outline "用户给的新剧情大纲"` when the user provides a direction
- use `recent_context`, `plot_evidence`, `style_evidence`, and `constraints` before writing
- follow `constraints.hard`; treat inferred constraints as guidance and uncertain constraints as warnings
- after the continuation prose, output the self-checklist
- do not copy source sentences or generate direct author-imitation instructions
