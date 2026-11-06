/** Check the displayed artifact and the committed original, not a loading button's accessible name. */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

export async function assertStudioArtifact(page, jobId, allowedMediaTypes) {
    await page.getByText('载入产物', { exact: true }).waitFor({ state: 'detached', timeout: 45000 });
    const saved = await page.evaluate(async (id) => {
        const name = 'comfy-aggregator-studio:journal';
        if (!(await indexedDB.databases()).some((item) => item.name === name && item.version === 5)) throw new Error('Expected existing journal v5');
        const rows = await new Promise((resolve, reject) => {
            const request = indexedDB.open(name);
            request.onupgradeneeded = () => { request.transaction.abort(); reject(new Error('Never create or upgrade acceptance storage')); };
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const db = request.result, tx = db.transaction('results', 'readonly'), read = tx.objectStore('results').getAll();
                tx.oncomplete = () => { db.close(); resolve(read.result); };
                tx.onerror = tx.onabort = () => { db.close(); reject(tx.error || new Error('Read-only results transaction failed')); };
            };
        });
        const result = rows.find((row) => row.jobId === id);
        if (!result || !(result.blob instanceof Blob)) throw new Error('No committed original for this exact job');
        const data = await new Promise((resolve, reject) => {
            const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(reader.error); reader.readAsDataURL(result.blob);
        });
        return { content_id: result.metadata.id, job_id: result.jobId, bytes: result.blob.size, media_type: result.blob.type, metadata: result.metadata, data };
    }, jobId);
    const bytes = Buffer.from(saved.data, 'base64'), digest = createHash('sha256').update(bytes).digest('hex');
    assert.equal(bytes.length, saved.bytes); assert.equal(saved.bytes, saved.metadata.bytes);
    assert.equal(saved.media_type, saved.metadata.media_type); assert.equal(digest, saved.metadata.sha256);
    assert(allowedMediaTypes.includes(saved.media_type));
    const selector = saved.media_type.startsWith('image/') ? '[data-node-id] img' : saved.media_type.startsWith('video/') ? '[data-canvas-video]' : saved.media_type.startsWith('audio/') ? '[data-node-id] audio' : null;
    if (selector) await page.locator(selector).first().waitFor();
    else await page.getByRole('button', { name: '保存 GLB 原件', exact: true }).waitFor();
    return { job_id: saved.job_id, content_id: saved.content_id, bytes: saved.bytes, media_type: saved.media_type, sha256: digest, journal_version: 5, committed_original_verified: true, rendered_media_verified: true };
}
