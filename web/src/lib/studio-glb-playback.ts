import { AnimationMixer, type AnimationAction, type AnimationClip, type Object3D } from "three";

type Frames = { request: (callback: FrameRequestCallback) => number; cancel: (id: number) => void };

export function createGlbPlayback(root: Object3D, clips: AnimationClip[], render: () => void, failed: (reason: unknown) => void, frames: Frames = { request: (callback) => globalThis.requestAnimationFrame(callback), cancel: (id) => globalThis.cancelAnimationFrame(id) }) {
    const mixer = new AnimationMixer(root);
    let selected = 0, action: AnimationAction | undefined, playing = false, visible = true, disposed = false;
    let frame: number | undefined, previous: number | undefined, generation = 0;
    const stopFrames = () => { generation++; if (frame !== undefined) frames.cancel(frame); frame = undefined; previous = undefined; };
    const tick = (time: number, current: number) => {
        if (current !== generation) return;
        frame = undefined;
        if (disposed || !playing || !visible) return;
        try {
            mixer.update(previous === undefined ? 0 : (time - previous) / 1000);
            previous = time; render(); schedule();
        } catch (reason) { playing = false; stopFrames(); failed(reason); }
    };
    const schedule = () => { if (!disposed && playing && visible && frame === undefined) { const current = generation; frame = frames.request((time) => tick(time, current)); } };
    const pause = () => { playing = false; stopFrames(); };
    const select = (index: number) => {
        if (disposed) return;
        pause();
        if (!Number.isInteger(index) || !clips[index] || !Number.isFinite(clips[index].duration) || clips[index].duration <= 0) throw new Error("该动画没有可播放的时长，请保存原件检查");
        mixer.stopAllAction(); selected = index;
        action = mixer.clipAction(clips[index]).reset().play(); mixer.update(0); render();
    };
    return {
        select,
        play: () => { if (disposed) return; if (!action) select(selected); playing = true; schedule(); },
        pause,
        reset: () => select(selected),
        setVisible: (value: boolean) => { visible = value; stopFrames(); schedule(); },
        dispose: () => { if (disposed) return; disposed = true; pause(); mixer.stopAllAction(); mixer.uncacheRoot(root); },
    };
}
