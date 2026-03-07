# Launch Runbook

## Launch Sequence

1. Verify deployment checklist completion.
2. Seed demo content if operating in demo mode.
3. Perform live smoke flow:
   - ingest document
   - observe events in stream
   - run query and verify citations
4. Capture screenshots/video clips from `demo/screenshots` and `demo/video` storyboard.
5. Publish release notes.

## Monitoring Plan

- API health: `/health`
- Error logs: `echosystem.log`
- Event throughput: websocket stream behavior and UI event log consistency
- Latency checks:
  - query under target budget
  - ingestion under target budget

## Rollback Plan

1. Stop current container: `docker compose down`
2. Pull previous known-good image tag
3. Restore previous env and restart
4. Verify `/health` and graph stats endpoint
