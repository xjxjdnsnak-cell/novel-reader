import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "novel-reader"


def run_cli(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(CLI), "--store", str(store), *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )


def make_sample_book(path: Path) -> None:
    chapters = []
    for index in range(1, 13):
        chapters.append(
            "\n".join(
                [
                    f"第{index}章 测试章节{index}",
                    f"唐三和小舞在第{index}章推进剧情。",
                    "战斗场景里刀光与魂力碰撞，悬疑线索被暂时隐藏。",
                    "主角潜入北塔，守卫没有发现他的踪迹。",
                    "",
                ]
            )
        )
    path.write_text("\n".join(chapters), encoding="utf-8")


def import_sample(tmp_path: Path) -> tuple[Path, str]:
    store = tmp_path / "store"
    book_file = tmp_path / "sample.txt"
    make_sample_book(book_file)
    run_cli(store, "ingest", str(book_file), "--book-id", "sample-book", "--title", "样本书")
    return store, "sample-book"


def load_json(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def test_do_status_routes_to_status(tmp_path: Path):
    store, book = import_sample(tmp_path)
    data = load_json(run_cli(store, "do", book, "这本书现在读到哪了", "--json"))
    assert data["ok"] is True
    assert data["route"]["intent"] == "status"
    assert data["payload"]["book_id"] == book


def test_do_style_extracts_battle_scene(tmp_path: Path):
    store, book = import_sample(tmp_path)
    data = load_json(run_cli(store, "do", book, "帮我分析战斗场景怎么写", "--json"))
    assert data["ok"] is True
    assert data["route"]["intent"] == "style"
    assert data["route"]["suggested_args"]["scene"] == "战斗"


def test_do_continue_extracts_controls(tmp_path: Path):
    store, book = import_sample(tmp_path)
    data = load_json(run_cli(store, "do", book, "接第12章后面续写，短一点，偏悬疑", "--json"))
    assert data["ok"] is True
    assert data["route"]["intent"] == "continue"
    goal = data["payload"]["continuation_goal"]
    assert goal["after_chapter"] == 12
    assert goal["target_length"] == "short"
    assert goal["scene"] == "悬疑"


def test_write_next_outputs_package_and_prompt(tmp_path: Path):
    store, book = import_sample(tmp_path)
    data = load_json(
        run_cli(
            store,
            "write-next",
            book,
            "--after-chapter",
            "12",
            "--outline",
            "主角潜入北塔",
            "--json",
        )
    )
    assert data["ok"] is True
    assert "package" in data
    assert "prose_generation_prompt" in data
    assert data["package"]["continuation_goal"]["after_chapter"] == 12
    assert "self_checklist" in data["package"]
    assert "constraints" in data["package"]
