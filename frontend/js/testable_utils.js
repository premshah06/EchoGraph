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
