"""Backfill default_user role via admin API.

This script logs in as admin, finds users with no roles, and assigns
`default_user` to non-superusers.

Env vars:
  BASE_URL        (default: http://127.0.0.1:8000)
  ADMIN_EMAIL     (default: admin@localhost)
  ADMIN_PASSWORD  (default: admin)

Usage:
  python scripts/backfill_default_user_role_via_api.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@localhost")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        cookie_jar = urllib.request.HTTPCookieProcessor()
        self.opener = urllib.request.build_opener(cookie_jar)

    def request(self, method: str, path: str, data: dict | None = None, form: bool = False):
        url = f"{self.base_url}{path}"
        body = None
        headers: dict[str, str] = {}
        if data is not None:
            if form:
                body = urllib.parse.urlencode(data).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                if not raw:
                    return resp.status, None
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return exc.code, payload

    def login(self) -> None:
        code, _payload = self.request("POST", "/api/auth/dev-login")
        if code == 200:
            print("Authenticated using /api/auth/dev-login")
            return

        code, payload = self.request(
            "POST",
            "/api/auth/cookie/login",
            data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            form=True,
        )
        if code != 204:
            raise RuntimeError(f"Login failed ({code}): {payload}")
        print(f"Authenticated as {ADMIN_EMAIL}")


def main() -> None:
    client = ApiClient(BASE_URL)
    client.login()

    code, roles_payload = client.request("GET", "/api/admin/users/roles")
    if code != 200 or not isinstance(roles_payload, list):
        raise RuntimeError(f"Failed to fetch roles ({code}): {roles_payload}")

    default_role = next((r for r in roles_payload if r.get("name") == "default_user"), None)
    if default_role is None:
        raise RuntimeError("Role 'default_user' not found. Start backend once to seed RBAC and retry.")
    default_role_id = default_role["id"]

    code, users_payload = client.request("GET", "/api/admin/users/")
    if code != 200 or not isinstance(users_payload, list):
        raise RuntimeError(f"Failed to fetch users ({code}): {users_payload}")

    targets = [
        u for u in users_payload
        if (not u.get("is_superuser", False)) and (len(u.get("roles", [])) == 0)
    ]

    updated = 0
    for user in targets:
        user_id = user["id"]
        code, payload = client.request(
            "PUT",
            f"/api/admin/users/{user_id}/roles",
            data={"role_ids": [default_role_id]},
        )
        if code != 200:
            raise RuntimeError(f"Failed updating user {user.get('email')} ({code}): {payload}")
        updated += 1

    print(f"default_user role id: {default_role_id}")
    print(f"users scanned: {len(users_payload)}")
    print(f"users updated: {updated}")
    if updated:
        print("updated emails:")
        for user in targets:
            print(f" - {user.get('email')}")


if __name__ == "__main__":
    main()
