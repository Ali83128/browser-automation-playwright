import pytest

from runner.config import TaskError, load_task, render


def test_render_row_and_env(monkeypatch):
    monkeypatch.setenv("SITE_USER", "alice")
    spec = {"selector": "#n", "value": "{{row.full_name}} / {{env.SITE_USER}} / {{env.MISSING|fallback}}"}
    assert render(spec, {"full_name": "Ada Lovelace"})["value"] == "Ada Lovelace / alice / fallback"


def test_render_missing_column_is_an_error():
    with pytest.raises(TaskError, match="row.email"):
        render("{{row.email}}", {"name": "x"})


def test_render_missing_env_without_default_is_an_error(monkeypatch):
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    with pytest.raises(TaskError, match="NOPE_NOT_SET"):
        render("{{env.NOPE_NOT_SET}}")


def _write(tmp_path, text):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    p = tasks / "t.yaml"
    p.write_text(text)
    return p


def test_unknown_step_rejected(tmp_path):
    p = _write(tmp_path, "name: t\nbase_url: http://x\nsteps:\n  - teleport: '#a'\n")
    with pytest.raises(TaskError, match="unknown step 'teleport'"):
        load_task(p)


def test_missing_required_key(tmp_path):
    p = _write(tmp_path, "name: t\nsteps:\n  - click: '#a'\n")
    with pytest.raises(TaskError, match="base_url"):
        load_task(p)


def test_missing_data_file(tmp_path):
    p = _write(tmp_path, "name: t\nbase_url: http://x\ndata: data/none.csv\nsteps:\n  - click: '#a'\n")
    with pytest.raises(TaskError, match="data file not found"):
        load_task(p)


@pytest.mark.parametrize("name", ["bulk_lead_entry", "quotes_login_export", "the_internet_files"])
def test_shipped_tasks_are_valid(name):
    from pathlib import Path
    task = load_task(Path(__file__).resolve().parent.parent / "tasks" / f"{name}.yaml")
    assert task.steps and task.name == name
