export function findShortestPath(edges, startId, endId) {
    if (!startId || !endId) return [];
    if (startId === endId) return [startId];

    const adjacency = new Map();
    for (const edge of edges) {
        if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
        if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
        adjacency.get(edge.source).push(edge.target);
        adjacency.get(edge.target).push(edge.source);
    }

    if (!adjacency.has(startId) || !adjacency.has(endId)) return [];

    const visited = new Set([startId]);
    const queue = [[startId]];

    while (queue.length) {
        const path = queue.shift();
        const node = path[path.length - 1];

        if (node === endId) return path;

        for (const neighbor of adjacency.get(node) || []) {
            if (visited.has(neighbor)) continue;
            visited.add(neighbor);
            queue.push([...path, neighbor]);
        }
    }

    return [];
}

export function escapeHtml(text) {
    return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

export function mapErrorToUserMessage(error) {
    const rawMessage = String(error?.message || "Unexpected error");
    const message = rawMessage.toLowerCase();

    if (message.includes("rate limit")) {
        return {
            title: "Rate limit reached",
            detail: "Too many requests were sent in a short time.",
            suggestion: "Wait a minute and retry.",
        };
    }

    if (message.includes("demo mode")) {
        return {
            title: "Demo mode restriction",
            detail: "Ingestion is disabled while running without an OpenAI API key.",
            suggestion: "Configure OPENAI_API_KEY to enable ingestion.",
        };
    }

    if (message.includes("failed to fetch")) {
        return {
            title: "Network error",
            detail: "The backend could not be reached.",
            suggestion: "Verify the API server is running and reachable.",
        };
    }

    return {
        title: "Operation failed",
        detail: rawMessage,
        suggestion: "Check inputs and try again.",
    };
}

export function parseEventTimestamp(event) {
    const ts = event?.timestamp;
    if (!ts) {
        return new Date();
    }

    const parsed = new Date(ts);
    if (Number.isNaN(parsed.getTime())) {
        return new Date();
    }

    return parsed;
}

export function createIngestHistoryEntry(sourceLabel, type, now = new Date()) {
    return {
        id: now.getTime(),
        source: sourceLabel || "unknown",
        type: type === "url" ? "url" : "document",
        startedAt: now,
        finishedAt: null,
        nodes: 0,
        edges: 0,
        status: "running",
    };
}

export function finalizeIngestHistoryEntry(entry, nodes, edges, status = "done", now = new Date()) {
    if (!entry) return entry;
    entry.finishedAt = now;
    entry.nodes = nodes;
    entry.edges = edges;
    entry.status = status;
    return entry;
}

export function renderIngestHistoryList(entries) {
    if (!entries || !entries.length) {
        return '<p class="ingest-history-empty">No ingestions yet.</p>';
    }

    return entries
        .map((entry) => {
            const started = entry.startedAt.toLocaleString(undefined, {
                dateStyle: "short",
                timeStyle: "short",
            });
            const duration = entry.finishedAt
                ? `${((entry.finishedAt - entry.startedAt) / 1000).toFixed(1)}s`
                : "…";
            const statusClass =
                entry.status === "done" ? "done" : entry.status === "error" ? "error" : "running";
            const typeIcon = entry.type === "url" ? "\u{1F517}" : "\u{1F4C4}";

            return `
      <div class="ingest-history-item ${statusClass}">
        <div class="ingest-history-head">
          <span class="ingest-history-source">${typeIcon} ${escapeHtml(entry.source)}</span>
          <span class="ingest-history-status ${statusClass}">${entry.status}</span>
        </div>
        <div class="ingest-history-meta">
          <span>${started}</span>
          <span>${duration}</span>
          ${
              entry.status !== "running"
                  ? `<span>${entry.nodes} nodes · ${entry.edges} edges</span>`
                  : "<span>Processing…</span>"
          }
        </div>
      </div>`;
        })
        .join("");
}

export function createQueryHistoryEntry(query, answer, sources = [], now = new Date()) {
    return {
        id: now.getTime(),
        query: query || "",
        answer: answer || "",
        sources: Array.isArray(sources) ? sources : [],
        timestamp: now,
    };
}

export function renderQueryHistoryList(entries) {
    if (!entries || !entries.length) {
        return '<p class="query-history-empty">No queries yet.</p>';
    }

    return entries
        .map((entry) => {
            const time = entry.timestamp instanceof Date
                ? entry.timestamp.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })
                : new Date(entry.timestamp).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
            const sourceCount = entry.sources.length;

            return `
      <div class="query-history-item" data-query-id="${entry.id}">
        <div class="query-history-q">${escapeHtml(entry.query)}</div>
        <div class="query-history-meta">
          <span>${time}</span>
          <span>${sourceCount} source${sourceCount !== 1 ? "s" : ""}</span>
        </div>
      </div>`;
        })
        .join("");
}

export function renderSourceBreakdown(sources) {
    if (!sources || !sources.length) {
        return '<p class="source-breakdown-empty">No sources yet.</p>';
    }

    const maxCount = Math.max(...sources.map((s) => s.node_count));

    return sources
        .map((s) => {
            const pct = Math.round((s.node_count / maxCount) * 100);
            const confPct = Math.round(s.avg_confidence * 100);
            const confColor = confPct >= 80 ? "#6ee7b7" : confPct >= 50 ? "#fcd34d" : "#fca5a5";

            return `
      <div class="source-stat">
        <div class="source-stat-head">
          <span class="source-stat-name">${escapeHtml(s.source)}</span>
          <span class="source-stat-count">${s.node_count} node${s.node_count !== 1 ? "s" : ""}</span>
        </div>
        <div class="source-stat-bar-wrap">
          <div class="source-stat-bar" style="width:${pct}%"></div>
        </div>
        <div class="source-stat-conf" style="color:${confColor};">avg confidence ${confPct}%</div>
      </div>`;
        })
        .join("");
}

export function renderAnswerTemplate(answer, sources = []) {
    const safeAnswer = escapeHtml(answer)
        .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.*?)\*/g, "<em>$1</em>")
        .replace(/^(\d+)\. /gm, "<br><strong>$1.</strong> ")
        .replace(/\n/g, "<br>");
    const withCitations = safeAnswer.replace(
        /node #\[([a-zA-Z0-9\-]+)\]/g,
        (_full, nodeId) =>
            `node <button class="citation-link" data-node-id="${escapeHtml(nodeId)}">#${escapeHtml(
                nodeId.slice(0, 8)
            )}</button>`
    );

    const sourceButtons = sources
        .map(
            (sourceId) =>
                `<button class="citation-link" data-node-id="${escapeHtml(sourceId)}">#${escapeHtml(
                    sourceId.slice(0, 8)
                )}</button>`
        )
        .join(" ");

    return `
        <div>${withCitations}</div>
        <hr>
        <strong>Sources:</strong>
        <div>${sourceButtons || "None"}</div>
    `;
}
