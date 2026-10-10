import threading
from dataclasses import replace
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from mock_site.app import create_app
from runner.config import load_task

ROOT = Path(__file__).resolve().parent.parent


class MockServer:
    def __init__(self, flaky_every=0):
        self.app = create_app(flaky_every=flaky_every)
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def store(self):
        return self.app.config["STORE"]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()


@pytest.fixture
def mock_server():
    with MockServer() as srv:
        yield srv


@pytest.fixture
def flaky_server():
    with MockServer(flaky_every=2) as srv:
        yield srv


@pytest.fixture
def bulk_task(tmp_path):
    def make(server, **overrides):
        task = load_task(ROOT / "tasks" / "bulk_lead_entry.yaml", base_url=server.url)
        return replace(task, storage_state=tmp_path / "state.json", output=tmp_path / "results.csv",
                       rate_limit_seconds=0, **overrides)
    return make
