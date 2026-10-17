# NaSCon ’26 — Multi-Sport League Platform

## Run the backend

From the repository root, with your team salt (never commit the real value):

```bash
DAY1_SALT=<your-salt> docker compose up -d --build
```

The API listens on http://localhost:8080. `GET /health` returns `{"status":"ok"}` after Postgres is seeded.

Object storage uses `bitnamilegacy/minio:2024.6.13`. Docker Hub removed `minio/minio` in September 2026, so that name cannot be pulled on a fresh machine.

## Demo accounts

Password for every account: `Nascon@26`

| Role | Email |
|---|---|
| Platform Super-Admin | admin@nascon.test |
| League Organizer (league-alpha-1) | organizer@nascon.test |
| Club Manager (club-alpha-1) | manager@nascon.test |
| Player (player-alpha-1) | player@nascon.test |
| General Viewer | viewer@nascon.test |

The phone app has a server field. An emulator uses `http://10.0.2.2:8080`. A phone on the team hotspot uses `http://<laptop-lan-ip>:8080`.

## Tests

```bash
./run_tests.sh
```

## Mobile release

The installable Android build is `mobile/release/app-release.apk` (Android 10 / API 29 or newer).
