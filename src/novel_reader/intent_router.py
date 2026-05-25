from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

INTENT_STATUS = "status"
INTENT_READ = "read"
INTENT_SEARCH = "search"
INTENT_ASK = "ask"
INTENT_OUTLINE = "outline"
INTENT_MAP = "map"
INTENT_ANALYZE = "analyze"
INTENT_STYLE = "style"
INTENT_CONTINUE = "continue"
INTENT_EMBED = "embed"
INTENT_UNKNOWN = "unknown"

STYLE_SCENES = ("战斗", "悬疑", "感情", "日常", "说明")
CONTINUATION_LENGTHS = ("short", "medium", "long")


@dataclass
class IntentResult:
    intent: str
    confidence: float
    reason: str
    suggested_args: dict[str, Any]


def _extract_chapter_number(text: str) -> int | None:
    match = re.search(r"(?:第|chapter\s*)?(\d+)\s*章", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_scene(text: str) -> str | None:
    for scene in STYLE_SCENES:
        if scene in text:
            return scene
    return None


def _extract_length(text: str) -> str | None:
    if any(keyword in text for keyword in ("短", "简短", "short")):
        return "short"
    elif any(keyword in text for keyword in ("中", "中等", "medium")):
        return "medium"
    elif any(keyword in text for keyword in ("长", "长一点", "long")):
        return "long"
    return None


def _extract_chunk_id(text: str) -> str | None:
    match = re.search(r"(c\d+-\d+)", text)
    if match:
        return match.group(1)
    return None


def classify_request(text: str) -> IntentResult:
    text_lower = text.lower()

    # 9. Continue intent - 优先级高，避免被 read/ask 等覆盖
    continue_keywords = (
        "续写", "接着写", "继续写", "下一章", "continue", "next chapter",
    )
    after_chapter = _extract_chapter_number(text)
    length = _extract_length(text)
    scene = _extract_scene(text)
    chunk_id = _extract_chunk_id(text)
    if any(keyword in text for keyword in continue_keywords) or any(keyword in text_lower for keyword in ("continue", "next chapter")):
        suggested_args = {}
        if after_chapter is not None:
            suggested_args["after_chapter"] = after_chapter
        if chunk_id is not None:
            suggested_args["after_chunk"] = chunk_id
        if length:
            suggested_args["length"] = length
        if scene:
            suggested_args["scene"] = scene
        return IntentResult(
            intent=INTENT_CONTINUE,
            confidence=0.9,
            reason="请求包含续写/接着写相关关键词",
            suggested_args=suggested_args,
        )

    # 8. Style intent - 优先级高
    style_keywords = (
        "文风", "风格", "语言风格", "写作风格", "场景写法", "style", "distill",
    )
    scene = _extract_scene(text)
    has_style_word = any(keyword in text for keyword in style_keywords) or any(keyword in text_lower for keyword in ("style", "distill"))
    # 如果包含场景词 + "怎么写" 或 "分析"，也分类为 style
    has_scene_and_write = scene is not None and ("怎么写" in text or "分析" in text or "写法" in text)
    if has_style_word or has_scene_and_write:
        suggested_args = {}
        if scene:
            suggested_args["scene"] = scene
        return IntentResult(
            intent=INTENT_STYLE,
            confidence=0.9,
            reason="请求包含文风/风格相关关键词",
            suggested_args=suggested_args,
        )

    # 1. Status intent
    status_keywords = (
        "状态", "进度", "读到哪", "读到哪里", "status", "coverage", "progress",
        "摘要覆盖", "读了多少", "读了几章",
    )
    if any(keyword in text for keyword in status_keywords) or any(keyword in text_lower for keyword in ("status", "progress")):
        return IntentResult(
            intent=INTENT_STATUS,
            confidence=0.9,
            reason="请求包含状态/进度相关关键词",
            suggested_args={},
        )

    # 2. Read intent - 排除包含 continue 关键词的情况
    read_keywords = (
        "读第", "阅读第", "看第", "read", "阅读第", "读一下第",
    )
    chapter = _extract_chapter_number(text)
    chunk_id = _extract_chunk_id(text)
    is_read = any(keyword in text for keyword in read_keywords) or any(keyword in text_lower for keyword in ("read",))
    has_chapter_and_read = chapter is not None and "章" in text and not any(keyword in text for keyword in continue_keywords)
    if is_read or has_chapter_and_read:
        suggested_args = {}
        if chapter is not None:
            suggested_args["chapter"] = chapter
        if chunk_id is not None:
            suggested_args["chunk"] = chunk_id
        return IntentResult(
            intent=INTENT_READ,
            confidence=0.85,
            reason="请求包含阅读/查看章节相关关键词",
            suggested_args=suggested_args,
        )

    # 3. Search intent
    search_keywords = (
        "搜索", "查找", "找一下", "search", "find", "找",
    )
    if any(keyword in text for keyword in search_keywords) or any(keyword in text_lower for keyword in ("search", "find")):
        return IntentResult(
            intent=INTENT_SEARCH,
            confidence=0.8,
            reason="请求包含搜索/查找相关关键词",
            suggested_args={"query": text},
        )

    # 5. Outline intent
    outline_keywords = (
        "梳理", "大纲", "剧情梳理", "章节整理", "outline", "plot",
    )
    if any(keyword in text for keyword in outline_keywords) or any(keyword in text_lower for keyword in ("outline", "plot")):
        return IntentResult(
            intent=INTENT_OUTLINE,
            confidence=0.85,
            reason="请求包含剧情梳理/大纲相关关键词",
            suggested_args={"write": "--write" in text or "写入" in text},
        )

    # 6. Map intent
    map_keywords = (
        "地图", "全书地图", "人物表", "事件表", "map",
    )
    if any(keyword in text for keyword in map_keywords) or any(keyword in text_lower for keyword in ("map",)):
        return IntentResult(
            intent=INTENT_MAP,
            confidence=0.85,
            reason="请求包含全书地图/人物表相关关键词",
            suggested_args={},
        )

    # 7. Analyze intent
    analyze_keywords = (
        "分析", "写作分析", "分析一下", "analyze", "analysis",
    )
    if any(keyword in text for keyword in analyze_keywords) or any(keyword in text_lower for keyword in ("analyze", "analysis")):
        return IntentResult(
            intent=INTENT_ANALYZE,
            confidence=0.85,
            reason="请求包含写作分析相关关键词",
            suggested_args={},
        )

    # 4. Ask intent - 排除已经匹配到其他更具体意图的情况
    ask_keywords = (
        "为什么", "谁", "什么", "哪里", "怎么", "如何", "哪", "疑问", "ask", "question", "why", "who", "what", "where", "how",
    )
    if any(keyword in text for keyword in ask_keywords) or any(keyword in text_lower for keyword in ("ask", "why", "who", "what", "where", "how")):
        # 如果包含 style 或 continue 关键词，则不应该分类为 ask
        has_style_keywords = any(keyword in text for keyword in style_keywords)
        has_continue_keywords = any(keyword in text for keyword in continue_keywords)
        if not has_style_keywords and not has_continue_keywords:
            return IntentResult(
                intent=INTENT_ASK,
                confidence=0.8,
                reason="请求包含疑问词或提问相关关键词",
                suggested_args={"question": text},
            )

    # 10. Embed intent
    embed_keywords = (
        "embedding", "embed", "向量化", "语义索引",
    )
    if any(keyword in text for keyword in embed_keywords) or any(keyword in text_lower for keyword in ("embed", "embedding")):
        return IntentResult(
            intent=INTENT_EMBED,
            confidence=0.8,
            reason="请求包含 embedding/向量化相关关键词",
            suggested_args={},
        )

    # Unknown intent
    return IntentResult(
        intent=INTENT_UNKNOWN,
        confidence=0.5,
        reason="无法识别意图，请使用更明确的关键词",
        suggested_args={},
    )
