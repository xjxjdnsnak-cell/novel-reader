from pathlib import Path

from novel_reader.reading_session import choose_key_chapters, group_chunks_by_chapter, score_chapter_importance


def test_chinese_plot_chapter_scores_higher_than_daily_chapter():
    daily = "清晨他吃饭，喝茶，慢慢走过街道，和朋友闲聊。" * 20
    plot = "真相终于暴露，宗门背叛，血脉传承被发现，他突破境界后决定离开。" * 20

    daily_score = score_chapter_importance(daily, {"chapter_index": 1})
    plot_score = score_chapter_importance(plot, {"chapter_index": 2})

    assert plot_score["score"] > daily_score["score"]
    assert "reversal_keywords" in plot_score["reasons"]
    assert "setting_keywords" in plot_score["reasons"]
    assert "plot_progress_keywords" in plot_score["reasons"]


def test_deep_ratio_selects_high_scoring_chinese_chapter():
    chapters = [{"chapter_index": index, "title": f"Chapter {index}"} for index in range(1, 5)]
    chunks = [
        {"chapter_index": 1, "text": "日常吃饭喝茶散步。" * 40},
        {"chapter_index": 2, "text": "日常训练聊天休息。" * 40},
        {"chapter_index": 3, "text": "真相 秘密 背叛 宗门 突破 血 剑 死 传承。" * 40},
        {"chapter_index": 4, "text": "日常赶路看风景。" * 40},
    ]

    key_chapters = choose_key_chapters(chapters, group_chunks_by_chapter(chunks), 0.25, None)

    assert key_chapters == [3]
