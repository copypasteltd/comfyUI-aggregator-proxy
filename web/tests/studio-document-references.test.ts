import test from "node:test";
import assert from "node:assert/strict";
import { documentNames, documentReferences } from "../src/services/studio-documents";

const document = (state: unknown) => JSON.stringify({ version: 0, state });

test("saved canvas references protect historical originals alongside primary originals", () => {
    const value = document({ projects: [{ nodes: [{ metadata: { storageKey: "studio:primary", references: ["studio:history", "image:local", "studio:history", "file:local"], images: [{ storageKey: "studio:nested", references: ["studio:variant"] }] } }] }] });
    assert.deepEqual(documentReferences(documentNames[0], value), ["studio:history", "studio:nested", "studio:primary", "studio:variant"]);
});

test("saved asset reference lists protect originals without a primary storage key", () => {
    const value = document({ assets: [{ metadata: { references: ["studio:asset-reference", "studio:asset-reference"] } }] });
    assert.deepEqual(documentReferences(documentNames[1], value), ["studio:asset-reference"]);
});

test("recoverable deleted projects retain their historical original references", () => {
    const value = document({ projects: [], deletedProjects: [{ project: { nodes: [{ metadata: { references: ["studio:trash-reference"] } }] } }] });
    assert.deepEqual(documentReferences(documentNames[0], value), ["studio:trash-reference"]);
});

test("document reference indexing ignores prompt literals and rejects malformed documents", () => {
    assert.deepEqual(documentReferences(documentNames[0], document({ projects: [{ nodes: [{ metadata: { prompt: "studio:literal", content: "studio:text", references: ["https://example.invalid/a.png", "plain text"] } }] }] })), []);
    for (const value of ["{broken", document({ projects: null }), JSON.stringify({ version: 99, state: { projects: [] } })]) assert.throws(() => documentReferences(documentNames[0], value));
});
