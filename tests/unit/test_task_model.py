from pathlib import Path

from spark.scripts.model import Task, load_task, save_task


TASK_YAML = """
name: "Reading module - reach 450"
start_url: "https://example.invalid/module/1"
goal: >
  Read each passage, click Next, and answer the comprehension questions
  correctly. Repeat until the score target is met.

stop:
  score_target: 450
  comparison: ">="
  max_iterations: 25
  max_runtime_minutes: 60

score:
  cumulative: true
  is_percentage: false

loop_action:
  type: click
  button_text: null

auth:
  requires_login: true

perception:
  force_ocr: false
  ocr_read_engine: windows

limits:
  max_steps_per_iteration: 60
  max_cost_usd: 2.00
"""


def test_load_task_from_yaml(tmp_path: Path):
    path = tmp_path / "task.yaml"
    path.write_text(TASK_YAML)
    task = load_task(path)
    assert task.name == "Reading module - reach 450"
    assert task.stop.score_target == 450
    assert task.score.cumulative is True
    assert task.loop_action.button_text is None
    assert task.auth.requires_login is True
    assert task.perception.ocr_read_engine == "windows"


def test_save_and_reload_task_roundtrip(tmp_path: Path):
    task = Task(
        name="t",
        start_url="https://x/",
        goal="do the thing",
        stop={"score_target": 100},
    )
    path = tmp_path / "roundtrip.yaml"
    save_task(task, path)
    reloaded = load_task(path)
    assert reloaded == task


def test_defaults_are_sane():
    task = Task(name="t", start_url="https://x/", goal="g", stop={"score_target": 1})
    assert task.stop.comparison == ">="
    assert task.stop.max_iterations == 25
    assert task.score.cumulative is True
    assert task.loop_action.type == "click"
