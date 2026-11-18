# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT

"""Register the ClickStack demo user and load the dashboard.

ClickStack creates the ClickHouse connection and the message source when the
first user registers. The dashboard tiles are raw SQL and need the ID of that
connection, so the dashboard is created through the API afterwards.
"""

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("CLICKSTACK_API", "http://clickstack:8000")
EMAIL = os.environ.get("CLICKSTACK_EMAIL", "demo@example.com")
PASSWORD = os.environ.get("CLICKSTACK_PASSWORD", "YangPush-e2e-1")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)
# The session cookie is issued for the domain in FRONTEND_URL (localhost), so
# a cookie jar would not send it back to the service name used here.
session = {}


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", **session}
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        r = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        r = e
    cookie = r.headers.get("Set-Cookie")
    if cookie:
        session["Cookie"] = cookie.split(";")[0]
    text = r.read().decode()
    return r.status, json.loads(text) if text.startswith(("{", "[")) else text


for _ in range(120):
    try:
        if call("GET", "/health")[0] == 200:
            break
    except OSError:
        pass
    time.sleep(2)
else:
    raise SystemExit("ClickStack API did not become ready")

status, body = call("POST", "/register/password",
                    {"email": EMAIL, "password": PASSWORD, "confirmPassword": PASSWORD})
if status == 409:
    status, body = call("POST", "/login/password", {"email": EMAIL, "password": PASSWORD})
    if status not in (200, 302, 303):
        raise SystemExit(f"login failed: {status} {body}")
elif status != 200:
    raise SystemExit(f"registration failed: {status} {body}")

status, connections = call("GET", "/connections")
if status != 200 or not connections:
    raise SystemExit(f"no ClickHouse connection: {status} {connections}")
connection = connections[0].get("id") or connections[0].get("_id")

with open(os.path.join(os.path.dirname(__file__), "dashboard.json")) as f:
    dashboard = json.loads(f.read().replace("__CONNECTION__", connection))

status, existing = call("GET", "/dashboards")
match = [d for d in existing if d["name"] == dashboard["name"]] if status == 200 else []
if match:
    status, body = call("PATCH", "/dashboards/" + match[0]["id"], dashboard)
else:
    status, body = call("POST", "/dashboards", dashboard)
if status != 200:
    raise SystemExit(f"dashboard not loaded: {status} {body}")

print(f"ClickStack is ready: http://localhost:8090  user {EMAIL}  password {PASSWORD}")
