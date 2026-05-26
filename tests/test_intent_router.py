from novel_reader.intent_router import classify_request


def test_status_request():
    result = classify_request("这本书现在读到哪了")
    assert result.intent == "status"
    assert result.confidence > 0.8


def test_style_scene_request():
    result = classify_request("帮我分析战斗场景怎么写")
    assert result.intent == "style"
    assert result.suggested_args["scene"] == "战斗"


def test_continue_request_extracts_controls():
    result = classify_request("接第12章后面续写，短一点，偏悬疑")
    assert result.intent == "continue"
    assert result.suggested_args["after_chapter"] == 12
    assert result.suggested_args["length"] == "short"
    assert result.suggested_args["scene"] == "悬疑"


def test_search_request():
    result = classify_request("找一下小舞献祭")
    assert result.intent == "search"
