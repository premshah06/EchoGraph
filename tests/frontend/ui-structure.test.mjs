import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const indexHtml = fs.readFileSync(path.join(root, "frontend/index.html"), "utf8");
const stylesheet = fs.readFileSync(path.join(root, "frontend/css/style.css"), "utf8");

test("index.html includes responsive navbar and footer", () => {
    assert.match(indexHtml, /id="navToggle"/);
    assert.match(indexHtml, /id="navLinks"/);
    assert.match(indexHtml, /class="site-footer"/);
});

test("index.html includes graph, inspector, and event log panels", () => {
    assert.match(indexHtml, /id="graph-container"/);
    assert.match(indexHtml, /id="inspectorBody"/);
    assert.match(indexHtml, /id="eventLog"/);
    assert.match(indexHtml, /id="loopBadge"/);
});

test("index.html includes upload, URL, and query controls", () => {
    assert.match(indexHtml, /id="fileInput"/);
    assert.match(indexHtml, /id="documentContent"/);
    assert.match(indexHtml, /id="urlInput"/);
    assert.match(indexHtml, /id="queryInput"/);
    assert.match(indexHtml, /id="queryBtn"/);
});

test("index.html includes demo banner and legend", () => {
    assert.match(indexHtml, /id="demoBanner"/);
    assert.match(indexHtml, /class="legend"/);
});

test("style.css defines light theme tokens", () => {
    assert.match(stylesheet, /--bg-canvas:\s*#f5f9ff/i);
    assert.match(stylesheet, /--text-primary:\s*#1d2a3d/i);
    assert.match(stylesheet, /--bg-shell:\s*#ffffff/i);
});

test("style.css includes mobile/tablet responsive breakpoints", () => {
    assert.match(stylesheet, /@media\s*\(max-width:\s*1280px\)/);
    assert.match(stylesheet, /@media\s*\(max-width:\s*980px\)/);
    assert.match(stylesheet, /@media\s*\(max-width:\s*760px\)/);
});
