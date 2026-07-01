import assert from "node:assert/strict";
import test from "node:test";

import {
    escapeHtml,
    mapErrorToUserMessage,
    parseEventTimestamp,
    renderAnswerTemplate,
    createIngestHistoryEntry,
    finalizeIngestHistoryEntry,
    renderIngestHistoryList,
    findShortestPath,
    createQueryHistoryEntry,
    renderQueryHistoryList,
    renderSourceBreakdown,
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

test("createIngestHistoryEntry builds a running entry with defaults", () => {
    const now = new Date("2026-06-29T10:00:00Z");
    const entry = createIngestHistoryEntry("whitepaper.txt", "document", now);

    assert.equal(entry.source, "whitepaper.txt");
    assert.equal(entry.type, "document");
    assert.equal(entry.status, "running");
    assert.equal(entry.nodes, 0);
    assert.equal(entry.edges, 0);
    assert.equal(entry.finishedAt, null);
    assert.equal(entry.startedAt, now);
    assert.equal(entry.id, now.getTime());
});

test("createIngestHistoryEntry normalizes unknown type to document", () => {
    const entry = createIngestHistoryEntry("source.txt", "carrier-pigeon");
    assert.equal(entry.type, "document");
});

test("createIngestHistoryEntry falls back to 'unknown' for empty source", () => {
    const entry = createIngestHistoryEntry("", "url");
    assert.equal(entry.source, "unknown");
});

test("finalizeIngestHistoryEntry marks entry done with counts", () => {
    const startedAt = new Date("2026-06-29T10:00:00Z");
    const finishedAt = new Date("2026-06-29T10:00:05Z");
    const entry = createIngestHistoryEntry("doc.txt", "document", startedAt);

    finalizeIngestHistoryEntry(entry, 12, 7, "done", finishedAt);

    assert.equal(entry.status, "done");
    assert.equal(entry.nodes, 12);
    assert.equal(entry.edges, 7);
    assert.equal(entry.finishedAt, finishedAt);
});

test("finalizeIngestHistoryEntry handles null entry gracefully", () => {
    assert.equal(finalizeIngestHistoryEntry(null, 1, 1, "done"), null);
});

test("renderIngestHistoryList shows empty state with no entries", () => {
    assert.match(renderIngestHistoryList([]), /No ingestions yet\./);
    assert.match(renderIngestHistoryList(undefined), /No ingestions yet\./);
});

test("renderIngestHistoryList renders a running entry without counts", () => {
    const entry = createIngestHistoryEntry("live-doc.txt", "document", new Date());
    const html = renderIngestHistoryList([entry]);

    assert.match(html, /ingest-history-item running/);
    assert.match(html, /live-doc\.txt/);
    assert.match(html, /Processing…/);
});

test("renderIngestHistoryList renders a completed entry with node\/edge counts", () => {
    const startedAt = new Date("2026-06-29T10:00:00Z");
    const finishedAt = new Date("2026-06-29T10:00:03Z");
    const entry = createIngestHistoryEntry("report.pdf", "document", startedAt);
    finalizeIngestHistoryEntry(entry, 5, 3, "done", finishedAt);

    const html = renderIngestHistoryList([entry]);

    assert.match(html, /ingest-history-item done/);
    assert.match(html, /5 nodes · 3 edges/);
    assert.match(html, /3\.0s/);
});

test("renderIngestHistoryList escapes the source label", () => {
    const entry = createIngestHistoryEntry("<img src=x onerror=alert(1)>", "document");
    const html = renderIngestHistoryList([entry]);

    assert.doesNotMatch(html, /<img src=x/);
    assert.match(html, /&lt;img/);
});

test("renderIngestHistoryList uses a link icon for url type entries", () => {
    const entry = createIngestHistoryEntry("https://example.com", "url");
    const html = renderIngestHistoryList([entry]);

    assert.match(html, /\u{1F517}/u);
});

test("findShortestPath finds a direct connection", () => {
    const edges = [{ source: "a", target: "b" }];
    assert.deepEqual(findShortestPath(edges, "a", "b"), ["a", "b"]);
});

test("findShortestPath finds the shortest multi-hop path", () => {
    const edges = [
        { source: "a", target: "b" },
        { source: "b", target: "c" },
        { source: "a", target: "d" },
        { source: "d", target: "c" },
        { source: "c", target: "e" },
    ];
    const path = findShortestPath(edges, "a", "e");
    assert.equal(path[0], "a");
    assert.equal(path[path.length - 1], "e");
    assert.equal(path.length, 4);
});

test("findShortestPath treats edges as undirected", () => {
    const edges = [{ source: "b", target: "a" }];
    assert.deepEqual(findShortestPath(edges, "a", "b"), ["a", "b"]);
});

test("findShortestPath returns single node when start equals end", () => {
    assert.deepEqual(findShortestPath([{ source: "a", target: "b" }], "a", "a"), ["a"]);
});

test("findShortestPath returns empty array when no path exists", () => {
    const edges = [
        { source: "a", target: "b" },
        { source: "x", target: "y" },
    ];
    assert.deepEqual(findShortestPath(edges, "a", "y"), []);
});

test("findShortestPath returns empty array for unknown nodes", () => {
    assert.deepEqual(findShortestPath([{ source: "a", target: "b" }], "a", "z"), []);
});

test("findShortestPath returns empty array for missing start or end id", () => {
    assert.deepEqual(findShortestPath([{ source: "a", target: "b" }], "", "b"), []);
    assert.deepEqual(findShortestPath([{ source: "a", target: "b" }], "a", ""), []);
});

test("createQueryHistoryEntry builds entry with correct fields", () => {
    const now = new Date("2026-06-30T10:00:00Z");
    const entry = createQueryHistoryEntry("What is X?", "X is Y.", ["node-1"], now);

    assert.equal(entry.query, "What is X?");
    assert.equal(entry.answer, "X is Y.");
    assert.deepEqual(entry.sources, ["node-1"]);
    assert.equal(entry.timestamp, now);
    assert.equal(entry.id, now.getTime());
});

test("createQueryHistoryEntry defaults sources to empty array", () => {
    const entry = createQueryHistoryEntry("Q?", "A.");
    assert.deepEqual(entry.sources, []);
});

test("createQueryHistoryEntry handles empty query gracefully", () => {
    const entry = createQueryHistoryEntry("", "answer");
    assert.equal(entry.query, "");
});

test("renderQueryHistoryList shows empty state when no entries", () => {
    assert.match(renderQueryHistoryList([]), /No queries yet\./);
    assert.match(renderQueryHistoryList(undefined), /No queries yet\./);
});

test("renderQueryHistoryList renders a query entry with text and source count", () => {
    const entry = createQueryHistoryEntry("What is A?", "A is B.", ["node-1", "node-2"], new Date("2026-06-30T10:00:00Z"));
    const html = renderQueryHistoryList([entry]);

    assert.match(html, /What is A\?/);
    assert.match(html, /2 sources/);
    assert.match(html, /query-history-item/);
});

test("renderQueryHistoryList escapes the query text", () => {
    const entry = createQueryHistoryEntry('<script>alert(1)</script>', "safe answer", [], new Date());
    const html = renderQueryHistoryList([entry]);

    assert.doesNotMatch(html, /<script>/);
    assert.match(html, /&lt;script&gt;/);
});

test("renderQueryHistoryList shows singular 'source' for 1 result", () => {
    const entry = createQueryHistoryEntry("Q?", "A.", ["node-1"], new Date());
    const html = renderQueryHistoryList([entry]);

    assert.match(html, /1 source[^s]/);
});

test("renderSourceBreakdown shows empty state when no sources", () => {
    assert.match(renderSourceBreakdown([]), /No sources yet\./);
    assert.match(renderSourceBreakdown(undefined), /No sources yet\./);
});

test("renderSourceBreakdown renders source name, node count and confidence", () => {
    const html = renderSourceBreakdown([
        { source: "paper.pdf", node_count: 5, avg_confidence: 0.9 },
    ]);
    assert.match(html, /paper\.pdf/);
    assert.match(html, /5 nodes/);
    assert.match(html, /90%/);
});

test("renderSourceBreakdown escapes source name", () => {
    const html = renderSourceBreakdown([
        { source: "<img onerror=x>", node_count: 1, avg_confidence: 0.5 },
    ]);
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
});

test("renderSourceBreakdown widest bar is always 100%", () => {
    const html = renderSourceBreakdown([
        { source: "a.txt", node_count: 10, avg_confidence: 1.0 },
        { source: "b.txt", node_count: 5, avg_confidence: 0.7 },
    ]);
    assert.match(html, /width:100%/);
    assert.match(html, /width:50%/);
});

test("renderSourceBreakdown uses singular 'node' for count of 1", () => {
    const html = renderSourceBreakdown([
        { source: "solo.txt", node_count: 1, avg_confidence: 0.8 },
    ]);
    assert.match(html, /1 node[^s]/);
});
