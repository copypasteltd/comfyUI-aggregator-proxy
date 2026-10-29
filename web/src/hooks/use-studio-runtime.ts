import { useEffect } from "react";
import { localTasks, refreshIntent, subscribeStudioTasks } from "@/services/aggregator-engine";
import { updateRequestBudget } from "@/services/aggregator-journal";
import { terminalStatuses } from "@/services/api/aggregator/types";
import { useStudioStore } from "@/stores/use-studio-store";

// Poll cadence comes from the server's Retry-After. Missing hints pause automatic polling.
export function useStudioRuntime() {
    const client = useStudioStore((state) => state.client);
    useEffect(() => {
        if (!client) return;
        let disposed = false;
        let running = false;
        let requested = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const controller = new AbortController();
        const available = () => !document.hidden && navigator.onLine !== false;
        const wake = () => {
            requested = true;
            if (timer) clearTimeout(timer);
            if (!running) void pump();
        };
        const pump = async () => {
            if (disposed || running || !available()) return;
            running = true; requested = false;
            let nextAt = Infinity;
            try {
                const tasks = (await localTasks()).filter((intent) => intent.job && !terminalStatuses.has(intent.job.status) && !intent.pollPaused && intent.pollIntervalMs);
                for (const intent of tasks.sort((a, b) => (a.retryAt || 0) - (b.retryAt || 0))) {
                    if (disposed || !available()) break;
                    if (intent.retryAt && intent.retryAt > Date.now()) { nextAt = Math.min(nextAt, intent.retryAt); continue; }
                    let claimed = false;
                    const reservation = await updateRequestBudget(`poll:${client.fingerprint}:${intent.job!.id}`, (budget) => {
                        if (budget.nextAt > Date.now()) return budget;
                        claimed = true;
                        return { ...budget, nextAt: Date.now() + intent.pollIntervalMs! };
                    });
                    nextAt = Math.min(nextAt, reservation.nextAt);
                    if (claimed && !disposed) await refreshIntent(intent.id, controller.signal).catch(() => {});
                }
            } catch { /* Durable errors remain visible in the task view; no POST is issued here. */ }
            finally {
                running = false;
                if (!disposed) {
                    if (requested) queueMicrotask(() => void pump());
                    else if (Number.isFinite(nextAt)) timer = setTimeout(wake, Math.min(2_147_483_647, Math.max(0, nextAt - Date.now())));
                }
            }
        };
        const unsubscribe = subscribeStudioTasks(wake);
        document.addEventListener("visibilitychange", wake);
        window.addEventListener("online", wake);
        window.addEventListener("pageshow", wake);
        wake();
        return () => { disposed = true; if (timer) clearTimeout(timer); controller.abort(); unsubscribe(); document.removeEventListener("visibilitychange", wake); window.removeEventListener("online", wake); window.removeEventListener("pageshow", wake); };
    }, [client]);
}
