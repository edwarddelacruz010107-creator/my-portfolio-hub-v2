"""Legacy PostgreSQL connectivity smoke test.

This file intentionally reads the database URL from TEST_DATABASE_URL only.
Never hardcode Render/PostgreSQL credentials in source control. If a password
was previously committed or shared, rotate that database password immediately.
"""

import os

import psycopg2


def main() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL is required for this legacy Postgres connectivity test. "
            "Example: TEST_DATABASE_URL=postgresql://user:password@host:5432/dbname"
        )

    conn = psycopg2.connect(database_url)
    try:
        print("connected")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
