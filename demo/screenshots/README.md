# Screenshot Guide

Capture these UI states for demo packaging:

1. Empty graph + controls visible
2. Live ingestion with event stream updates
3. Contradiction highlight + resolution animation
4. Query answer with clickable citations

Recommended command (when Playwright is available):

```bash
npx playwright screenshot --device="Desktop Chrome" http://localhost:8000/frontend demo/screenshots/ui-overview.png
```
