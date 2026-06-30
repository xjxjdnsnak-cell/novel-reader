# 抽取 search+embedding 集群到独立模块 Spec

## Why

`cli.py` 仍是 2625 行的 god module。上一轮 spec（decouple-web-from-cli-stdout）在 checklist 第 22 条明确写下"未拆 search.py / embedding.py / renderers.py（本轮明确不做）"，把这块留作下一步。现在 Web 解耦已稳定（82/82 测试通过），按用户既定路线"先诚实化 → 再模块化 → 最后做深 governed reading"，模块化阶段的下一个最小切片就是把 search+embedding 集群从 cli.py 抽出，让 cli.py 减重约 300 行，并为后续 renderers/style/continuation 的拆分建立可复用的抽取范式。

## What Changes

- 新增 `src/novel_reader/search.py`，承接 cli.py 中 lines 522-822 的 15 个函数：`split_terms`、`snippet`、`like_search`、`fts_query`、`fts_search`、`cosine`、`local_config_path`、`read_local_launcher_config`、`local_qwen_embedding_health`、`local_qwen_embedding_available`、`discover_local_qwen_embedding`、`resolve_embedding_config`、`embed_texts`、`semantic_search`、`search_book`
- `cli.py` 在 NovelReaderError 定义之后（与 storage 导入同区位，约 line 154）新增 `from .search import (...)`，把这 15 个名字 re-export 回 cli 命名空间，保证 `cli.search_book` / `cli.embed_texts` 等外部调用（web_app.py、tests）零改动
- `cli.py` 内部调用点（command_search、command_ask、build_continuation_packet、command_embed、_check_vector_backend）不改变写法，继续用裸名 `search_book(...)` 等——因为 re-export 后名字仍在 cli 命名空间
- 删除 cli.py 中这 15 个函数的原始定义（约 300 行）
- `search.py` 的依赖：从 `.cli` 导入 `NovelReaderError`（循环导入安全：cli.py 在 NovelReaderError 定义后才 import search，search 顶部 `from .cli import NovelReaderError` 时该类已就位）；从 `.storage` 导入 `open_db`、`load_manifest`
- 不改变任何函数签名、返回结构、错误消息、行为
- 不拆 embedding 为独立文件（search_book 与 semantic_search 强耦合，拆开会产生循环依赖，本轮明确不做）

## Impact

- Affected specs: 无前置 spec 依赖；继 `decouple-web-from-cli-stdout` 之后的模块化第二步
- Affected code:
  - 新增：`src/novel_reader/search.py`
  - 修改：`src/novel_reader/cli.py`（删 ~300 行定义 + 加 re-export import）
  - 不动：`src/novel_reader/web_app.py`（继续用 `cli.search_book`，re-export 透明）、`src/novel_reader/storage.py`、所有 `command_*` 函数体、所有测试
- 风险：低。纯搬迁 + re-export，行为零变更。最大风险点是循环导入，已通过"cli 在 NovelReaderError 定义后 import search"规避

## ADDED Requirements

### Requirement: search+embedding 模块独立
系统 SHALL 提供 `novel_reader.search` 模块，承载所有"按查询找 chunk"相关逻辑（关键词分词、LIKE 检索、FTS5 检索、embedding 健康探测、embedding 配置解析、向量检索、混合检索入口）。

#### Scenario: 模块可独立导入
- **WHEN** 执行 `from novel_reader import search` 
- **THEN** 不触发循环导入错误，`search.search_book` / `search.embed_texts` / `search.discover_local_qwen_embedding` 等均可访问

#### Scenario: cli 命名空间向后兼容
- **WHEN** 外部代码（web_app.py、tests）调用 `cli.search_book(...)` 或 `cli.embed_texts(...)`
- **THEN** 行为与抽取前完全一致（re-export 透传）

## MODIFIED Requirements

### Requirement: cli.py 模块体积
`cli.py` SHALL 在移除 search+embedding 集群后减至约 2325 行（2625 - 300），且所有 command_* 函数与 dispatch_command 行为不变。

#### Scenario: 回归测试全过
- **WHEN** 执行 `python -m pytest -q`
- **THEN** 82 个测试全部通过（数量与抽取前一致，不新增也不删测试）

#### Scenario: 端到端检索行为不变
- **WHEN** 通过 CLI 执行 `search <book> <query> --json` 或 `ask <book> <question> --json`
- **THEN** 返回的 JSON 结构、score、source、snippet 字段与抽取前一致

## REMOVED Requirements

无。本轮纯搬迁，不删除任何能力。
