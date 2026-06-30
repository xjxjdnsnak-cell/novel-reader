"""锁定前端静态文件中已知的正确中文文案。

本测试与 test_web_static_console.py 互补：
- test_web_static_console.py 只做粗粒度检查（id 存在、JS 符号存在、乱码黑名单、CSS 选择器）。
- 本测试逐条锁定 index.html / app.js 中真实存在的中文文案（按钮文字、面板标题、
  状态标签、错误提示、任务名称、结果卡片标题等），一旦有人改坏或删掉某条文案即可定位。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "novel_reader" / "web_static"


def read_static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# index.html 中文文案锁定（面板标题、按钮文字、状态标签、占位提示等）
# ---------------------------------------------------------------------------

def test_index_html_locks_brand_and_header():
    html = read_static("index.html")
    for marker in (
        "Novel Reader 本地控制台",
        "本地长篇小说阅读、证据包与 Claude 协作控制台",
        "Embedding 未检查",
        "Claude 未检查",
        "未选择书籍",
    ):
        assert marker in html, f"missing chinese text: {marker!r}"


def test_index_html_locks_library_panel_titles():
    html = read_static("index.html")
    for marker in (
        "书库与精读进度",
        "选择当前书，检查 full-scope 是否解锁。",
        "精读状态",
        "L1 快速覆盖",
        "L2 标准阅读",
        "L3 深度精读",
        "导入 TXT/Markdown 后开始。",
        "未创建 reading session",
    ):
        assert marker in html, f"missing chinese text: {marker!r}"


def test_index_html_locks_auto_survey_section():
    html = read_static("index.html")
    for marker in (
        "Claude 自动阅读（实验性）",
        "Claude 缓存：尚未调用",
        "手动提交章节笔记",
        "按 session 要求自动",
        "只跑 L1",
        "跑到 L2",
        "跑到 L3",
        "自动阅读",
    ):
        assert marker in html, f"missing chinese text: {marker!r}"


def test_index_html_locks_workflow_cards():
    html = read_static("index.html")
    for marker in (
        "工作流队列",
        "常用操作集中在这里",
        "检索 / 问答",
        "关键词、问题或剧情线索",
        "问答证据包",
        "阶段性范围",
        "全书范围",
        "全书范围需要完成精读并 finalize。",
        "风格分析",
        "预测后续剧情",
        "这本未完结小说后面可能怎么发展？",
        "伏笔回收",
        "续写任务包",
        "构建语义索引",
        "生成预测包",
        "生成任务包",
    ):
        assert marker in html, f"missing chinese text: {marker!r}"


def test_index_html_locks_task_and_artifact_panels():
    html = read_static("index.html")
    for marker in (
        "当前任务",
        "当前没有任务运行",
        "当前产物",
        "生成预测包、续写包或报告后会出现在这里。",
        "发送给 Claude",
        "打开文档",
        "复制摘要",
        "查看 JSON",
        "无产物",
        "等待操作",
    ):
        assert marker in html, f"missing chinese text: {marker!r}"


def test_index_html_locks_documents_and_chat_panels():
    html = read_static("index.html")
    for marker in (
        "暂无最近生成文档",
        "全部分类",
        "剧情预测",
        "分析报告",
        "剧情地图",
        "风格文档",
        "章节摘要",
        "未选择文档",
        "点击文档后预览。",
        "发送文档给 Claude",
        "Claude 对话",
        "可附加当前产物、文档或证据。",
        "连续会话",
        "单次对话",
        "未附加上下文",
        "向 Claude 追问",
        "附加当前产物",
        "附加当前文档",
    ):
        assert marker in html, f"missing chinese text: {marker!r}"


# ---------------------------------------------------------------------------
# app.js 中文文案锁定（任务状态、错误提示、结果卡片标题、按钮 loading 文案等）
# ---------------------------------------------------------------------------

def test_app_js_locks_task_status_labels():
    js = read_static("app.js")
    for marker in (
        "运行中",
        "成功",
        "失败",
        "空闲",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_auto_survey_status_labels():
    js = read_static("app.js")
    for marker in (
        "已停止",
        "已暂停",
        "暂停中",
        "出错",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_task_progress_and_step_labels():
    js = read_static("app.js")
    for marker in (
        "准备中...",
        "任务已创建",
        "任务完成",
        "任务失败",
        "完成",
        "最近任务",
        "等待操作",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_artifact_titles():
    js = read_static("app.js")
    for marker in (
        "剧情预测包",
        "续写任务包",
        "风格证据包",
        "剧情大纲",
        "剧情地图",
        "写作分析",
        "问答证据包",
        "搜索证据",
        "当前文档",
        "当前产物",
        "无产物",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_result_card_titles():
    js = read_static("app.js")
    for marker in (
        "当前剧情状态",
        "最新章节",
        "摘要覆盖",
        "向量后端",
        "置信度",
        "暂无预测。",
        "没有证据片段。",
        "最近上下文",
        "剧情证据",
        "风格画像",
        "短引文证据",
        "文档已生成",
        "操作完成。",
        "书籍状态",
        "语义索引构建完成",
        "阅读会话已创建",
        "下一章阅读包",
        "章节笔记已提交",
        "Full-scope 已解锁",
        "导入完成",
        "搜索结果",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_error_messages():
    js = read_static("app.js")
    for marker in (
        "响应不是 JSON。",
        "请先选择一本书。",
        "请先创建 reading session。",
        "请输入搜索或问答内容。",
        "Claude 网页桥接未启用，请用 -EnableClaudeChat 重启 Web。",
        "未找到 claude 命令。",
        "Claude 没有返回可提交的笔记。",
        "接在章节后和接在 chunk 后只能填一个。",
        "请输入要发送给 Claude 的内容。",
        "当前没有可发送的产物。",
        "当前没有产物。",
        "当前没有打开文档。",
        "当前产物没有关联文档。",
        "操作失败",
        "未知错误",
        "下一步：",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_status_pill_and_empty_state_labels():
    js = read_static("app.js")
    for marker in (
        "Embedding 未配置",
        "Embedding 可用",
        "Embedding 不可用",
        "Claude 未启用",
        "Claude 可用",
        "Claude 未找到",
        "未在 PATH 中找到 claude 命令。",
        "暂无书籍。先导入 TXT/Markdown。",
        "暂无生成文档。",
        "未选择书籍",
        "未选择文档",
        "点击文档后预览。",
        "未附加上下文",
        "还没有对话。生成任务包后可一键发送给 Claude。",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_button_loading_texts():
    js = read_static("app.js")
    for marker in (
        "创建中...",
        "读取中...",
        "提交中...",
        "导入中...",
        "搜索中...",
        "生成中...",
        "预测中...",
        "构建中...",
        "阅读中...",
        "finalize...",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_task_names():
    js = read_static("app.js")
    for marker in (
        "生成后续剧情预测",
        "生成续写任务包",
        "构建语义索引",
        "风格分析",
        "导入小说",
        "提交章节笔记",
        "读取章节",
        "发送给 Claude",
        "Claude 自动阅读（实验性）",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_note_prompt_fields():
    js = read_static("app.js")
    for marker in (
        "人物与动机",
        "情节因果",
        "伏笔/回收",
        "设定/地点/势力",
        "时间线",
        "写作观察",
        "证据块",
        "请只根据下面原文生成 Novel Reader",
        "不要输出 JSON，只输出可提交的 Markdown 笔记。",
        "已截断",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"


def test_app_js_locks_cache_status_and_misc_toasts():
    js = read_static("app.js")
    for marker in (
        "本章命中率",
        "Claude 未返回缓存指标",
        "Claude 缓存",
        "已切换为阶段性范围。",
        "产物摘要已复制。",
        "刚刚生成：",
        "可交给 Claude 生成章节笔记。",
        "可交给 Claude 生成原创转写建议。",
        "Claude 已返回空响应。",
        "已附加：",
        "刷新状态",
        "切换阶段性范围",
        "等待后端返回",
        "准备上下文",
    ):
        assert marker in js, f"missing chinese text: {marker!r}"
