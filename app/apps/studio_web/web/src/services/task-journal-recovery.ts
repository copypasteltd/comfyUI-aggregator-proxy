import { journalList, journalTransaction, type IdentityRef, type Intent } from "./aggregator-journal";
import { createTaskJournal, readTaskJournal, readTaskJournalFromOrigin, sameJournalIdentity, taskJournalRecord, taskSubmissionSignature } from "./task-journal-format";
import { assertBatchMembers, batchKey } from "./aggregator-batch";

export async function exportTaskJournal(identity: IdentityRef) {
    const records = await journalList("intents");
    return createTaskJournal(records.filter((task) => sameJournalIdentity(task.identity, identity)), identity);
}

// Only the transaction completion permits the success UI; no network or canvas writes occur here.
export function restoreTaskJournal(source: string, identity: IdentityRef, stillConnected: () => boolean) {
    const backup = readTaskJournal(source, identity);
    return restoreRecords(backup, stillConnected);
}

export function restoreOriginalTaskJournal(source: string, origin: string, stillConnected: () => boolean) {
    return restoreRecords(readTaskJournalFromOrigin(source, origin), stillConnected);
}

function restoreRecords(backup: ReturnType<typeof readTaskJournal>, stillConnected: () => boolean) {
    return journalTransaction<{ added: number; kept: number }>(["intents"], "readwrite", (tx, result, abort) => {
        const store = tx.objectStore("intents");
        const request = store.getAll();
        request.onsuccess = () => {
            try {
                if (!stillConnected()) throw new Error("连接已改变，请使用原连接重新预览清单");
                const existing = request.result as Intent[];
                if (existing.some((task) => task.version !== 1)) throw new Error("本机存在未知版本任务，已保留原记录，未导入清单");
                const byId = new Map(existing.map((task) => [task.id, task]));
                const groups = new Set(backup.tasks.filter((task) => task.batch).map(batchKey));
                assertBatchMembers([...existing.filter((task) => task.batch && groups.has(batchKey(task))), ...backup.tasks.filter((task) => !byId.has(task.id))]);
                const active = new Set(existing.filter((task) => task.activeNodeKey).map((task) => task.activeNodeKey));
                let added = 0, kept = 0;
                for (const task of backup.tasks) {
                    const original = byId.get(task.id);
                    if (original) {
                        if (taskSubmissionSignature(taskJournalRecord(original)) !== taskSubmissionSignature(task) || task.job && original.job && task.job.id !== original.job.id) throw new Error("本机同ID记录与备份原提交冲突，整批未导入");
                        kept++; continue;
                    }
                    if (existing.some((value) => sameJournalIdentity(value.identity, task.identity) && value.idempotencyKey === task.idempotencyKey)) throw new Error("本机已存在相同幂等键的其他记录，整批未导入");
                    const activeNodeKey = task.stage === "terminal" ? undefined : `${task.identity.fingerprint}:${task.projectId}:${task.nodeId}`;
                    if (activeNodeKey && active.has(activeNodeKey)) throw new Error("该节点已有活动任务，整批未导入");
                    const value: Intent = { ...task, recovered: { version: 1, archiveSha256: backup.sha256, importedAt: Date.now() }, pollPaused: true, ...(activeNodeKey ? { activeNodeKey } : {}) };
                    store.add(value);
                    if (activeNodeKey) active.add(activeNodeKey);
                    added++;
                }
                result({ added, kept });
            } catch (error) { abort(error); }
        };
    });
}
