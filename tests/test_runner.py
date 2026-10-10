import csv
from dataclasses import replace

from runner.engine import run_task


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_bulk_entry_submits_each_row_once(mock_server, bulk_task, tmp_path):
    summary = run_task(bulk_task(mock_server), limit=5, root=tmp_path)
    assert (summary.ok, summary.failed, summary.logged_in_fresh) == (5, 0, True)
    subs = mock_server.store.submissions
    assert [s["reference"] for s in subs] == [f"REF-{i:04d}" for i in range(1, 6)]
    results = _rows(tmp_path / "results.csv")
    assert [r["reference"] for r in results] == [s["reference"] for s in subs]
    # every field, including the select, radio and checkbox, matches the CSV
    from conftest import ROOT
    leads = _rows(ROOT / "data" / "leads.csv")[:5]
    for lead, sub in zip(leads, subs):
        assert (sub["full_name"], sub["email"], sub["country"], sub["plan"]) == \
               (lead["full_name"], lead["email"], lead["country"], lead["plan"])
        assert sub["newsletter"] == (lead["newsletter"] == "yes")


def test_saved_session_is_reused(mock_server, bulk_task, tmp_path):
    task = bulk_task(mock_server)
    run_task(task, limit=1, root=tmp_path)
    second = run_task(task, limit=1, root=tmp_path)
    assert second.logged_in_fresh is False
    assert mock_server.store.login_posts == 1


def test_server_errors_are_retried(flaky_server, bulk_task, tmp_path):
    summary = run_task(bulk_task(flaky_server), limit=4, root=tmp_path)
    assert summary.ok == 4 and summary.failed == 0 and summary.retried >= 1
    assert len(flaky_server.store.submissions) == 4  # retries never create duplicates
    assert list((tmp_path / "output" / "failures").glob("*.png"))


def test_failure_after_retries_is_reported_with_screenshot(mock_server, bulk_task, tmp_path):
    task = bulk_task(mock_server)
    steps = [s if "expect_text" not in s else {"expect_text": {"selector": "#confirmation", "contains": "NOT THERE"}}
             for s in task.steps]
    summary = run_task(replace(task, steps=steps, retries=1, timeout_ms=4000), limit=1, root=tmp_path)
    assert summary.failed == 1
    row = _rows(tmp_path / "results.csv")[0]
    assert row["status"] == "failed" and row["attempts"] == "2" and "NOT THERE" in row["error"]
    assert len(list((tmp_path / "output" / "failures").glob("*.png"))) == 2


def test_invalid_row_is_rejected_by_form_validation(mock_server, bulk_task, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("full_name,email,company,country,plan,newsletter,message\n"
                   "No Email,not-an-email,Co,Canada,Team,no,\n")
    task = replace(bulk_task(mock_server), data=bad, retries=0, timeout_ms=3000)
    summary = run_task(task, root=tmp_path)
    assert summary.failed == 1
    assert mock_server.store.submissions == []
