# Tasks

- [x] Task 1: 抽出 `dispatch_command` helper 并改造 `main` 分发层
  - [x] SubTask 1.1: 在 `cli.py` 新增 `dispatch_command(args) -> int`，检测 `command_*(args)` 返回类型：dict → `print_json(data)` + 返回 0；int → 直接返回；None → 返回 0
  - [x] SubTask 1.2: `main` 改为调用 `dispatch_command(args)` 替代直接 `args.func(args)`，保留 `NovelReaderJsonError` / `(NovelReaderError, ValueError)` 的 except 分支不变
  - [x] SubTask 1.3: 新增测试 `tests/test_dispatch.py`，覆盖"返回 dict / 返回 int / 返回 None / 抛 NovelReaderJsonError"四种情况

- [x] Task 2: 改造 JSON 输出型 `command_*` 函数为返回 dict
  - [x] SubTask 2.1: `command_ingest` —— 把 `print_json(...)` 改为 `return {...}`，保留 `--json` 时由 dispatch 打印；非 JSON 模式（如果有文本输出）返回 int 0
  - [x] SubTask 2.2: `command_list` / `command_status` / `command_read` / `command_search` / `command_ask` / `command_note` —— 同样改造
  - [x] SubTask 2.3: `command_read_session` / `command_read_next` / `command_submit_note` / `command_reading_status` / `command_finalize_reading` —— 同样改造
  - [x] SubTask 2.4: `command_outline` / `command_map` / `command_analyze` / `command_style` —— 同样改造（注意非 JSON 模式返回 int 0）
  - [x] SubTask 2.5: `command_predict` / `command_continue` / `command_do` / `command_write_next` / `command_embed` —— 同样改造
  - [x] SubTask 2.6: `command_doctor` —— 已返回 int 0，但 JSON 模式当前自己 `print_json`；改为返回 dict，dispatch 打印
  - [x] SubTask 2.7: `command_select` —— 非 JSON 模式返回 int 0 保持不变；JSON 模式返回 dict
  - [x] SubTask 2.8: 跑 `pytest` 确认 CLI 子进程测试（`test_cli_unified_entry.py` / `test_prediction.py` 等通过 subprocess 调 CLI 的）全部通过

- [x] Task 3: 重写 `web_app.run_command_json` 为直接调用
  - [x] SubTask 3.1: 删除 `redirect_stdout` + `json.loads(text)` 链路
  - [x] SubTask 3.2: 改为 `result = func(args); if isinstance(result, dict): return result; return {"ok": True}`
  - [x] SubTask 3.3: 保留 `NovelReaderJsonError` 的 raise 透传（web 层有自己的 except）
  - [x] SubTask 3.4: 跑 `tests/test_web_reading_api.py` / `test_web_claude_bridge.py` 确认 web API 行为不变

- [x] Task 4: 验证 stdout 捕获链路彻底移除
  - [x] SubTask 4.1: `grep -n "redirect_stdout" src/novel_reader/web_app.py` 应无结果
  - [x] SubTask 4.2: 确认 `web_app.py` 顶部 `from contextlib import redirect_stdout` 已删除
  - [x] SubTask 4.3: 手工在某个 `command_*` 里临时加 `print("DEBUG")` 验证不影响 web API 返回（验证后删除 debug print）

- [x] Task 5: 新增前端中文文案锁定测试
  - [x] SubTask 5.1: 新建 `tests/test_web_static_text_lock.py`
  - [x] SubTask 5.2: 从 `index.html` 提取已知正确中文文案（按钮、面板标题、状态标签）逐条 `assert ... in html`
  - [x] SubTask 5.3: 从 `app.js` 提取已知正确中文文案（任务状态、置信度、错误提示）逐条 `assert ... in js`
  - [x] SubTask 5.4: 测试覆盖至少 20 条文案，每条独立断言，失败时报错能定位是哪条缺失
  - [x] SubTask 5.5: 跑 `pytest tests/test_web_static_text_lock.py` 全过

- [x] Task 6: 全套回归测试
  - [x] SubTask 6.1: `pytest` 全套通过（应 ≥ 57 + 新增 dispatch + text_lock 测试数）
  - [x] SubTask 6.2: 端到端冒烟：ingest → ask → predict --llm --write → doctor → web 启动 health 检查

# Task Dependencies

- Task 2 依赖 Task 1（dispatch 层先就位，command_* 才能安全返回 dict）
- Task 3 依赖 Task 2（web 层直接调函数拿 dict，前提是函数确实返回 dict）
- Task 4 依赖 Task 3
- Task 5 与 Task 1-4 无依赖，可并行
- Task 6 依赖 Task 1-5 全部完成
