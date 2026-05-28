from pathlib import Path
import html

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "novel_reader" / "web_static"


def read_static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def visible_html() -> str:
    return html.unescape(read_static("index.html"))


def test_web_console_has_workflow_layout_and_predict_panel():
    raw = read_static("index.html")
    visible = visible_html()

    for marker in (
        'id="libraryPanel"',
        'id="readingProgressPanel"',
        'id="activeTaskPanel"',
        'id="taskHistory"',
        'id="resultPreview"',
        'id="documentsPanel"',
        'id="predictCard"',
        'id="predictBtn"',
        'id="reportScope"',
        'id="l1Progress"',
        'id="fullScopeState"',
        'id="autoSurveyBtn"',
        'id="autoReadingDepth"',
        'id="claudeCacheStatus"',
        'id="pauseAutoSurveyBtn"',
        'id="resumeAutoSurveyBtn"',
        'id="embedBtn"',
    ):
        assert marker in raw

    assert "构建语义索引" in visible
    assert "常用操作" in visible
    assert "当前任务" in visible
    assert "Claude 自动阅读（实验性）" in visible
    assert "阶段性范围" in visible
    assert "全书范围" in visible


def test_web_console_js_has_task_state_and_renderers():
    js = read_static("app.js")

    for symbol in (
        "taskState",
        "startTask",
        "updateTaskProgress",
        "addTaskStep",
        "finishTask",
        "failTask",
        "renderActiveTask",
        "renderTaskHistory",
        "withButtonLoading",
        "renderPredictionPacket",
        "renderError",
        "runPredict",
        "autoSurveyState",
        "startAutoReading",
        "startAutoSurveyReading",
        "pauseAutoSurveyReading",
        "resumeAutoSurveyReading",
        "buildReadingNotePrompt",
        "buildL1NotePrompt",
        "buildL2NotePrompt",
        "buildL3NotePrompt",
        "clipTextForPrompt",
        "extractClaudeNoteText",
        "extractClaudeUsage",
        "warmClaudeCacheIfNeeded",
        "translateDocumentCategory",
        "translateAutoSurveyStatus",
    ):
        assert symbol in js

    assert "\\u5168\\u4e66\\u62a5\\u544a" in js
    assert "\\u7f6e\\u4fe1\\u5ea6" in js
    assert "\\u6700\\u8fd1\\u4efb\\u52a1" in js
    assert 'scope:$("reportScope")?.value||"partial"' in js.replace(" ", "")


def test_web_console_static_text_has_no_broken_question_mark_or_mojibake_labels():
    combined = read_static("index.html") + "\n" + read_static("app.js")

    forbidden = (
        "????",
        "???...",
        "?? JSON",
        "???????",
        "????????",
        "`${task.name} ? ${translateTaskType",
        "鍏",
        "璇",
        "闃",
        "褰",
        "鎼",
        "绔",
        "棰",
        "�",
    )
    for marker in forbidden:
        assert marker not in combined


def test_web_console_css_has_progress_task_and_error_styles():
    css = read_static("styles.css")

    for selector in (
        ".console-grid",
        ".progress-track",
        ".progress-fill",
        ".task-log",
        ".task-history",
        ".active-task",
        ".error-card",
        ".success-card",
        ".warning-card",
        ".prediction-card",
        ".json-details",
        ".auto-survey-controls",
        ".auto-survey-state",
        ".cache-meter",
        ".usage-summary",
        ".reading-level-badge",
    ):
        assert selector in css
