import assert from "node:assert/strict";
import test from "node:test";

import {
    escapeHtml,
    mapErrorToUserMessage,
    parseEventTimestamp,
    renderAnswerTemplate,
} from "../../frontend/js/testable_utils.js";

test("escapeHtml sanitizes special characters", () => {
    const raw = '<script>alert("x")</script>';
    const escaped = escapeHtml(raw);
    assert.equal(escaped, "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
});

test("mapErrorToUserMessage maps rate limit errors", () => {
    const mapped = mapErrorToUserMessage(new Error("Rate limit exceeded"));
    assert.equal(mapped.title, "Rate limit reached");
    assert.match(mapped.suggestion, /retry/i);
});

test("mapErrorToUserMessage maps demo mode errors", () => {
    const mapped = mapErrorToUserMessage(
        new Error("Ingestion is disabled in demo mode")
    );
    assert.equal(mapped.title, "Demo mode restriction");
});

test("mapErrorToUserMessage falls back for unknown errors", () => {
    const mapped = mapErrorToUserMessage(new Error("something odd"));
    assert.equal(mapped.title, "Operation failed");
    assert.equal(mapped.detail, "something odd");
});

test("parseEventTimestamp parses valid timestamp", () => {
    const parsed = parseEventTimestamp({ timestamp: "2026-03-06T12:00:00Z" });
    assert.equal(parsed.toISOString(), "2026-03-06T12:00:00.000Z");
});

test("parseEventTimestamp falls back to now on invalid timestamp", () => {
    const before = Date.now();
    const parsed = parseEventTimestamp({ timestamp: "not-a-date" });
    const after = Date.now();
    assert.ok(parsed.getTime() >= before && parsed.getTime() <= after + 1000);
});

test("renderAnswerTemplate creates citation buttons", () => {
    const html = renderAnswerTemplate(
        "According to node #[abc-1234], a claim is supported.",
        ["abc-1234", "def-5678"]
    );

    assert.match(html, /class="citation-link"/);
    assert.match(html, /data-node-id="abc-1234"/);
    assert.match(html, /data-node-id="def-5678"/);
    assert.match(html, /Sources:/);
});
