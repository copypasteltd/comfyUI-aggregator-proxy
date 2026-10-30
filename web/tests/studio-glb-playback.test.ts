import test from "node:test";
import assert from "node:assert/strict";
import { AnimationClip, NumberKeyframeTrack, Object3D } from "three";
import { createGlbPlayback } from "../src/lib/studio-glb-playback";

function fixture() {
    const root = new Object3D(), queue = new Map<number, FrameRequestCallback>();
    let id = 0, renders = 0;
    const errors: unknown[] = [];
    const clips = [new AnimationClip("向右", 2, [new NumberKeyframeTrack(".position[x]", [0, 2], [0, 2])]), new AnimationClip("向上", 2, [new NumberKeyframeTrack(".position[y]", [0, 2], [0, 4])])];
    const playback = createGlbPlayback(root, clips, () => { renders++; }, (error) => errors.push(error), { request: (callback) => { queue.set(++id, callback); return id; }, cancel: (value) => { queue.delete(value); } });
    const frame = (time: number) => { const callbacks = [...queue.values()]; queue.clear(); callbacks.forEach((callback) => callback(time)); };
    return { root, clips, playback, queue, frame, errors, renders: () => renders };
}

test("animation starts only on request, interpolates real Three tracks, pauses and resumes without time jumps", () => {
    const f = fixture(); assert.equal(f.queue.size, 0); assert.equal(f.renders(), 0);
    f.playback.play(); f.frame(1000); f.frame(1500); assert.equal(f.root.position.x, 0.5);
    f.playback.pause(); assert.equal(f.queue.size, 0); f.frame(100000); assert.equal(f.root.position.x, 0.5);
    f.playback.play(); f.frame(100000); assert.equal(f.root.position.x, 0.5); f.frame(100500); assert.equal(f.root.position.x, 1);
    f.playback.dispose(); assert.equal(f.queue.size, 0); assert.deepEqual(f.errors, []);
});
test("hidden preview suspends actual updates and resumes without counting background time", () => {
    const f = fixture(); f.playback.play(); f.frame(0); f.frame(500); f.playback.setVisible(false);
    assert.equal(f.queue.size, 0); f.frame(50000); assert.equal(f.root.position.x, 0.5);
    f.playback.setVisible(true); f.frame(50000); f.frame(50500); assert.equal(f.root.position.x, 1); f.playback.dispose();
});
test("clip selection and reset return to the selected start pose while pausing playback", () => {
    const f = fixture(); f.playback.play(); f.frame(0); f.frame(1000); assert.equal(f.root.position.x, 1);
    f.playback.select(1); assert.equal(f.root.position.x, 0); assert.equal(f.root.position.y, 0); assert.equal(f.queue.size, 0);
    f.playback.play(); f.frame(0); f.frame(1000); assert.equal(f.root.position.y, 2);
    f.playback.reset(); assert.equal(f.root.position.y, 0); assert.equal(f.queue.size, 0); f.playback.dispose();
});
test("dispose cancels pending frames and stale callbacks cannot render or restore playback", () => {
    const f = fixture(); f.playback.play(); const late = [...f.queue.values()][0]; f.playback.dispose();
    const before = f.renders(); late(9999); f.playback.play(); f.playback.setVisible(true); f.playback.reset();
    assert.equal(f.queue.size, 0); assert.equal(f.renders(), before); assert.equal(f.root.position.x, 0);
});
test("invalid or empty clips fail explicitly without starting an endless render loop", () => {
    const f = fixture(); assert.throws(() => f.playback.select(99), /时长/); f.clips[0].duration = 0;
    assert.throws(() => f.playback.play(), /时长/); assert.equal(f.queue.size, 0); f.playback.dispose();
});

test("invalid clip selection pauses existing playback, and stale frames cannot consume a new loop", () => {
    const f = fixture(); f.playback.play(); const stale = [...f.queue.values()][0];
    f.playback.pause(); f.playback.play(); const next = [...f.queue.keys()][0];
    const before = f.renders(); stale(10000); assert.equal(f.renders(), before); assert.equal(f.queue.size, 1); assert.ok(f.queue.has(next));
    f.frame(0); f.frame(500); assert.equal(f.root.position.x, 0.5);
    assert.throws(() => f.playback.select(99), /时长/); assert.equal(f.queue.size, 0); f.playback.dispose();
});
