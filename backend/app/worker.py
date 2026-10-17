"""Queue worker. Order events are already durable in Postgres; this drains Redis."""

import os
import time


def main() -> None:
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        import redis
    except ImportError:
        return
    while True:
        try:
            client = redis.Redis.from_url(url)
            client.ping()
            messages = client.xread({"domain-events": "0-0"}, count=50, block=5000)
            for _stream, entries in messages or []:
                for entry_id, _fields in entries:
                    client.xdel("domain-events", entry_id)
        except Exception:
            time.sleep(2)


if __name__ == "__main__":
    main()
