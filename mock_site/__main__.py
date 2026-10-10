from .app import create_app
import os

if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "5055"))
    flaky = int(os.environ.get("MOCK_FLAKY_EVERY", "0"))
    print(f"Mock site on http://127.0.0.1:{port}  (login demo / demo123, flaky_every={flaky})")
    create_app(flaky_every=flaky).run(host="127.0.0.1", port=port, debug=False)
