from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


PREDICTION_SCHEMA_VERSION = "1.0"

THREAD_TERMS = {
    "foreshadowing": ("伏笔", "线索", "秘密", "真相", "隐藏", "异常", "prophecy", "secret", "truth", "hidden", "mystery", "clue"),
    "character": ("动机", "背叛", "牺牲", "黑化", "承诺", "关系", "betrayal", "oath", "promise", "cost"),
    "setting": ("规则", "境界", "宗门", "势力", "传承", "血脉", "bloodline", "heirloom", "ancient", "map"),
    "conflict": ("冲突", "死亡", "离散", "败落", "围攻", "突破", "enemy", "conflict", "attack", "battle", "siege", "breakthrough"),
}


def book_dir(root: Path, book_id: str) -> Path:
    return root / book_id


def load_manifest(root: Path, book_id: str) -> dict[str, Any]:
    path = book_dir(root, book_id) / "manifest.json"
    if not path.exists():
        raise ValueError(f"Book not found: {book_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def open_db(root: Path, book_id: str) -> sqlite3.Connection:
    con = sqlite3.connect(book_dir(root, book_id) / "index.sqlite")
    con.row_factory = sqlite3.Row
    return con


def fetch_chunks(root: Path, book_id: str) -> list[dict[str, Any]]:
    con = open_db(root, book_id)
    try:
        return [dict(row) for row in con.execute("SELECT * FROM chunks ORDER BY chapter_index, chunk_index")]
    finally:
        con.close()


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
) -> list[dict[str, Any]]:
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
            }
        )
    return predictions


def build_prediction_packet(root: Path, book: str, args: Any) -> dict[str, Any]:
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
    warnings = ["这是基于现有文本的推测，不是作者真实后续。"]
    if insufficient:
        warnings.append("摘要覆盖或证据数量不足，预测可靠性会下降。")

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
            "semantic": bool(getattr(args, "semantic", False)),
        },
        "current_state": {
            "latest_chapter": latest_chapter,
            "recent_context": context,
            "story_so_far": build_story_so_far(summaries, evidence, latest_chapter),
            "global_threads": global_threads,
            "open_threads": open_threads,
            "character_states": infer_character_states(evidence),
            "setting_constraints": infer_setting_constraints(evidence),
        },
        "evidence": evidence,
        "predictions": build_predictions(scope, evidence, insufficient, global_threads, context),
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
            "",
        ]
    )
    for title, probability in (("高概率预测", "high"), ("中概率预测", "medium"), ("低概率/反转预测", "low")):
        lines.append(f"## {title}")
        items = group_predictions(packet, probability)
        if not items:
            lines.append("- 暂无。")
        for item in items:
            lines.append(f"- {item['id']} {item['claim']}（confidence={item['confidence']}，evidence={', '.join(item['supporting_evidence'])}）")
        lines.append("")
    lines.append("## 关键伏笔与待回收点")
    for item in packet["current_state"]["global_threads"][:8]:
        lines.append(f"- 第 {item['chapter']} 章：{item['type']} - {item['summary']}")
    lines.extend(["", "## 人物走向"])
    for item in packet["current_state"]["character_states"][:5]:
        lines.append(f"- {item['state']}（{item['source']}）")
    lines.extend(["", "## 反证与不确定性"])
    for item in packet["predictions"]:
        lines.append(f"- {item['id']}：{item['risk']}")
    lines.extend(["", "## 下一章观察清单"])
    for item in packet["watchlist"]:
        lines.append(f"- {item}")
    lines.extend(["", "## 免责声明"])
    for item in packet["warnings"]:
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def write_prediction_packet(root: Path, book: str, packet: dict[str, Any]) -> list[str]:
    target = book_dir(root, book) / "predictions"
    target.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
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
    return paths
