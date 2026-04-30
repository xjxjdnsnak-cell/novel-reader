# Novel Reader

Novel Reader is a Claude Code + OpenCode compatible plugin for reading long TXT/Markdown novels that do not fit into one model context window. It builds a local chapter/chunk index, tracks chapter-summary coverage, and helps the model produce plot outlines, plot Q&A, full-book maps, writing analysis, language style distillation, and continuation writing packages with source evidence.

## What It Solves

- Million-word novels cannot be read safely in one prompt.
- Plot Q&A needs evidence, not vague memory.
- Writing analysis needs a full-book map: chapters, characters, timelines, foreshadowing, settings, and event chains.
- Style work needs statistics and short evidence, not direct author imitation.
- Continuation writing needs a traceable task package before prose generation.
- Privacy matters: the default workflow is local-only.

## Layout

```text
novel-reader/
  .claude-plugin/plugin.json
  skills/novel-reader/SKILL.md
  .opencode/skills/novel-reader/SKILL.md
  .opencode/commands/novel-*.md
  .opencode/plugins/novel-reader.ts
  bin/novel-reader
  bin/novel-reader.cmd
  bin/novel-reader.ps1
  src/novel_reader/
```

The Python CLI is the shared core. Claude Code and OpenCode use thin adapters that call the same commands.

## Requirements

- Python 3.9+
- No required third-party Python packages
- TXT/Markdown input files only in v0.1

## Quick Start

```bash
python ./bin/novel-reader ingest path/to/novel.txt
python ./bin/novel-reader status <book_id>
python ./bin/novel-reader read <book_id> --chapter 1
python ./bin/novel-reader search <book_id> "关键人物或情节"
python ./bin/novel-reader ask <book_id> "某个伏笔在哪里回收？"
```

Generate durable artifacts:

```bash
python ./bin/novel-reader outline <book_id> --write
python ./bin/novel-reader map <book_id>
python ./bin/novel-reader analyze <book_id>
python ./bin/novel-reader style <book_id> --write
```

Local data is stored under `.novel-reader/<book_id>/`.

## Continuation Packages

`continue` builds a continuation task package. It does not generate prose by itself.

```bash
python ./bin/novel-reader continue <book_id> --after-chapter 12 --json
python ./bin/novel-reader continue <book_id> --after-chunk c0012-004 --json
python ./bin/novel-reader continue <book_id> --after-chapter 12 --outline "主角潜入北塔" --json
python ./bin/novel-reader continue <book_id> --outline-file next-arc.md --scene 悬疑 --semantic --json
python ./bin/novel-reader continue <book_id> --after-chapter 12 --write
```

The package includes:

- `recent_context`: short excerpts before the continuation point
- `plot_evidence`: retrieved plot, character, setting, and foreshadowing evidence
- `style_evidence`: style statistics and scene evidence
- `constraints`: hard, inferred, uncertain, and copyright-boundary rules
- `draft_instructions`: how Claude/OpenCode should write the continuation
- `self_checklist`: checks to run after writing prose

`--write` creates timestamped files and latest aliases under `.novel-reader/<book_id>/continuations/`.

## Language Style Distillation

Use `style` to collect local statistics and short source evidence for an original-writing transfer guide:

```bash
python ./bin/novel-reader style <book_id>
python ./bin/novel-reader style <book_id> --scene 战斗
python ./bin/novel-reader style <book_id> --json
python ./bin/novel-reader style <book_id> --write
```

`--write` creates:

- `.novel-reader/<book_id>/styles/style-profile.md`
- `.novel-reader/<book_id>/styles/scene-styles.md`
- `.novel-reader/<book_id>/styles/style-guide.md`

The report covers sentence rhythm, paragraph density, punctuation, dialogue ratio, high-frequency word fields, short excerpts, and scene profiles for 战斗、悬疑、感情、日常、说明. It is for original writing guidance; do not use it to copy source passages or generate direct "write like this author" prompts.

## Claude Code

Use this folder as a Claude Code plugin directory. The plugin contains:

- `.claude-plugin/plugin.json`
- `skills/novel-reader/SKILL.md`
- `bin/novel-reader`

The Skill tells Claude Code to read chapter by chapter, cite chunk evidence, produce original-writing style guides, and build continuation packages before writing prose.

## OpenCode

Copy or keep this folder as an OpenCode project/plugin folder. It provides:

- `.opencode/skills/novel-reader/SKILL.md`
- `.opencode/commands/novel-ingest.md`
- `.opencode/commands/novel-status.md`
- `.opencode/commands/novel-outline.md`
- `.opencode/commands/novel-ask.md`
- `.opencode/commands/novel-map.md`
- `.opencode/commands/novel-analyze.md`
- `.opencode/commands/novel-style.md`
- `.opencode/commands/novel-continue.md`
- `.opencode/plugins/novel-reader.ts`

Example OpenCode commands:

```text
/novel-ingest path/to/novel.txt
/novel-status <book_id>
/novel-ask <book_id> "主角为什么背叛组织？"
/novel-style <book_id> --scene 战斗
/novel-continue <book_id> --after-chapter 12 --outline "主角潜入北塔"
```

## Reading Policy

The plugin is designed around a coverage ledger:

1. `ingest` splits the book into chapters and chunks.
2. The model reads chapters with `read`.
3. The model records summaries with `note`.
4. `status` reports coverage.
5. Q&A uses `ask` or `search` for evidence.
6. Whole-book conclusions require complete or explicitly scoped coverage.
7. Continuation prose requires a `continue` package first.

## Optional Embedding

Keyword search and SQLite FTS work locally by default. Semantic search is opt-in.

Set:

```bash
NOVEL_READER_EMBED_API_KEY=...
NOVEL_READER_EMBED_BASE_URL=http://127.0.0.1:8081/v1
NOVEL_READER_EMBED_MODEL=qwen3-embedding-0.6b
```

Then run:

```bash
python ./bin/novel-reader embed <book_id> --provider openai-compatible
python ./bin/novel-reader search <book_id> "人物动机变化" --semantic
python ./bin/novel-reader continue <book_id> --after-chapter 12 --outline "新剧情" --semantic --json
```

If embedding is not configured, all core local features still work.

## Main Commands

```text
ingest <file>                      Import TXT/Markdown and build index
list                               List imported books
status <book>                      Show coverage and index status
read <book> --chapter N            Read a chapter
read <book> --chunk c0001-001      Read one chunk
search <book> "<query>"            Search source text
ask <book> "<question>"            Build a plot Q&A evidence packet
note <book> --chapter N            Store a model-written chapter summary
outline <book> --write             Generate plot outline artifact
map <book>                         Generate full-book map artifact
analyze <book>                     Generate writing-analysis artifact
style <book>                       Distill language style evidence
continue <book> --after-chapter N  Build a continuation package
embed <book>                       Optional semantic index
```

## Privacy

By default, the novel text stays on disk and is indexed locally. The only command that can call an external service is `embed`, and it requires explicit environment configuration. `style` and `continue` read the local index and emit short excerpts with source positions.

