from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "novel_reader" / "web_static"


def read_static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_web_console_has_workflow_layout_and_predict_panel():
    html = read_static("index.html")

    assert 'id="libraryPanel"' in html
    assert 'id="readingProgressPanel"' in html
    assert 'id="activeTaskPanel"' in html
    assert 'id="resultPreview"' in html
    assert 'id="documentsPanel"' in html
    assert 'id="predictCard"' in html
    assert 'id="predictBtn"' in html
    assert 'id="l1Progress"' in html
    assert 'id="fullScopeState"' in html
    assert 'id="autoSurveyBtn"' in html
    assert 'id="autoReadingDepth"' in html
    assert 'id="claudeCacheStatus"' in html
    assert 'id="pauseAutoSurveyBtn"' in html
    assert 'id="resumeAutoSurveyBtn"' in html
    assert 'id="embedBtn"' in html
    assert "构建语义索引" in html
    assert "常用操作" in html
    assert "当前任务" in html


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

    assert "全书报告：已解锁" in js
    assert "置信度" in js


def test_web_console_css_has_progress_task_and_error_styles():
    css = read_static("styles.css")

    for selector in (
        ".console-grid",
        ".progress-track",
        ".progress-fill",
        ".task-log",
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
