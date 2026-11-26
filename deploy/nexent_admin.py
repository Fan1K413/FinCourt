"""Bootstrap the official Nexent supervisor account without a host Docker socket."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psycopg

EMAIL = "suadmin@nexent.com"


def auth_user_id() -> str | None:
    with psycopg.connect(
        host=os.environ["SUPABASE_POSTGRES_HOST"],
        port=int(os.environ["SUPABASE_POSTGRES_PORT"]),
        dbname=os.environ["SUPABASE_POSTGRES_DB"],
        user="postgres",
        password=os.environ["SUPABASE_POSTGRES_PASSWORD"],
        connect_timeout=3,
    ) as connection:
        row = connection.execute(
            "SELECT id::text FROM auth.users WHERE email = %s", (EMAIL,)
        ).fetchone()
    return row[0] if row else None


def create_user() -> str:
    token = os.environ["SERVICE_ROLE_KEY"]
    request = Request(
        "http://kong:8000/auth/v1/admin/users",
        data=json.dumps(
            {
                "email": EMAIL,
                "password": os.environ["NEXENT_SUPER_ADMIN_PASSWORD"],
                "email_confirm": True,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            result = json.load(response)
        return result["id"]
    except HTTPError as error:
        if error.code in {409, 422}:
            existing = auth_user_id()
            if existing:
                return existing
        if error.code < 500:
            raise SystemExit(f"Nexent 登录服务拒绝初始化请求（HTTP {error.code}）。") from None
        raise


def ensure_admin_role() -> None:
    with psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["NEXENT_POSTGRES_PASSWORD"],
        connect_timeout=3,
    ) as connection:
        connection.execute("SELECT user_role FROM nexent.user_tenant_t LIMIT 0")
        identifier = auth_user_id() or create_user()
        connection.execute(
            """INSERT INTO nexent.user_tenant_t
               (user_id, tenant_id, user_role, user_email, created_by, updated_by)
               VALUES (%s, '', 'SU', %s, 'system', 'system')
               ON CONFLICT (user_id, tenant_id) DO NOTHING""",
            (identifier, EMAIL),
        )


def main() -> None:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            ensure_admin_role()
            print("Nexent 管理员已就绪。")
            return
        except (psycopg.OperationalError, psycopg.errors.UndefinedTable, URLError, TimeoutError):
            time.sleep(4)
    raise SystemExit(
        "等待 Nexent 数据库迁移和登录服务超时，请检查 nexent-config、kong 和 auth 的日志。"
    )


if __name__ == "__main__":
    main()
