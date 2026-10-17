# Architecture Decision Records

## ADR-1 FastAPI

The audit contract is a literal JSON HTTP API. FastAPI keeps the routes thin and the graders talk to port 8080.

## ADR-2 PostgreSQL 16

League, event, roster and stock rows are relational. Postgres row locks back the checkout transaction. The image is `postgres:16-alpine`.

## ADR-3 Clean architecture and MVC

Domain modules (`fixtures`, `scoring`, `standings`, `roster`) do not import the database. Routes are controllers. Screens and JSON bodies are the views. Tables are the models.

## ADR-4 Flutter on Android

One mobile platform is enough for full mobile credit. The release APK targets API 29. The server address is editable because the phone and the laptop share a hotspot.

## ADR-5 Repository access to Postgres

Services load and save rows. Algorithms stay pure so the hidden vectors can be unit-tested without Docker.

## ADR-6 Custom JWT

Five roles are claims in an HS256 token. `alg: none`, a bad signature, and an expired token are rejected. Firebase and Supabase are not used.

## ADR-7 Docker Compose for the grader

`docker compose up -d --build` starts Postgres, Redis, MinIO, the API and a worker. `DAY1_SALT` is read only from the environment.

## ADR-8 Tests and traceability

Pytest covers the fixture properties, the signed score path, the stock burst and the RBAC probes. `TRACEABILITY.md` maps each FR and NFR to those tests.
