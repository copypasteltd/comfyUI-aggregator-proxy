import { journalGet, updateRequestBudget } from "./aggregator-journal";

function delayUntil(at: number, signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (at <= Date.now()) return Promise.resolve();
    return new Promise((resolve, reject) => {
        const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); };
        const abort = () => { cleanup(); reject(signal?.reason || new DOMException("Aborted", "AbortError")); };
        // No request timeout/retry limit: this wake-up derives only from server policy/hints.
        const timer = setTimeout(() => { cleanup(); resolve(); }, Math.min(at - Date.now(), 2_147_483_647));
        signal?.addEventListener("abort", abort, { once: true });
    });
}

export async function waitBusinessTurn(fingerprint: string, rpm: number | null, signal?: AbortSignal) {
    if (rpm !== null && (!Number.isFinite(rpm) || rpm <= 0)) throw new Error("当前 Key 没有可用的请求频率额度");
    let at = 0;
    await updateRequestBudget(fingerprint, (budget) => {
        at = Math.max(Date.now(), budget.nextAt, budget.pausedUntil);
        return { ...budget, nextAt: at + (rpm === null ? 0 : 60_000 / rpm) };
    });
    while (at > Date.now()) await delayUntil(at, signal);
    // A different page may have received Retry-After after this slot was reserved.
    for (;;) {
        const budget = await journalGet("budgets", fingerprint);
        if (!budget || budget.pausedUntil <= Date.now()) return;
        await delayUntil(budget.pausedUntil, signal);
    }
}

export async function pauseBusinessRequests(fingerprint: string, until: number) {
    await updateRequestBudget(fingerprint, (budget) => ({ ...budget, pausedUntil: Math.max(budget.pausedUntil, until) }));
}
