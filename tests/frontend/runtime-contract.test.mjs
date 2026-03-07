import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const appJs = fs.readFileSync(path.join(root, "frontend/js/app.js"), "utf8");

test("app defines KnowledgeGraph3D class and rendering stack", () => {
    assert.match(appJs, /class\s+KnowledgeGraph3D/);
    assert.match(appJs, /new\s+THREE\.WebGLRenderer/);
    assert.match(appJs, /new\s+OrbitControls/);
    assert.match(appJs, /forceSimulation\(/);
});

test("KnowledgeGraph3D supports node and edge operations", () => {
    assert.match(appJs, /addOrUpdateNode\(/);
    assert.match(appJs, /addOrUpdateEdge\(/);
    assert.match(appJs, /setGraph\(/);
    assert.match(appJs, /syncPositions\(/);
});

test("KnowledgeGraph3D includes contradiction and resolution animations", () => {
    assert.match(appJs, /highlightContradiction\(/);
    assert.match(appJs, /animateResolution\(/);
    assert.match(appJs, /createArcEffect\(/);
    assert.match(appJs, /pulseNode\(/);
});

test("KnowledgeGraph3D includes rendering optimizations", () => {
    assert.match(appJs, /refreshNodeRenderingMode\(/);
    assert.match(appJs, /new\s+THREE\.InstancedMesh/);
    assert.match(appJs, /updateNodeLod\(/);
    assert.match(appJs, /updateFrustumCulling\(/);
    assert.match(appJs, /maxParticleCount/);
});

test("KnowledgeGraph3D supports interactions and shortcuts", () => {
    assert.match(appJs, /handlePointerMove\(/);
    assert.match(appJs, /handlePointerClick\(/);
    assert.match(appJs, /nodeSearchBtn\.addEventListener/);
    assert.match(appJs, /event\.code\s*===\s*"KeyR"/);
    assert.match(appJs, /event\.code\s*===\s*"Space"/);
});

test("SessionSocket includes reconnect and ping support", () => {
    assert.match(appJs, /class\s+SessionSocket/);
    assert.match(appJs, /scheduleReconnect\(/);
    assert.match(appJs, /setInterval\(\(\)\s*=>\s*\{[\s\S]*socket\.send\("ping"\)/);
});

test("socket handler covers all expected event types", () => {
    const expectedEvents = [
        "agent_start",
        "concept_extracted",
        "connection_found",
        "contradiction_found",
        "resolution_start",
        "resolution_done",
        "loop_back",
        "node_stored",
        "ingestion_complete",
        "scholar_answer",
        "error",
    ];

    for (const eventName of expectedEvents) {
        const pattern = new RegExp(`case\\s+"${eventName}"`);
        assert.match(appJs, pattern);
    }

    assert.match(appJs, /event\.event\s*===\s*"event_batch"/);
    assert.match(appJs, /event\.event\s*===\s*"event_batch_compact"/);
});

test("app wires citation focus behavior for answer sources", () => {
    assert.match(appJs, /function\s+handleCitationClick\(/);
    assert.match(appJs, /citation-link/);
    assert.match(appJs, /graph\.focusNode\(/);
    assert.match(appJs, /showInspector\(/);
});
