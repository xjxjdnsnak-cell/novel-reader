# Web 层解耦 stdout 捕获 + 前端中文文案锁定 Spec

## Why

上一轮"稳定化与诚实化修复"完成后，`web_app.py` 仍通过 `redirect_stdout` 捕获 `cli.command_*` 的 `print_json` 输出再 `json.loads` 解析。这迫使每个命令必须用 `print` 输出 JSON、且 `argparse.Namespace` 必须从外部手工拼装。任何调试用的 `print` 都会破坏 JSON 解析，且命令函数无法被直接单测。

同时，前端 `index.html` / `app.js` 的中文文案只在 `test_web_static_console.py` 里有 4 个粗粒度测试和 1 个乱码黑名单测试，缺少"已知正确文案锁定"——一旦有人改坏某个面板标题（如把"当前产物"改成乱码），现有测试不一定能拦住。

本轮目标：让 `command_*` 函数返回 dict（CLI 层负责 print），Web 层直接调函数拿 dict；并补一组中文文案锁定测试，防止已知正确文案被改坏。**不重构 search/embedding/renderers，不动命令行为。**

## What Changes

- 新增 `command_*` 的 dict 返回路径：每个 `command_*` 内部把"要打印的 dict"先返回，由统一的 `dispatch` 层决定是 `print_json` 还是直接交给调用方。
- Web 层 `run_command_json` 改为直接调用 `command_*(args)` 拿 dict，删除 `redirect_stdout` + `json.loads` 链路。
- 保留 `print_json` 作为 CLI 出口；`command_*` 不再自己 `print_json`，而是返回 `(dict, return_code)` 或通过一个轻量 helper。
- 新增 `tests/test_web_static_text_lock.py`：把 `index.html` / `app.js` 里已知的正确中文文案（按钮、面板标题、状态文案）逐条锁定，任何一条被改坏即测试失败。
- **不**改 `command_*` 的外部行为、参数、JSON schema。
- **不**改 OpenCode 适配层。
- **不**拆 search.py / embedding.py / renderers.py。

## Impact

- Affected specs: 无（项目无既有 spec）
- Affected code:
  - `src/novel_reader/cli.py` —— `command_*` 系列函数改为返回 dict；`main` / argparse 分发层负责 print
  - `src/novel_reader/web_app.py` —— `run_command_json` 重写为直接调用，删除 `redirect_stdout`
  - `tests/test_web_*.py` —— 现有 web API 测试应继续通过（行为不变）
  - `tests/test_web_static_text_lock.py` —— 新增

## ADDED Requirements

### Requirement: 命令函数返回结构化数据

CLI 命令函数（`command_ingest` / `command_status` / `command_ask` / `command_predict` / `command_continue` / `command_outline` / `command_map` / `command_analyze` / `command_style` / `command_read` / `command_search` / `command_note` / `command_list` / `command_select` / `command_read_session` / `command_read_next` / `command_submit_note` / `command_reading_status` / `command_finalize_reading` / `command_do` / `command_write_next` / `command_embed` / `command_doctor`）SHALL 返回一个结构化结果（dict 或 `(dict, int)`），而不是直接 `print_json` 到 stdout。

CLI 入口（`main` 或 argparse 分发层）SHALL 负责把返回的 dict 通过 `print_json` 输出。

#### Scenario: CLI 直接调用打印 JSON

- **WHEN** 用户运行 `novel-reader status <book> --json`
- **THEN** CLI 入口拿到 `command_status` 返回的 dict，调用 `print_json` 输出到 stdout
- **AND** 退出码为 0

#### Scenario: Web 层直接调用拿 dict

- **WHEN** `web_app.run_command_json` 调用 `cli.command_status(args)`
- **THEN** 直接拿到返回的 dict
- **AND** 不经过 `redirect_stdout` / `json.loads`
- **AND** 命令函数内部的任何调试 `print` 不会破坏返回值

#### Scenario: 命令失败抛 NovelReaderJsonError

- **WHEN** 命令函数遇到受控失败（如 full-scope guard 拦截）
- **THEN** 仍抛 `NovelReaderJsonError`（携带 payload）
- **AND** CLI 入口和 Web 层各自的 except 分支继续按现有方式处理

### Requirement: Web 层不再捕获 stdout

`web_app.run_command_json` SHALL 直接调用命令函数拿返回值，不再使用 `contextlib.redirect_stdout` + `json.loads(text)` 的解析路径。

#### Scenario: 命令返回 dict

- **WHEN** 命令函数返回 dict
- **THEN** `run_command_json` 原样返回该 dict

#### Scenario: 命令返回 None（无 JSON 输出的命令）

- **WHEN** 命令函数返回 None 或空
- **THEN** `run_command_json` 返回 `{"ok": True}`（保持现有兼容行为）

### Requirement: 前端中文文案锁定测试

系统 SHALL 提供一组测试，把 `index.html` / `app.js` 中已知的正确中文文案逐条锁定。任何一条文案被删除、改成乱码、或被替换为不同含义的词，测试 SHALL 失败。

#### Scenario: 已知文案存在

- **WHEN** 运行 `test_web_static_text_lock`
- **THEN** `index.html` 包含"当前产物""发送给 Claude""Claude 对话"等已知文案
- **AND** `app.js` 包含"最近任务""置信度"等已知文案

#### Scenario: 文案被改坏

- **WHEN** 有人把"当前产物"改成乱码或删除
- **THEN** 对应断言失败，测试报告指出缺失的文案

## MODIFIED Requirements

### Requirement: `command_*` 函数签名

`command_*` 函数 SHALL 返回 `int` 退出码（兼容旧 main 分发）或 dict（新行为）。为避免歧义，统一约定：

- 返回 `int`：表示无 JSON 输出，退出码即返回值（如 `command_select` 非 JSON 模式）
- 返回 `dict`：表示有 JSON 输出，CLI 入口负责 `print_json(data)` 并返回 0
- 返回 `None`：等同于返回 0 且无输出

CLI `main` 分发层 SHALL 检测返回类型并正确处理。

#### Scenario: 返回 dict

- **WHEN** `command_status` 返回 `{"ok": True, ...}`
- **THEN** `main` 调用 `print_json` 输出，退出码 0

#### Scenario: 返回 int

- **WHEN** `command_select` 非 JSON 模式返回 0
- **THEN** `main` 不打印 JSON，退出码即返回的 int

## REMOVED Requirements

无。本轮不删除任何现有能力。

## 非目标（明确不做）

- 不拆 `cli.py` 为 search.py / embedding.py / renderers.py（留给下一轮）
- 不改 `command_*` 的参数、JSON schema、错误消息
- 不动 OpenCode 适配层
- 不动 `intent_router`
- 不重写前端 JS
