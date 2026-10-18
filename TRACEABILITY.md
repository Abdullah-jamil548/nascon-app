# Traceability

| ID | Implementation | Tests |
|---|---|---|
| FR-1.1 | `backend/app/domain/sports.py`, `backend/app/domain/scoring.py`, `backend/app/services/ops.py` | `tests/test_api.py::test_mystery_sport_and_signed_score` |
| FR-1.2 | `backend/app/services/rest.py` `configure_structure` | structure endpoint covered by service tests through the API module |
| FR-1.3 | `backend/app/domain/fixtures.py`, `backend/app/services/rest.py` `reschedule` | `tests/test_domain_fixtures.py` |
| FR-1.4 | `backend/app/domain/standings.py` | `tests/test_api.py::test_mystery_sport_and_signed_score` |
| FR-2.1 | `backend/app/seed.py`, `backend/app/services/rest.py` `club_profile` | `tests/test_api.py::test_login_and_bad_sql_is_400` |
| FR-2.2 | `backend/app/domain/roster.py`, `backend/app/services/rest.py` | roster rules in `backend/app/domain/roster.py` |
| FR-2.3 | `backend/app/services/rest.py` `checkout`, `transition_order`, `pin_product` | `tests/test_api.py::test_checkout_burst_and_receipt` |
| FR-5.1 | `backend/app/services/rest.py` `authorize` | `tests/test_api.py::test_rbac_matrix_and_rate_limit` |
| NFR-3 | `backend/app/services/rest.py` `checkout` | `tests/test_api.py::test_checkout_burst_and_receipt` |
| NFR-4 | `mobile/lib/local_store.dart`, `mobile/lib/main.dart`, `POST /audit/v1/offline-sync` | offline queue in the app; sync route in `rest.offline_sync` |
| NFR-5 | `backend/app/core/security.py`, rate limit in `backend/app/main.py` | `tests/test_api.py::test_rbac_matrix_and_rate_limit` |
| NFR-7 | `docker-compose.yml`, `backend/Dockerfile`, `backend/app/seed.py` | `tests/test_api.py::test_health_and_reset_are_stable` |
| FR-3.1 | `backend/app/services/day2.py` `stream_key` | `tests/test_day2.py::test_stream_key_lifecycle_and_signature` |
| FR-3.2 | `backend/app/services/day2.py` `costream` | `tests/test_day2.py::test_costream_handoff_replay_and_limits` |
| FR-4.1 | `backend/app/services/day2.py` `qr_view`, `qr_target` | `tests/test_day2.py::test_qr_routes_exports_and_redirect` |
| FR-4.3 | `backend/app/main.py` `_reject_guest` | `tests/test_day2.py::test_cart_merge_is_idempotent_and_guest_writes_are_forbidden` |
| FR-4.4 | `backend/app/services/day2.py` `cart_merge` | `tests/test_day2.py::test_cart_merge_is_idempotent_and_guest_writes_are_forbidden` |
| FR-5.3 | `backend/app/services/day2.py` `moderate` | `tests/test_day2.py::test_moderation_masks_kicks_and_mutes` |
| NFR-1 | `backend/app/main.py` `/ws/matches/{match_id}` | websocket broadcast after `POST /audit/v1/events` |
| NFR-2 | `tests/k6_load.js` | `k6 run --summary-export=tests/reports/k6-summary.json tests/k6_load.js` |
| NFR-6 | `backend/app/services/day2.py` `metrics` | `tests/test_day2.py::test_metrics_privacy_and_guest_page` |
| NFR-8 | `backend/app/services/day2.py` `guest_page` | `tests/selenium/test_guest_grid.py` |
| NFR-9 | `backend/app/services/day2.py` `erase` | `tests/test_day2.py::test_metrics_privacy_and_guest_page` |
