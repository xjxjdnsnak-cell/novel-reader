# Novel Reader

Novel Reader is a Claude Code + OpenCode compatible toolkit for reading long TXT/Markdown novels with a local chapter/chunk index, source-grounded evidence, optional semantic search, style analysis, continuation packages, and governed full-reading sessions.

## Quick Start

Normal users only need import plus the natural-language entrypoint:

```bash
python ./bin/novel-reader ingest path/to/novel.txt
python ./bin/novel-reader do <book_id> "你的需求"
```

Examples:

```bash
python ./bin/novel-reader do <book_id> "这本书现在读到哪了"
python ./bin/novel-reader do <book_id> "找一下主角第一次失败的情节" --semantic
python ./bin/novel-reader do <book_id> "帮我分析战斗场景怎么写"
python ./bin/novel-reader do <book_id> "接第12章后面续写，短一点，偏悬疑"
python ./bin/novel-reader write-next <book_id> --after-chapter 12 --outline "主角潜入北塔" --json
```

Local data is stored under `.novel-reader/<book_id>/`.

## Governed Full Reading

For requests like "精读完整本", "读完整本再分析", or "完整拆解", use a reading session. This prevents an agent from reading only a few chapters and then producing full-book conclusions.

```bash
python ./bin/novel-reader read-session <book_id> --goal full --mode balanced --json
python ./bin/novel-reader read-next <session_id> --json
python ./bin/novel-reader submit-note <session_id> --chapter 1 --text "<structured note>" --json
python ./bin/novel-reader reading-status <session_id> --json
python ./bin/novel-reader finalize-reading <session_id> --json
```

Reading depth:

- `survey`: whole book L1 skim coverage.
- `balanced`: whole book L1 plus key chapters L2.
- `deep`: whole book L1, key chapters L2, anchor/focus chapters L3.

Full-scope reports are guarded:

```bash
python ./bin/novel-reader outline <book_id> --scope full --json
python ./bin/novel-reader analyze <book_id> --scope full
python ./bin/novel-reader continue <book_id> --after-chapter 12 --scope full --json
```

If required coverage is missing, the command refuses full-scope output and returns the current coverage, missing chapters, and the next `read-next` command to run.

`reading-status` exposes three explicit gates:

- `required_coverage_complete`: required L1/L2/L3 notes are complete.
- `finalized`: `finalize-reading` has been successfully run.
- `full_scope_allowed`: both required coverage and finalize are complete.

Use `full_scope_allowed` for new integrations. `final_reports_allowed` is kept as a deprecated compatibility alias for `full_scope_allowed`.

## What It Does

- Indexes TXT/Markdown novels into chapters and chunks.
- Tracks legacy summary coverage and governed L1/L2/L3 reading coverage.
- Answers plot questions with chapter/chunk/line evidence.
- Builds outlines, book maps, writing analysis reports, style evidence, and continuation packages.
- Keeps normal work local-first; semantic search is optional and can use a local Qwen embedding service.

## One-Click Startup

Use the launcher to check or start local Qwen Embedding and then start Claude Code, OpenCode, or only the embedding service.

```powershell
.\bin\start-novel-reader.ps1
```

Useful variants:

```powershell
.\bin\start-novel-reader.ps1 -Client none
.\bin\start-novel-reader.ps1 -Client opencode
.\bin\start-novel-reader.ps1 -NoEmbedding
.\bin\start-novel-reader.ps1 -ModelPath "C:\Users\xsjhxs\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B"
```

## Local Web Console

The optional Flask web console provides import, book list, status, reading, search, Q&A evidence, documents, style analysis, continuation packages, and Claude bridge features.

```powershell
pip install -e .
.\bin\start-web.ps1 -OpenBrowser
```

Default URL:

```text
http://127.0.0.1:8765
```

## Claude Code And OpenCode

Claude Code plugin files live under `.claude-plugin/` and `skills/`. OpenCode adapters live under `.opencode/`.

Daily agent usage should call:

```bash
python ./bin/novel-reader do <book_id> "用户需求" --json
```

For governed full reading, agents must loop through `read-next` and `submit-note`, then wait for `finalize-reading` before issuing `--scope full` reports.

## Advanced Commands

Most users should use `ingest + do`. These lower-level commands remain available for scripts, debugging, and precise control.

```text
ingest <file>                         Import TXT/Markdown and build index
do <book> "<request>"                 Route a natural-language request
read-session <book>                   Create governed full-reading session
read-next <session_id>                Return next required chapter batch
submit-note <session_id> --chapter N  Validate and store governed chapter note
reading-status <session_id>           Show L1/L2/L3 coverage
finalize-reading <session_id>         Allow full reports only when complete
write-next <book>                     Build continuation package plus prose prompt
list                                  List imported books
select [book]                         Select or show the default book
status <book>                         Show coverage and index status
read <book> --chapter N               Read a chapter
read <book> --chunk c0001-001         Read one chunk
search <book> "<query>"               Search source text
ask <book> "<question>"               Build a plot Q&A evidence packet
note <book> --chapter N               Store a legacy chapter summary
outline <book> --scope partial|full   Generate plot outline
map <book> --scope partial|full       Generate book map
analyze <book> --scope partial|full   Generate writing analysis
style <book> --scope partial|full     Distill language style evidence
continue <book> --after-chapter N     Build a continuation package
embed <book>                          Optional semantic index
```

## Semantic Search

Keyword search and SQLite FTS work locally by default.

Current semantic search is deliberately simple:

- Embedding service generates vectors.
- Novel Reader stores vectors in the local SQLite `embeddings` table as `vector_json`.
- Query-time ranking uses local Python cosine similarity over those SQLite-stored vectors.

This version does not integrate an external vector database such as Qdrant, Chroma, FAISS, or Milvus. Status reports this as:

```text
vector_backend: sqlite_cosine
```

Semantic search requires an embedding index and a running OpenAI-compatible embedding endpoint for query vectors.

For local Qwen:

```powershell
$env:NOVEL_READER_EMBED_API_KEY="local"
$env:NOVEL_READER_EMBED_BASE_URL="http://127.0.0.1:8081/v1"
$env:NOVEL_READER_EMBED_MODEL="qwen3-embedding-0.6b"
python ./bin/novel-reader embed <book_id> --provider openai-compatible
```

Then:

```bash
python ./bin/novel-reader do <book_id> "找一下人物动机变化" --semantic
```

TODO backend options:

- `sqlite-vec`
- FAISS
- Qdrant

## Safety Notes

- Plot claims should cite source evidence.
- Full-book conclusions require governed reading coverage when using `--scope full`.
- Continuation writing should start from `continue` or `write-next`; the CLI does not call an external writing model.
- Style distillation outputs transferable original-writing guidance, not direct imitation prompts.
- Do not commit `.novel-reader/`, `.novel-reader-local/`, model paths, API keys, or novel text.
