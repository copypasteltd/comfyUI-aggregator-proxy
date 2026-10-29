import test from "node:test";
import assert from "node:assert/strict";
import { focusViewportForNode } from "../src/lib/canvas/canvas-node-geometry";

test("landscape focus keeps the selected image and node toolbar between fixed controls", () => {
    const size = { width: 844, height: 314 };
    const node = { position: { x: 1280, y: 720 }, width: 640, height: 640 };
    const frame = { topInset: 64, bottomInset: 76, above: 60 };
    const view = focusViewportForNode(node, size, frame);
    const top = view.y + node.position.y * view.k;
    const bottom = top + node.height * view.k;
    assert.ok(top - frame.above >= frame.topInset);
    assert.ok(bottom <= size.height - frame.bottomInset);
    assert.ok(view.x + node.position.x * view.k >= 0);
    assert.ok(view.x + (node.position.x + node.width) * view.k <= size.width);
});

test("unmanaged focus retains the original centered viewport without fixed insets", () => {
    const node = { position: { x: 1234, y: -567 }, width: 340, height: 240 };
    const view = focusViewportForNode(node, { width: 900, height: 600 });
    assert.equal(view.x + (node.position.x + node.width / 2) * view.k, 450);
    assert.equal(view.y + (node.position.y + node.height / 2) * view.k, 300);
});
