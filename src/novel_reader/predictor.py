from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .storage import book_dir, fetch_chunks, load_manifest, open_db


PREDICTION_SCHEMA_VERSION = "1.1"

# Maximum character budget allocated to prediction prompt building
# so that the prompt stays within reasonable LLM context limits.
MAX_PROMPT_EVIDENCE_CHARS = 60000
# Timeout (seconds) for the subprocess-based LLM call.
LLM_TIMEOUT_SECONDS = 240

THREAD_TERMS = {
    "foreshadowing": ("伏笔", "线索", "秘密", "真相", "隐藏", "异常", "prophecy", "secret", "truth", "hidden", "mystery", "clue"),
    "character": ("动机", "背叛", "牺牲", "黑化", "承诺", "关系", "betrayal", "oath", "promise", "cost"),
    "setting": ("规则", "境界", "宗门", "势力", "传承", "血脉", "bloodline", "heirloom", "ancient", "map"),
    "conflict": ("冲突", "死亡", "离散", "败落", "围攻", "突破", "enemy", "conflict", "attack", "battle", "siege", "breakthrough"),
}


def fetch_summaries(root: Path, book_id: str) -> dict[int, str]:
    con = open_db(root, book_id)
    try:
        rows = list(con.execute("SELECT chapter_index, summary_path FROM summaries ORDER BY chapter_index"))
    finally:
        con.close()
    summaries: dict[int, str] = {}
    for row in rows:
        path = Path(row["summary_path"])
        if path.exists():
            summaries[int(row["chapter_index"])] = path.read_text(encoding="utf-8", errors="replace")
    return summaries


def restrict_chunks_to_anchor(chunks: list[dict[str, Any]], anchor_chapter: int | None, anchor_chunk: str | None) -> list[dict[str, Any]]:
    if anchor_chunk:
        limited = []
        for chunk in chunks:
            limited.append(chunk)
            if chunk["chunk_id"] == anchor_chunk:
                return limited
        return limited
    if anchor_chapter is not None:
        return [chunk for chunk in chunks if int(chunk["chapter_index"]) <= anchor_chapter]
    return chunks


def restrict_summaries_to_anchor(summaries: dict[int, str], anchor_chapter: int | None) -> dict[int, str]:
    if anchor_chapter is None:
        return summaries
    return {chapter: text for chapter, text in summaries.items() if chapter <= anchor_chapter}


def compact_excerpt(text: str, max_chars: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:max_chars]


def recent_context(chunks: list[dict[str, Any]], anchor_chapter: int | None, anchor_chunk: str | None, count: int) -> list[dict[str, Any]]:
    if not chunks:
        return []
    anchor_index = len(chunks) - 1
    if anchor_chunk:
        for index, chunk in enumerate(chunks):
            if chunk["chunk_id"] == anchor_chunk:
                anchor_index = index
                break
    elif anchor_chapter:
        for index, chunk in enumerate(chunks):
            if int(chunk["chapter_index"]) <= anchor_chapter:
                anchor_index = index
    start = max(0, anchor_index - max(count, 1) + 1)
    return [format_context_chunk(chunk) for chunk in chunks[start : anchor_index + 1]]


def format_context_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": chunk["chunk_id"],
        "chapter": int(chunk["chapter_index"]),
        "chapter_title": chunk["chapter_title"],
        "line_start": int(chunk["line_start"]),
        "line_end": int(chunk["line_end"]),
        "excerpt": compact_excerpt(chunk["text"], 320),
    }


def query_terms(question: str | None, scope: str, horizon: str) -> list[str]:
    terms = []
    if question:
        terms.extend(term for term in re.split(r"\W+", question) if len(term) >= 2)
    by_scope = {
        "general": ["冲突", "秘密", "真相", "突破", "敌人", "目标", "线索", "conflict", "secret", "truth"],
        "next-arc": ["当前", "冲突", "行动", "敌人", "障碍", "进入", "离开", "current", "enemy", "attack"],
        "character": ["动机", "背叛", "黑化", "牺牲", "成长", "关系", "承诺", "betrayal", "oath"],
        "foreshadowing": ["伏笔", "线索", "秘密", "真相", "异常", "预感", "隐藏", "prophecy", "hidden", "clue"],
        "ending": ["结局", "主线", "最终", "敌人", "完成", "死亡", "代价", "ending", "final", "cost"],
    }
    by_horizon = {
        "next-3-chapters": ["下一章", "短期", "当前冲突"],
        "next-arc": ["下一阶段", "小高潮", "阶段目标"],
        "ending": ["最终", "结局", "主线"],
    }
    terms.extend(by_scope.get(scope, by_scope["general"]))
    terms.extend(by_horizon.get(horizon, []))
    return [term for term in terms if term]


def score_terms(text: str, terms: tuple[str, ...] | list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term.lower()) for term in terms) + sum(text.count(term) for term in terms)


def reason_from_text(text: str, question: str | None = None) -> str:
    scores = {category: score_terms(text, terms) for category, terms in THREAD_TERMS.items()}
    category = max(scores, key=scores.get)
    if scores[category] <= 0:
        return "问题相关" if question else "最近剧情状态"
    return {
        "foreshadowing": "伏笔/线索",
        "character": "人物动机",
        "setting": "设定规则",
        "conflict": "冲突升级",
    }[category]


def format_evidence_chunk(chunk: dict[str, Any], reason: str | None = None, max_chars: int = 300) -> dict[str, Any]:
    return {
        "chunk_id": chunk["chunk_id"],
        "chapter": int(chunk["chapter_index"]),
        "chapter_title": chunk["chapter_title"],
        "line_start": int(chunk["line_start"]),
        "line_end": int(chunk["line_end"]),
        "reason": reason or reason_from_text(chunk["text"]),
        "excerpt": compact_excerpt(chunk["text"], max_chars),
    }


def select_distributed(scored: list[tuple[float, int, Any]], chapter_of: Any, limit: int) -> list[Any]:
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1]))
    chapters = [chapter_of(item[2]) for item in scored]
    min_chapter = min(chapters)
    max_chapter = max(chapters)
    span = max(max_chapter - min_chapter + 1, 1)
    bands = (
        (min_chapter, min_chapter + span // 3),
        (min_chapter + span // 3 + 1, min_chapter + 2 * span // 3),
        (min_chapter + 2 * span // 3 + 1, max_chapter),
    )
    selected = []
    seen_keys = set()
    for start, end in bands:
        for _, _, item in scored:
            chapter = chapter_of(item)
            key = item.get("chunk_id") or item.get("chapter")
            if start <= chapter <= end and key not in seen_keys:
                selected.append(item)
                seen_keys.add(key)
                break
    for _, _, item in scored:
        key = item.get("chunk_id") or item.get("chapter")
        if key not in seen_keys:
            selected.append(item)
            seen_keys.add(key)
        if len(selected) >= limit:
            break
    return selected[:limit]


def collect_evidence(
    chunks: list[dict[str, Any]],
    question: str | None,
    scope: str,
    horizon: str,
    top: int,
    context_ids: set[str],
) -> list[dict[str, Any]]:
    terms = query_terms(question, scope, horizon)
    scored = []
    for index, chunk in enumerate(chunks):
        text = chunk["text"]
        score = score_terms(text, terms)
        reason = reason_from_text(text, question)
        for category_terms in THREAD_TERMS.values():
            score += score_terms(text, category_terms) * 1.5
        if chunk["chunk_id"] in context_ids:
            score += 2
        if score > 0:
            scored.append((score, index, chunk))
    if not scored:
        scored = [(1, index, chunk) for index, chunk in enumerate(chunks[-top:])]
    selected = select_distributed(scored, lambda item: int(item["chapter_index"]), top)
    return [format_evidence_chunk(chunk, reason_from_text(chunk["text"], question)) for chunk in selected]


def extract_global_threads(summaries: dict[int, str], evidence: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    scored = []
    for order, (chapter, text) in enumerate(summaries.items()):
        category_scores = {category: score_terms(text, terms) for category, terms in THREAD_TERMS.items()}
        category = max(category_scores, key=category_scores.get)
        score = category_scores[category]
        if score:
            scored.append(
                (
                    score,
                    order,
                    {
                        "chapter": chapter,
                        "type": category,
                        "summary": compact_excerpt(text, 180),
                    },
                )
            )
    if not scored:
        for order, item in enumerate(evidence):
            scored.append((1, order, {"chapter": item["chapter"], "type": item["reason"], "summary": item["excerpt"]}))
    return select_distributed(scored, lambda item: int(item["chapter"]), limit)


def extract_open_threads(summaries: dict[int, str], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    threads = extract_global_threads(summaries, evidence, limit=8)
    if not threads:
        return [{"chapter": item["chapter"], "type": item["reason"], "summary": item["excerpt"]} for item in evidence[:5]]
    return threads


def infer_character_states(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": item["chunk_id"],
            "state": "人物目标、关系或立场仍需以后续章节验证。",
            "basis": item["reason"],
        }
        for item in evidence[:5]
    ]


def infer_setting_constraints(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    constraints = []
    for item in evidence:
        if item["reason"] == "设定规则":
            constraints.append({"source": item["chunk_id"], "constraint": "后续预测应遵守该处出现的设定、势力或能力规则。"})
    return constraints[:5]


def build_story_so_far(summaries: dict[int, str], evidence: list[dict[str, Any]], latest_chapter: int) -> dict[str, Any]:
    return {
        "covered_chapters": len(summaries) or len({item["chapter"] for item in evidence}),
        "latest_chapter": latest_chapter,
        "summary": "全前文线索已纳入预测评分；近期章节仅用于判断当前状态。",
        "representative_chapters": sorted({item["chapter"] for item in evidence})[:12],
    }


SCOPE_PROMPT_GUIDANCE: dict[str, str] = {
    "ending": (
        "你正在做「结局预测」。请基于前文所有伏笔、判词、诗歌谶语、人物对话中的暗示，"
        "预测全书的最终结局。至少覆盖以下方面：\n"
        "1. 主要人物的最终命运（逐个分析，引用判词/曲文/诗句作为证据）\n"
        "2. 核心冲突的最终解决方式\n"
        "3. 主题（如'好'与'了'、盛筵必散）的最终体现\n"
        "4. 关键伏笔的回收路径（指出哪些前文线索将如何应验）\n"
        "5. 可能的最终画面/场景描述"
    ),
    "next-arc": (
        "你正在做「下一阶段预测」。请基于当前剧情状态和未解决的冲突，"
        "预测下一阶段（约 3-10 章）的剧情走向：\n"
        "1. 当前冲突如何升级或转变\n"
        "2. 人物关系会发生什么变化\n"
        "3. 哪些隐藏信息可能被揭露\n"
        "4. 短期障碍和转折点"
    ),
    "character": (
        "你正在做「人物走向预测」。请基于前文的人物动机、性格刻画和伏笔，"
        "预测关键人物的未来发展：\n"
        "1. 每个人物的弧线方向（成长/堕落/牺牲/黑化/和解）\n"
        "2. 人物关系的关键转折点\n"
        "3. 隐藏动机或秘密身份的揭示"
    ),
    "foreshadowing": (
        "你正在做「伏笔回收预测」。请基于前文所有已铺设的伏笔和线索，"
        "预测它们的回收方式：\n"
        "1. 哪些伏笔将在结局前回收\n"
        "2. 每个伏笔最可能的回收路径\n"
        "3. 哪些伏笔可能相互关联形成更大的揭示"
    ),
    "general": (
        "你正在做「综合剧情预测」。请基于前文所有信息，"
        "预测后续剧情的主要发展方向：\n"
        "1. 主线冲突的走向\n"
        "2. 重要人物的命运变化\n"
        "3. 关键伏笔的回收\n"
        "4. 可能的反转或意外发展"
    ),
}

PREDICTION_OUTPUT_FORMAT = """请以 JSON 格式输出预测结果，结构如下：
```json
{
  "predictions": [
    {
      "id": "P1",
      "type": "ending|plot_direction|character_arc|foreshadowing_payoff|conflict",
      "claim": "具体的、有证据支撑的预测声明（不要泛泛而谈，要涉及具体人物和情节）",
      "probability": "high|medium|low",
      "confidence": 0.0-1.0,
      "reasoning": ["推理步骤1", "推理步骤2", "..."],
      "supporting_evidence": ["chunk_id1", "chunk_id2", "..."],
      "counter_evidence": ["可能反驳此预测的证据"],
      "risk": "此预测的风险或不确定性"
    }
  ],
  "alternative_scenarios": [
    {"name": "场景名", "summary": "描述", "probability": "medium|low"}
  ],
  "watchlist": ["下一步应观察的关键信号"]
}
```

要求：
- 每个 prediction 必须引用具体的 chunk_id 作为证据
- claim 必须具体，涉及书中实际人物和情节
- reasoning 必须展示从证据到结论的推理链
- 不要输出模板化的通用声明"""


def build_prediction_prompt(
    book: dict[str, Any],
    question: str | None,
    scope: str,
    horizon: str,
    summaries: dict[int, str],
    evidence: list[dict[str, Any]],
    global_threads: list[dict[str, Any]],
    open_threads: list[dict[str, Any]],
    recent_context: list[dict[str, Any]],
    character_states: list[dict[str, Any]],
    anchor_chapter: int | None,
    latest_chapter: int,
    coverage: float,
) -> str:
    """Build a detailed Chinese prompt for LLM-powered prediction.

    The prompt assembles all available evidence — summaries, chunk excerpts,
    narrative threads, character states, and recent context — into a
    structured analysis request that an LLM can use to generate deep,
    evidence-grounded predictions instead of template filler.
    """
    lines: list[str] = []

    # ── header ──────────────────────────────────────────────
    lines.append(f"# 小说预测分析请求")
    lines.append(f"书籍：{book.get('title', '未知')} (book_id: {book.get('id', '未知')})")
    lines.append(f"总章节数：{book.get('chapter_count', '未知')}")
    lines.append(f"摘要覆盖率：{coverage}%")
    if anchor_chapter:
        lines.append(f"分析锚点：第 {anchor_chapter} 章（仅使用该章及之前的内容）")
    lines.append(f"分析范围：{scope}")
    lines.append(f"时间跨度：{horizon}")
    if question:
        lines.append(f"用户问题：{question}")
    lines.append("")
    lines.append(SCOPE_PROMPT_GUIDANCE.get(scope, SCOPE_PROMPT_GUIDANCE["general"]))
    lines.append("")

    # ── story state ─────────────────────────────────────────
    lines.append("## 当前剧情状态")
    lines.append(f"最新章节：第 {latest_chapter} 章")
    if recent_context:
        lines.append("### 近期章节上下文")
        for item in recent_context[-5:]:
            title = item.get("chapter_title", "")
            excerpt = item.get("excerpt", "")
            lines.append(f"- **{item.get('chunk_id', '')}** ({title})：{excerpt}")
    lines.append("")

    # ── evidence chunks ─────────────────────────────────────
    lines.append("## 关键证据片段")
    lines.append("（以下是从全书中按线索相关性筛选出的原文片段和章节摘要）")
    evidence_budget = 0
    for item in evidence:
        title = item.get("chapter_title", "")
        reason = item.get("reason", "")
        excerpt = item.get("excerpt", "")
        chunk_id = item.get("chunk_id", "")
        entry = f"- **{chunk_id}** [{reason}] ({title})：{excerpt}"
        evidence_budget += len(entry)
        if evidence_budget > MAX_PROMPT_EVIDENCE_CHARS:
            lines.append(f"... (evidence truncated at {MAX_PROMPT_EVIDENCE_CHARS} chars, {len(evidence)} total chunks)")
            break
        lines.append(entry)
    lines.append("")

    # ── chapter summaries ───────────────────────────────────
    lines.append("## 章节摘要（L1 快速覆盖）")
    lines.append("（以下是全书各章的 L1 快速摘要，按章节顺序排列）")
    summary_chars = 0
    summary_budget = MAX_PROMPT_EVIDENCE_CHARS // 2
    for chapter in sorted(summaries):
        text = summaries[chapter]
        # Extract the one_sentence field preferentially, plus a compact view of the rest
        compact = compact_excerpt(text, 300)
        entry = f"- 第{chapter}章：{compact}"
        summary_chars += len(entry)
        if summary_chars > summary_budget:
            lines.append(f"... (summaries truncated, {len(summaries)} total)")
            break
        lines.append(entry)
    lines.append("")

    # ── global threads ──────────────────────────────────────
    lines.append("## 全局叙事线索（跨章节）")
    for item in global_threads[:8]:
        lines.append(f"- 第{item['chapter']}章 [{item['type']}]：{item['summary']}")
    lines.append("")

    # ── open threads ────────────────────────────────────────
    lines.append("## 未解决的叙事线索")
    for item in open_threads[:6]:
        lines.append(f"- 第{item['chapter']}章 [{item['type']}]：{item['summary']}")
    lines.append("")

    # ── character states ────────────────────────────────────
    if character_states:
        lines.append("## 人物状态快照")
        for item in character_states:
            lines.append(f"- {item.get('source', '')} [{item.get('basis', '')}]：{item.get('state', '')}")
        lines.append("")

    # ── output format ───────────────────────────────────────
    lines.append(PREDICTION_OUTPUT_FORMAT)

    return "\n".join(lines)


def call_claude_for_prediction(prompt: str) -> str | None:
    """Call the ``claude`` CLI as a subprocess for LLM-powered prediction.

    Uses the same subprocess pattern as the web console's Claude bridge
    (web_app.py:594-631).  Returns the parsed text reply on success,
    or ``None`` when the CLI is not available or the call fails.
    """
    executable = shutil.which("claude")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "-p", "--output-format", "json"],
            cwd=Path.cwd(),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LLM_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if completed.returncode != 0:
        return None

    stdout = completed.stdout.strip()
    if not stdout:
        return None

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        # Raw text reply — use as-is.
        return stdout

    # Extract the actual text from the Claude CLI JSON envelope.
    for key in ("result", "response", "text", "message"):
        if key in parsed and isinstance(parsed[key], str):
            return str(parsed[key])
    # Claude Messages API format: content is a list of blocks.
    if isinstance(parsed.get("content"), list):
        pieces = [block["text"] for block in parsed["content"] if isinstance(block, dict) and block.get("type") == "text" and "text" in block]
        if pieces:
            return "\n".join(pieces)
    return stdout


def parse_llm_predictions(
    llm_text: str,
    scope: str,
    evidence: list[dict[str, Any]],
    global_threads: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    insufficient: bool,
) -> list[dict[str, Any]]:
    """Parse structured predictions from an LLM text response.

    Attempts to extract a JSON block from the LLM output.  On success,
    validates and normalises each prediction entry against the existing
    packet schema.  Falls back to a single ``llm_raw`` prediction entry
    when JSON parsing fails.
    """
    # Try to locate a JSON block in the response.
    json_candidate = llm_text
    json_match = re.search(r"\{[\s\S]*\"predictions\"[\s\S]*\}", llm_text)
    if json_match:
        json_candidate = json_match.group(0)
    elif re.search(r"```json\s*([\s\S]*?)\s*```", llm_text):
        json_candidate = re.search(r"```json\s*([\s\S]*?)\s*```", llm_text).group(1)

    predictions: list[dict[str, Any]] = []

    try:
        parsed = json.loads(json_candidate)
        if isinstance(parsed, dict) and "predictions" in parsed:
            for index, item in enumerate(parsed["predictions"], start=1):
                if not isinstance(item, dict):
                    continue
                predictions.append({
                    "id": item.get("id", f"P{index}"),
                    "type": item.get("type", "plot_direction"),
                    "claim": str(item.get("claim", "")),
                    "probability": item.get("probability", "medium"),
                    "confidence": round(float(item.get("confidence", 0.5)), 2),
                    "reasoning": item.get("reasoning", []) if isinstance(item.get("reasoning"), list) else [str(item.get("reasoning", ""))],
                    "supporting_evidence": item.get("supporting_evidence", []) if isinstance(item.get("supporting_evidence"), list) else [],
                    "counter_evidence": item.get("counter_evidence", []) if isinstance(item.get("counter_evidence"), list) else [],
                    "risk": str(item.get("risk", "")),
                    "source": "llm",
                })
    except (json.JSONDecodeError, TypeError):
        # Fallback: store the raw LLM response as a prediction entry.
        predictions.append({
            "id": "P1",
            "type": f"{scope}_llm_analysis",
            "claim": llm_text[:500].replace("\n", " "),
            "probability": "medium",
            "confidence": 0.5,
            "reasoning": ["LLM 输出未能解析为结构化 JSON，完整回复见 raw_llm_response 字段。"],
            "supporting_evidence": [item["chunk_id"] for item in evidence[:3]],
            "counter_evidence": [],
            "risk": "LLM 输出格式不标准，无法自动提取结构化预测。",
            "source": "llm",
            "raw_llm_response": llm_text[:8000],
        })

    if not predictions:
        # Empty response guard.
        predictions.append({
            "id": "P1",
            "type": "plot_direction",
            "claim": "LLM 返回了空响应，无法生成预测。",
            "probability": "low",
            "confidence": 0.1,
            "reasoning": ["LLM 输出为空或仅包含空白字符。"],
            "supporting_evidence": [],
            "counter_evidence": [],
            "risk": "LLM 调用失败。",
            "source": "llm",
        })

    return predictions


def prediction_templates(scope: str) -> list[tuple[str, str, str]]:
    common = [
        ("plot_direction", "当前未解决冲突更可能在下一阶段继续升级，并迫使主角采取更主动的行动。", "high"),
        ("foreshadowing_payoff", "已出现的线索或秘密可能被部分回收，但仍会保留新的反转空间。", "medium"),
        ("character_arc", "关键人物的立场可能出现摇摆，关系变化会成为推动剧情的压力源。", "medium"),
        ("conflict", "短期障碍可能来自敌对势力、规则限制或隐藏信息暴露。", "medium"),
        ("ending", "结局更可能围绕主线目标完成与代价支付展开，而不是无条件圆满。", "low"),
    ]
    focused = {
        "next-arc": [common[0], common[3], common[1]],
        "character": [common[2], common[0], common[1]],
        "foreshadowing": [common[1], common[3], common[2]],
        "ending": [common[4], common[0], common[2]],
        "general": common[:4],
    }
    return focused.get(scope, common[:4])


def build_predictions(
    scope: str,
    evidence: list[dict[str, Any]],
    insufficient: bool,
    global_threads: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    llm_response: str | None = None,
) -> list[dict[str, Any]]:
    if llm_response and llm_response.strip():
        return parse_llm_predictions(llm_response, scope, evidence, global_threads, recent, insufficient)

    # -- fallback: template-based predictions (backward compatible) --
    predictions = []
    global_refs = [f"第{item['chapter']}章" for item in global_threads[:3]] or ["全前文暂无明确摘要线索"]
    recent_refs = [item["chunk_id"] for item in recent[-3:]] or [item["chunk_id"] for item in evidence[-2:]]
    for index, (kind, claim, probability) in enumerate(prediction_templates(scope), start=1):
        support = [item["chunk_id"] for item in evidence[index - 1 : index + 2]] or [item["chunk_id"] for item in evidence[:1]]
        counter = [item["chunk_id"] for item in evidence[-1:]] if len(evidence) > 3 else []
        confidence_base = {"high": 0.72, "medium": 0.56, "low": 0.34}[probability]
        confidence = max(0.18, confidence_base - (0.18 if insufficient else 0.0))
        predictions.append(
            {
                "id": f"P{index}",
                "type": kind,
                "claim": claim,
                "probability": probability,
                "confidence": round(confidence, 2),
                "reasoning": [
                    f"全前文长期线索来自 {', '.join(global_refs)}。",
                    f"近期剧情状态参考 {', '.join(recent_refs)}。",
                    f"直接支持证据来自 {', '.join(support)}。",
                    "该判断是概率推测，不等同于作者真实后续。",
                ],
                "supporting_evidence": support,
                "counter_evidence": counter,
                "risk": "证据不足或作者可能故意误导；若后续出现反向设定，该预测应下调。",
                "source": "template",
            }
        )
    return predictions


def build_prediction_packet(root: Path, book: str, args: Any, use_llm: bool = False) -> dict[str, Any]:
    manifest = load_manifest(root, book)
    all_chunks = fetch_chunks(root, book)
    anchor_chapter = getattr(args, "anchor_chapter", None)
    anchor_chunk = getattr(args, "anchor_chunk", None)
    chunks = restrict_chunks_to_anchor(all_chunks, anchor_chapter, anchor_chunk)
    summaries = restrict_summaries_to_anchor(fetch_summaries(root, book), anchor_chapter)
    chapter_count = int(manifest.get("chapter_count", 0))
    coverage_denominator = anchor_chapter or chapter_count
    coverage = round(len(summaries) * 100 / max(coverage_denominator, 1), 2)
    scope = getattr(args, "scope", None) or "general"
    horizon = getattr(args, "horizon", None) or "next-arc"
    question = getattr(args, "question", None) or None
    context = recent_context(chunks, anchor_chapter, anchor_chunk, int(getattr(args, "context_chunks", 5) or 5))
    context_ids = {item["chunk_id"] for item in context}
    evidence = collect_evidence(chunks, question, scope, horizon, int(getattr(args, "top", 8) or 8), context_ids)
    latest_chapter = max((int(chunk["chapter_index"]) for chunk in chunks), default=0)
    global_threads = extract_global_threads(summaries, evidence, limit=12)
    open_threads = extract_open_threads(summaries, evidence)
    insufficient = len(evidence) < 3 or coverage < 20
    character_states = infer_character_states(evidence)
    warnings = ["这是基于现有文本的推测，不是作者真实后续。"]
    semantic_requested = bool(getattr(args, "semantic", False))
    semantic_applied = False
    if semantic_requested:
        warnings.append(
            "semantic requested but predict currently uses local heuristic scoring; "
            "semantic_applied=false (predict 当前仍使用本地启发式证据排序，semantic 只记录请求状态，未参与预测排序)."
        )
    if insufficient:
        warnings.append("摘要覆盖或证据数量不足，预测可靠性会下降。")

    # ── Build the LLM prediction prompt ─────────────────────
    prompt_md = build_prediction_prompt(
        book={
            "id": book,
            "title": manifest.get("title", book),
            "chapter_count": chapter_count,
        },
        question=question,
        scope=scope,
        horizon=horizon,
        summaries=summaries,
        evidence=evidence,
        global_threads=global_threads,
        open_threads=open_threads,
        recent_context=context,
        character_states=character_states,
        anchor_chapter=anchor_chapter,
        latest_chapter=latest_chapter,
        coverage=coverage,
    )
    prompt_path: str | None = None

    # ── LLM-powered prediction ──────────────────────────────
    llm_response: str | None = None
    if use_llm:
        llm_response = call_claude_for_prediction(prompt_md)
        if llm_response is None:
            warnings.append("--llm 已指定但 claude CLI 不可用或调用失败，回退到模板预测。")

    predictions = build_predictions(
        scope, evidence, insufficient, global_threads, context,
        llm_response=llm_response,
    )

    if not use_llm:
        warnings.append(
            "当前为模板预测（默认模式）。要使用 LLM 驱动的深度预测，请添加 --llm 参数，"
            "或使用 --write 生成分析 prompt 文件后手动交由 LLM 分析。"
        )

    return {
        "ok": True,
        "schema_version": PREDICTION_SCHEMA_VERSION,
        "book": {
            "id": book,
            "title": manifest.get("title", book),
            "chapter_count": chapter_count,
            "summary_coverage_percent": coverage,
        },
        "prediction_goal": {
            "question": question,
            "scope": scope,
            "horizon": horizon,
            "anchor_chapter": anchor_chapter,
            "anchor_chunk": anchor_chunk,
            "semantic": semantic_requested,
            "semantic_requested": semantic_requested,
            "semantic_applied": semantic_applied,
            "use_llm": use_llm,
        },
        "current_state": {
            "latest_chapter": latest_chapter,
            "recent_context": context,
            "story_so_far": build_story_so_far(summaries, evidence, latest_chapter),
            "global_threads": global_threads,
            "open_threads": open_threads,
            "character_states": character_states,
            "setting_constraints": infer_setting_constraints(evidence),
        },
        "evidence": evidence,
        "predictions": predictions,
        "alternative_scenarios": [
            {"name": "保守走向", "summary": "已有冲突按当前方向推进，伏笔逐步回收。", "probability": "medium"},
            {"name": "反转走向", "summary": "关键人物立场或已知设定被重新解释，改变主线判断。", "probability": "low"},
        ],
        "watchlist": [
            "观察下一章是否继续强化同一冲突或转入新地点。",
            "如果关键人物再次提到秘密、承诺或代价，相关预测概率上升。",
            "如果出现反向设定或新敌对势力，应重新生成预测包。",
        ],
        "evidence_insufficient": insufficient,
        "missing_reading_suggestions": ["提高 summary coverage，或先完成 balanced reading session。"] if insufficient else [],
        "recommended_next_reads": [item["chapter"] for item in context[-3:]],
        "warnings": warnings,
        # Always retain prompt_md so users can audit what is/was sent to Claude,
        # whether the LLM mode succeeded, fell back, or wasn't requested at all.
        "prompt_md": prompt_md,
        "prompt_path": prompt_path,
        "llm_response": llm_response,
    }


def group_predictions(packet: dict[str, Any], probability: str) -> list[dict[str, Any]]:
    return [item for item in packet["predictions"] if item["probability"] == probability]


def render_prediction_packet(packet: dict[str, Any]) -> str:
    lines = ["# 后续剧情预测", ""]
    goal = packet["prediction_goal"]
    book = packet["book"]
    lines.extend(
        [
            "## 当前剧情状态",
            f"- 书籍：{book['title']} ({book['id']})",
            f"- 章节数：{book['chapter_count']}",
            f"- 摘要覆盖率：{book['summary_coverage_percent']}%",
            f"- 问题：{goal.get('question') or '全局后续剧情预测'}",
            f"- 分析模式：{'LLM 驱动' if goal.get('use_llm') else '模板（默认）'}",
            "",
        ]
    )
    for title, probability in (("高概率预测", "high"), ("中概率预测", "medium"), ("低概率/反转预测", "low")):
        lines.append(f"## {title}")
        items = group_predictions(packet, probability)
        if not items:
            lines.append("- 暂无。")
        for item in items:
            src = f" [source: {item.get('source', 'template')}]"
            lines.append(f"- {item['id']} {item['claim']}（confidence={item['confidence']}，evidence={', '.join(item['supporting_evidence'])}）{src}")
            if item.get("reasoning") and item.get("source") == "llm":
                for reason in item["reasoning"][:3]:
                    lines.append(f"  - {reason}")
        lines.append("")
    lines.append("## 关键伏笔与待回收点")
    for item in packet["current_state"]["global_threads"][:8]:
        lines.append(f"- 第 {item['chapter']} 章：{item['type']} - {item['summary']}")
    lines.extend(["", "## 人物走向"])
    for item in packet["current_state"]["character_states"][:5]:
        lines.append(f"- {item['state']}（{item['source']}）")
    lines.extend(["", "## 反证与不确定性"])
    for item in packet["predictions"]:
        lines.append(f"- {item['id']}：{item.get('risk', '')}")
    lines.extend(["", "## 下一章观察清单"])
    for item in packet["watchlist"]:
        lines.append(f"- {item}")
    if packet.get("prompt_path"):
        lines.extend(["", "## LLM 分析 Prompt"])
        lines.append(f"已保存至：{packet['prompt_path']}")
    if packet.get("llm_response"):
        lines.extend(["", "## LLM 原始回复（摘要）"])
        excerpt = packet["llm_response"][:400].replace("\n", " ")
        lines.append(f"- {excerpt}...")
    lines.extend(["", "## 免责声明"])
    for item in packet["warnings"]:
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def write_prediction_packet(root: Path, book: str, packet: dict[str, Any]) -> list[str]:
    target = book_dir(root, book) / "predictions"
    target.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    prompt_path: Path | None = None
    prompt_latest: Path | None = None
    if packet.get("prompt_md"):
        prompt_path = target / f"{stamp}-prediction-prompt.md"
        prompt_latest = target / "prediction-prompt-latest.md"
        packet["prompt_path"] = str(prompt_path)
    markdown = render_prediction_packet(packet)
    json_text = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    paths = []
    for path, content in (
        (target / f"{stamp}-prediction.json", json_text),
        (target / f"{stamp}-prediction.md", markdown),
        (target / "prediction-latest.json", json_text),
        (target / "prediction-latest.md", markdown),
    ):
        path.write_text(content, encoding="utf-8")
        paths.append(str(path))

    # Write the LLM-ready prediction prompt as a standalone file.
    if packet.get("prompt_md") and prompt_path and prompt_latest:
        prompt_path.write_text(packet["prompt_md"], encoding="utf-8")
        prompt_latest.write_text(packet["prompt_md"], encoding="utf-8")
        paths.extend([str(prompt_path), str(prompt_latest)])

    return paths
