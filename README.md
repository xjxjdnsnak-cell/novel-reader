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
  bin/start-novel-reader.ps1
  bin/start-web.ps1
  src/novel_reader/
```

The Python CLI is the shared core. Claude Code and OpenCode use thin adapters that call the same commands.

## Requirements

- Python 3.9+
- Flask 3.0+ for the optional local Web console
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

## One-Click Startup

Use the launcher to check or start local Qwen Embedding and then start Claude Code or OpenCode with the right environment variables.

```powershell
.\bin\start-novel-reader.ps1
```

If Windows blocks PowerShell scripts, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\bin\start-novel-reader.ps1
```

Defaults:

- client: `claude`
- Claude permission mode: ask interactively between normal Claude and `claude --dangerously-skip-permissions`
- embedding URL: `http://127.0.0.1:8081/v1`
- model name: `qwen3-embedding-0.6b`
- local config: `.novel-reader-local/config.json`

Other examples:

```powershell
.\bin\start-novel-reader.ps1 -Client opencode
.\bin\start-novel-reader.ps1 -Client none
.\bin\start-novel-reader.ps1 -NoEmbedding
.\bin\start-novel-reader.ps1 -ModelPath "C:\Users\xsjhxs\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B"
.\bin\start-novel-reader.ps1 -Port 8081 -BatchSize 4
.\bin\start-novel-reader.ps1 -ClaudePermissionMode normal
.\bin\start-novel-reader.ps1 -ClaudePermissionMode dangerous
```

`-ClaudePermissionMode dangerous` starts `claude --dangerously-skip-permissions` only after you type `DANGEROUS` to confirm. Use it only in a trusted workspace.

If the model is not found, the launcher asks for a model path. Press Enter to continue without embedding. Keyword search, plot Q&A, writing analysis, style distillation, and continuation packages still work without embedding.

The launcher only connects to `127.0.0.1` by default. Do not put real API keys or private model paths in committed files.

## Two Claude Modes

Novel Reader supports two Claude Code workflows.

Mode 1: start Claude in plugin mode from PowerShell:

```powershell
.\bin\start-claude-plugin.ps1
.\bin\start-claude-plugin.ps1 -ClaudePermissionMode dangerous
.\bin\start-claude-plugin.ps1 -ModelPath "C:\Users\xsjhxs\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B"
```

This checks or starts local Qwen Embedding, then opens Claude Code with the Novel Reader plugin available.

Mode 2: open the Web panel from inside Claude Code:

```text
/novel-web
/novel-web --port 8770
/novel-web --no-embedding
/novel-web --dangerous
```

The `/novel-web` command starts the local Web console in the background, opens the browser panel, and enables the Claude bridge for the page. If the requested port is busy, the launcher picks the next available local port and prints the final URL.

## Local Web Console

The Web console is a local browser UI for common work: import, book list, status, reading, search, plot Q&A evidence packages, outline/map/analyze, language style distillation, continuation packages, and embedding checks.

Install the package or Flask first:

```powershell
pip install -e .
```

Then start the console:

```powershell
.\bin\start-web.ps1 -OpenBrowser
```

Open manually if needed:

```text
http://127.0.0.1:8765
```

Other examples:

```powershell
.\bin\start-web.ps1
.\bin\start-web.ps1 -Port 8770 -OpenBrowser
.\bin\start-web.ps1 -NoEmbedding
.\bin\start-web.ps1 -ModelPath "C:\Users\xsjhxs\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B"
.\bin\start-web.ps1 -EnableClaudeChat -OpenBrowser
```

The console only binds to `127.0.0.1` by default. If local Qwen Embedding is already running on `8081`, semantic features are enabled automatically. If it is not running, the launcher tries to start it from `-ModelPath`, `QWEN_EMBED_MODEL_PATH`, saved `.novel-reader-local/config.json`, or the common ModelScope cache path. If no model is found, the page still supports keyword search and all local evidence-package workflows.

To start Qwen Embedding before opening the Web console:

```powershell
.\bin\start-novel-reader.ps1 -Client none
.\bin\start-web.ps1 -OpenBrowser
```

Web document viewer:

- The Documents tab lists generated `maps/`, `reports/`, `styles/`, `continuations/`, and `summaries/` files.
- Markdown files can be previewed in the browser, viewed as source, or downloaded.
- The web API refuses absolute paths and `..` traversal, so it cannot read arbitrary local files.

Claude bridge:

```powershell
.\bin\start-web.ps1 -EnableClaudeChat -ClaudeMode both -OpenBrowser
.\bin\start-web.ps1 -EnableClaudeChat -ClaudePermissionMode dangerous -OpenBrowser
```

The Claude tab calls the local `claude` CLI with fixed command templates:

- one-shot mode: `claude -p --output-format json <prompt>`
- continue mode: `claude -c -p --output-format json <prompt>`

Dangerous mode adds `--dangerously-skip-permissions` only after the launcher asks you to type `DANGEROUS`. Attached documents and evidence packages may be sent through Claude Code, so keep the server bound to `127.0.0.1`.

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
