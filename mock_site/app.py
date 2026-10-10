"""Local practice site: a login-protected contact form that the bulk task fills in.

Ships with the repo so the tests and the demo run without touching anyone else's site.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

from flask import Flask, abort, redirect, render_template, request, session, url_for

DEMO_USER = "demo"
DEMO_PASSWORD = "demo123"
COUNTRIES = ["United States", "United Kingdom", "Canada", "Germany", "Australia", "Pakistan", "UAE"]
PLANS = ["Starter", "Team", "Enterprise"]


@dataclass
class Store:
    submissions: list[dict] = field(default_factory=list)
    login_posts: int = 0
    form_posts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def create_app(flaky_every: int = 0) -> Flask:
    """flaky_every=N makes every Nth form POST return 503, to exercise the runner's retries."""
    app = Flask(__name__)
    app.secret_key = os.environ.get("MOCK_SECRET", "local-practice-only")
    store = Store()
    app.config["STORE"] = store

    @app.get("/")
    def index():
        return redirect(url_for("form"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            with store.lock:
                store.login_posts += 1
            if request.form.get("username") == DEMO_USER and request.form.get("password") == DEMO_PASSWORD:
                session["user"] = DEMO_USER
                return redirect(url_for("form"))
            error = "Invalid username or password."
        return render_template("login.html", error=error)

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/form", methods=["GET", "POST"])
    def form():
        if "user" not in session:
            return redirect(url_for("login"))
        if request.method == "GET":
            return render_template("form.html", countries=COUNTRIES, plans=PLANS, errors={}, values={})

        with store.lock:
            store.form_posts += 1
            attempt_no = store.form_posts
        if flaky_every and attempt_no % flaky_every == 0:
            abort(503)

        values = request.form.to_dict()
        errors = {}
        if not values.get("full_name", "").strip():
            errors["full_name"] = "Required"
        if "@" not in values.get("email", ""):
            errors["email"] = "Enter a valid email"
        if values.get("country") not in COUNTRIES:
            errors["country"] = "Choose a country"
        if values.get("plan") not in PLANS:
            errors["plan"] = "Choose a plan"
        if errors:
            return render_template("form.html", countries=COUNTRIES, plans=PLANS, errors=errors, values=values), 422

        with store.lock:
            ref = f"REF-{len(store.submissions) + 1:04d}"
            store.submissions.append({**values, "newsletter": "newsletter" in values, "reference": ref})
        return render_template("thanks.html", ref=ref, name=values["full_name"])

    @app.get("/submissions")
    def submissions():
        if "user" not in session:
            return redirect(url_for("login"))
        return render_template("submissions.html", rows=store.submissions)

    return app


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "5055"))
    flaky = int(os.environ.get("MOCK_FLAKY_EVERY", "0"))
    create_app(flaky_every=flaky).run(host="127.0.0.1", port=port, debug=False)
