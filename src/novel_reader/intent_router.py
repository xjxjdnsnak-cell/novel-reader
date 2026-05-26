from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


INTENTS = {
    "status",
    "read",
    "search",
    "ask",
    "outline",
    "map",
    "analyze",
    "style",
    "continue",
    "embed",
    "unknown",
}

SCENES = ("战斗", "悬疑", "感情", "日常", "说明")


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    reason: str
    suggested_args: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def extract_scene(text: str) -> str | None:
    for scene in SCENES:
        if scene in text:
            return scene
    aliases = {
        "battle": "战斗",
        "fight": "战斗",
        "action": "战斗",
        "suspense": "悬疑",
        "mystery": "悬疑",
        "romance": "感情",
        "emotion": "感情",
        "daily": "日常",
        "slice of life": "日常",
        "exposition": "说明",
        "explain": "说明",
    }
    lowered = normalize_text(text)
    for key, value in aliases.items():
        if key in lowered:
            return value
    return None


def extract_length(text: str) -> str | None:
    lowered = normalize_text(text)
    if any(word in text for word in ("短一点", "短些", "简短", "短篇", "短的")) or "short" in lowered:
        return "short"
    if any(word in text for word in ("长一点", "长些", "详细", "长篇", "展开写")) or "long" in lowered:
        return "long"
    if "medium" in lowered or "中等" in text:
        return "medium"
    return None


def extract_after_chapter(text: str) -> int | None:
    patterns = (
        r"第\s*(\d+)\s*章\s*(?:后|之后|后面|往后|续)",
        r"(\d+)\s*章\s*(?:后|之后|后面|往后|续)",
        r"after[-\s_]*chapter\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def extract_chapter(text: str) -> int | None:
    after = extract_after_chapter(text)
    if after is not None:
        return after
    patterns = (r"第\s*(\d+)\s*章", r"chapter\s*(\d+)")
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def extract_chunk(text: str) -> str | None:
    match = re.search(r"\bc\d{4,}-\d{3,}\b", text, re.I)
    return match.group(0) if match else None


def clean_query(text: str) -> str:
    cleaned = text.strip()
    prefixes = (
        "帮我",
        "请",
        "给我",
        "查一下",
        "找一下",
        "搜索",
        "检索",
        "分析",
        "梳理",
        "生成",
        "看看",
    )
    for prefix in prefixes:
        cleaned = re.sub(rf"^{re.escape(prefix)}", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or text.strip()


def base_args(text: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    lowered = normalize_text(text)
    if any(word in text for word in ("完整", "全书", "整本", "读完整本", "完整拆解", "精读完整本")) or any(
        word in lowered for word in ("full-scope", "full scope", "whole book", "complete reading")
    ):
        args["scope"] = "full"
    scene = extract_scene(text)
    if scene:
        args["scene"] = scene
    length = extract_length(text)
    if length:
        args["length"] = length
    after_chapter = extract_after_chapter(text)
    if after_chapter is not None:
        args["after_chapter"] = after_chapter
    chapter = extract_chapter(text)
    if chapter is not None:
        args["chapter"] = chapter
    chunk = extract_chunk(text)
    if chunk:
        args["chunk"] = chunk
    return args


def result(intent: str, confidence: float, reason: str, text: str, **extra: Any) -> IntentResult:
    args = base_args(text)
    args.update({key: value for key, value in extra.items() if value is not None})
    return IntentResult(intent=intent, confidence=confidence, reason=reason, suggested_args=args)


def classify_request(text: str) -> IntentResult:
    raw = text.strip()
    lowered = normalize_text(raw)
    query = clean_query(raw)

    if not raw:
        return IntentResult("unknown", 0.0, "empty request", {})

    if any(word in raw for word in ("embedding", "语义索引", "向量", "嵌入", "建索引")) or "embed" in lowered:
        return result("embed", 0.91, "embedding/indexing keyword matched", raw)

    if any(word in raw for word in ("续写", "接着写", "继续写", "往后写", "下一章", "写下一段")) or any(
        word in lowered for word in ("continue", "write next", "next chapter")
    ):
        outline = query
        return result("continue", 0.93, "continuation keyword matched", raw, outline=outline)

    if any(word in raw for word in ("风格", "文风", "语言", "场景怎么写", "写法", "蒸馏")) or any(
        word in lowered for word in ("style", "voice", "tone")
    ):
        return result("style", 0.9, "style keyword matched", raw)

    if any(word in raw for word in ("写作分析", "优缺点", "节奏", "人物弧光", "冲突", "伏笔", "修改建议")) or "analyze" in lowered:
        return result("analyze", 0.89, "analysis keyword matched", raw)

    if any(word in raw for word in ("人物表", "事件表", "时间线", "地点表", "势力表", "设定表", "地图", "图谱")) or "map" in lowered:
        return result("map", 0.88, "map keyword matched", raw)

    if any(word in raw for word in ("梳理", "大纲", "主线", "支线", "剧情线", "章节整理", "概括全书")) or any(
        word in lowered for word in ("outline", "plot summary")
    ):
        return result("outline", 0.88, "outline keyword matched", raw)

    if any(word in raw for word in ("读到哪", "状态", "进度", "覆盖率", "有哪些书", "索引状态")) or "status" in lowered:
        return result("status", 0.92, "status/progress keyword matched", raw)

    if any(word in raw for word in ("读取", "读第", "看第", "原文", "章节内容")) or lowered.startswith("read"):
        return result("read", 0.86, "read keyword matched", raw)

    if any(word in raw for word in ("为什么", "是谁", "哪里", "哪一章", "发生了什么", "是否", "怎么回事", "问答")) or any(
        word in lowered for word in ("why", "who", "where", "what happened", "ask", "?")
    ):
        return result("ask", 0.84, "question keyword matched", raw, question=query)

    if any(word in raw for word in ("找", "查", "搜索", "检索", "相关片段", "关键词")) or any(
        word in lowered for word in ("search", "find", "lookup")
    ):
        return result("search", 0.82, "search keyword matched", raw, query=query)

    return IntentResult(
        intent="unknown",
        confidence=0.15,
        reason="no routing rule matched",
        suggested_args={"query": query},
    )
