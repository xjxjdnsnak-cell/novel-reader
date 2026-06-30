# Tasks

- [x] Task 1: 创建 `src/novel_reader/search.py`，搬迁 15 个函数
  - [x] SubTask 1.1: 新建 `src/novel_reader/search.py`，顶部声明依赖：`from __future__ import annotations`、stdlib（`math`、`json`、`os`、`re`、`sqlite3`、`unicodedata`、`urllib.error`、`urllib.request`）、`pathlib.Path`、`typing.Any`；从 `.storage` 导入 `open_db`、`load_manifest`。**注**：`NovelReaderError` 改为在 `embed_texts` 内 lazy import（`from .cli import NovelReaderError`），而非顶部——这是对原 spec 的偏离修正，因为顶部 import 会导致 `from novel_reader import search` 独立导入时循环失败（cli 反向从 search 取 `cosine` 等名字时 search 尚未初始化完成）。lazy import 仅影响 `embed_texts` 一个函数，且只在抛异常时执行，零性能影响
  - [x] SubTask 1.2: 逐字复制 cli.py 原 lines 522-822 的 15 个函数定义到 search.py：`split_terms`、`snippet`、`like_search`、`fts_query`、`fts_search`、`cosine`、`local_config_path`、`read_local_launcher_config`、`local_qwen_embedding_health`、`local_qwen_embedding_available`、`discover_local_qwen_embedding`、`resolve_embedding_config`、`embed_texts`、`semantic_search`、`search_book`（保持函数顺序、缩进、docstring、错误消息完全一致）
  - [x] SubTask 1.3: 验证 `local_config_path` 中 `Path(__file__).resolve().parents[2]` 在 search.py 里仍解析到项目根目录（search.py 位于 `src/novel_reader/search.py`，`parents[2]` = 项目根，与 cli.py 一致）
  - [x] SubTask 1.4: 单独执行 `python -c "from novel_reader import search; print(search.search_book, search.embed_texts)"` 确认无循环导入错误 ✅

- [x] Task 2: 在 cli.py 添加 re-export import，删除原始 15 个函数定义
  - [x] SubTask 2.1: 在 cli.py 的 storage re-export 区块之后（line 155）新增 `from .search import (...)`，15 个名字 re-export 回 cli 命名空间，带 `# noqa: E402` 注释
  - [x] SubTask 2.2: 删除 cli.py 中原 lines 522-822 这 15 个函数的原始定义（保留 `command_search` 起的命令函数）
  - [x] SubTask 2.3: 确认 cli.py 内部调用点（command_search、command_ask、build_continuation_packet、command_embed、_check_vector_backend）仍用裸名调用，无需改动 ✅

- [x] Task 3: 验证 web_app.py 与外部调用零改动
  - [x] SubTask 3.1: 确认 `web_app.py` 中 `cli.search_book(...)`（line 115）、`cli.get_chunks(...)`（line 107）、`cli.fetch_summary_rows(...)`（line 66）调用不变（re-export 透传）✅
  - [x] SubTask 3.2: 确认 `tests/` 下无任何测试直接 import 这 15 个函数 ✅

- [x] Task 4: 全套回归测试 + 端到端冒烟
  - [x] SubTask 4.1: `python -m pytest -q` 全套通过：**82 passed** ✅
  - [x] SubTask 4.2: 端到端冒烟：ingest ✅ → search --json（8 results, fts+like）✅ → ask --json（8 evidence）✅ → doctor（9 checks, ok=True）✅ → web `/api/status/smoke-book` 200 ✅ → web `/api/search` 200（5 results, 经 cli.search_book re-export）✅
  - [x] SubTask 4.3: `wc -l src/novel_reader/cli.py` = **2342 行**（2625 - 283，接近预估 2325）✅

# Task Dependencies

- Task 2 依赖 Task 1（search.py 先就位，cli.py 才能 re-export）
- Task 3 依赖 Task 2
- Task 4 依赖 Task 1-3 全部完成
