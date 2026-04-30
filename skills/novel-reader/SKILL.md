---
name: novel-reader
description: Use this skill when the user wants Claude Code to read a complete long novel, especially TXT/Markdown works that are too large for one context window, then produce plot outlines, plot Q&A, book maps, writing analysis, language style distillation, or continuation writing packages with source evidence.
---

# Novel Reader

Use this skill to handle complete long-form novels without pretending that the whole book fits in one context window. The plugin provides a local CLI named `novel-reader` that indexes TXT/Markdown novels into chapters and chunks, tracks chapter-summary coverage, and returns source-grounded evidence for plot questions.

## Core Rules

- Default to Chinese output unless the user asks otherwise.
- Never claim the full novel has been read unless `novel-reader status` shows 100% summary coverage.
- For factual plot Q&A, use `novel-reader ask` or `novel-reader search` first, then answer with chapter, chunk, and line references.
- If a question involves unread chapters, say the reading coverage is incomplete and read or ask the user to authorize reading the relevant chapters before giving a firm answer.
- Treat generated maps and reports as working artifacts. Important claims still need source chunks.
- Do not upload novel text. Embedding is optional and must only be used when the user has explicitly configured it.
- For language style distillation, produce an original-writing transfer guide, not a prompt to imitate a specific living author.
- For continuation writing, first build a continuation package with `novel-reader continue`; do not continue from memory alone.

## First-Time Setup

From the plugin root, use the bundled wrapper:

```bash
python ./bin/novel-reader ingest path/to/book.txt
```

The command prints a `book_id`. Use that id for all later commands. If the Python package is installed, `novel-reader ingest path/to/book.txt` is also fine.

## Full Reading Workflow

1. Run `novel-reader status <book_id>` to inspect chapter count, chunk count, and summary coverage.
2. Read chapters in order with `novel-reader read <book_id> --chapter N`.
3. After reading a chapter, write a structured summary with:

```bash
novel-reader note <book_id> --chapter N --text "<summary>"
```

Use this summary shape:

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

4. Repeat until `status` reports 100% coverage.
5. Generate durable artifacts:

```bash
novel-reader outline <book_id> --write
novel-reader map <book_id>
novel-reader analyze <book_id>
```

## Plot Outline

For "梳理剧情", "按章节整理", "主线支线", or "时间线" requests:

1. Check coverage with `status`.
2. Use `outline` and `map` if summaries exist.
3. If summaries are missing, read the needed chapters and record notes before producing a whole-book outline.
4. Separate confirmed plot facts from interpretation.

## Plot Q&A

For "为什么", "谁做了什么", "伏笔在哪里", "某情节是否矛盾", or other factual plot questions:

1. Run:

```bash
novel-reader ask <book_id> "<question>"
```

2. Read any high-value chunks if the evidence snippets are too short:

```bash
novel-reader read <book_id> --chunk c0001-001
```

3. Answer with:

- short direct answer
- supporting evidence with chapter/chunk/line references
- uncertainty or missing-reading warning if evidence is insufficient

## Writing Analysis

For writing craft requests:

1. Prefer full coverage before final diagnosis.
2. Use `novel-reader analyze <book_id>` as the report scaffold.
3. Evaluate plot structure, pacing, conflict density, character arcs, promise/payoff, foreshadowing, setting consistency, and revision priority.
4. Tie major claims to source chunks or chapter summaries.

## Language Style Distillation

For "蒸馏文风", "语言风格", "转写指南", "场景写法", or "统一文风" requests:

1. Check coverage:

```bash
novel-reader status <book_id>
```

2. Build a structured evidence packet:

```bash
novel-reader style <book_id> --json
```

For one scene type:

```bash
novel-reader style <book_id> --scene 战斗 --json
```

3. When the user wants artifacts, write them locally:

```bash
novel-reader style <book_id> --write
```

4. Output four sections:

- language style profile: narrative distance, sentence rhythm, paragraph density, punctuation, imagery, emotional register, dialogue function
- scene style guide: battle, suspense, emotion, daily life, exposition, or the requested scene
- original-writing transfer guide: reusable craft moves for new characters, new settings, and new wording
- forbidden list: do not copy source sentences, unique metaphors, proper nouns, recognizable scenes, or produce "write like this author" instructions

Use only short excerpts from the evidence packet and always cite chapter, chunk, and line references.

## Continuation Writing

For "续写", "接着写", "按大纲写下一章", or "延续当前剧情" requests:

1. Build a continuation package before writing prose:

```bash
novel-reader continue <book_id> --after-chapter N --json
```

Useful variants:

```bash
novel-reader continue <book_id> --after-chunk c0001-001 --json
novel-reader continue <book_id> --after-chapter N --outline "用户给的新剧情大纲" --json
novel-reader continue <book_id> --outline-file next-arc.md --scene 悬疑 --semantic --json
```

2. Read the package sections in order: `recent_context`, `plot_evidence`, `style_evidence`, `constraints`, `draft_instructions`, `self_checklist`.
3. Write original continuation prose only after the package is available.
4. Follow `constraints.hard`; treat `constraints.inferred` as guidance and `constraints.uncertain` as risk notes.
5. After the prose, output the self-checklist and note any unresolved risk.

Continuation prose must preserve continuity and abstract style traits, but must not copy source sentences, unique metaphors, proper nouns as substitutes for new invention, or recognizable passages.

## Optional Embedding

Keyword/FTS search works locally by default. To enable semantic search, the user must set:

```bash
NOVEL_READER_EMBED_API_KEY
NOVEL_READER_EMBED_BASE_URL
NOVEL_READER_EMBED_MODEL
```

Then run:

```bash
novel-reader embed <book_id> --provider openai-compatible
```

Only use `--semantic` after embedding has been built.
