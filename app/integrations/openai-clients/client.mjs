import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import OpenAI, { toFile } from 'openai';
import { exerciseMultimedia } from './multimedia.mjs';

const configuration = JSON.parse(await readFile(process.argv[2], 'utf8'));
const origin = new URL(configuration.origin);
assert.equal(origin.protocol, 'http:');
assert.equal(origin.hostname, '127.0.0.1');
assert.ok(origin.port && !origin.username && !origin.password && !origin.search && !origin.hash);
assert.equal(process.version, 'v24.22.1');
const version = JSON.parse(await readFile(new URL('./node_modules/openai/package.json', import.meta.url), 'utf8')).version;
assert.equal(version, '7.43.0');
const image = await readFile(configuration.image);
const sha = value => createHash('sha256').update(value).digest('hex');
const expectedHash = sha(image);
const cases = [];
const attempts = [];
let lastRequest = 0;
const row = (name, facts = {}) => cases.push({ case: name, passed: true, ...facts });
const generation = { model: 'protocol-text-to-image', prompt: 'Synthetic Node SDK compatibility', n: 1,
  size: '1024x1024', quality: 'standard', response_format: 'b64_json', user: 'sdk-node-fixture' };
const idem = 'sdk-node-generation-0001';
let multimediaJobs = 0;
const videoSamples = [];
const client = new OpenAI({ apiKey: configuration.token, baseURL: `${origin.origin}/v1`, maxRetries: 0,
  timeout: 70000, organization: null, project: null, logLevel: 'off',
  fetch: async (url, options) => {
    const target = new URL(url);
    assert.equal(target.origin, origin.origin);
    assert.ok(target.pathname.startsWith('/v1/'));
    const pause = Math.max(0, 3000 - (performance.now() - lastRequest));
    await new Promise(resolve => setTimeout(resolve, pause));
    lastRequest = performance.now();
    attempts.push({ method: options.method, path: target.pathname,
      idempotency: new Headers(options.headers).get('idempotency-key') });
    return fetch(url, { ...options, redirect: 'error' });
  },
});
function verifyImage(value) {
  assert.ok(Number.isInteger(value.created) && value.created > 0);
  assert.equal(value.data.length, 1);
  assert.equal(sha(Buffer.from(value.data[0].b64_json, 'base64')), expectedHash);
}
try {
  const models = await client.models.list();
  for (const model of models.data) {
    assert.equal(model.object, 'model');
    assert.ok(Number.isInteger(model.created) && model.created >= 0);
    assert.ok(typeof model.owned_by === 'string' && model.owned_by.length > 0);
  }
  assert.ok(models.data.some(value => value.id === 'protocol-text-to-image'));
  assert.equal((await client.models.retrieve('protocol-text-to-image')).id, 'protocol-text-to-image');
  row('models_list_and_retrieve');
  const raw = await client.images.generate(generation, { headers: { 'Idempotency-Key': idem } }).withResponse();
  verifyImage(raw.data);
  assert.equal(raw.response.status, 200);
  assert.ok(raw.request_id);
  assert.equal(raw.response.headers.get('x-request-id'), raw.request_id);
  row('generate_typed_base64', { http_status: 200, created_type: 'integer', image_sha256: expectedHash, request_id_present: true });
  verifyImage(await client.images.generate(generation, { headers: { 'Idempotency-Key': idem } }));
  row('same_key_replay');
  for (const retries of [0, 1]) {
    const before = attempts.length;
    await assert.rejects(client.images.generate({ ...generation, prompt: 'Synthetic changed prompt' }, {
      headers: { 'Idempotency-Key': idem }, maxRetries: retries,
    }), error => error instanceof OpenAI.ConflictError && error.status === 409 && error.code === 'task_conflict' && Boolean(error.requestID));
    assert.deepEqual(attempts.slice(before), Array.from({ length: retries + 1 }, () => ({
      method: 'POST', path: '/v1/images/generations', idempotency: idem,
    })));
    row(`conflict_with_retry_${retries}`, { http_status: 409, error_code: 'task_conflict', attempts: retries + 1, stable_idempotency: true });
  }
  await assert.rejects(client.images.generate({ ...generation, n: 2 }, { headers: { 'Idempotency-Key': 'sdk-node-invalid-n-0001' } }),
    error => error instanceof OpenAI.BadRequestError && error.status === 400 && error.code === 'invalid_request' && Boolean(error.requestID));
  row('unsupported_n_rejected', { http_status: 400, error_code: 'invalid_request', request_id_present: true });
  for (const [name, model, multiple, options] of [
    ['edit_single_multipart', 'protocol-image-to-image', false, { seed: 83 }],
    ['edit_multiple_multipart', 'protocol-references-to-image', true, { seed: 84,
      image_roles: [{ index: 0, role: 'character' }, { index: 1, role: 'scene' }] }],
  ]) {
    const first = await toFile(image, 'first.png', { type: 'image/png' });
    const files = multiple ? [first, await toFile(image, 'second.png', { type: 'image/png' })] : first;
    verifyImage(await client.images.edit({ model, prompt: 'Synthetic SDK edit', image: files,
      response_format: 'b64_json', workflow_options: JSON.stringify(options),
    }, { headers: { 'Idempotency-Key': `sdk-node-${name}` } }));
    row(name, { image_sha256: expectedHash, workflow_options_encoding: 'JSON form text' });
  }
  const asynchronous = await client.post('/images/generations/async', { body: generation, headers: { 'Idempotency-Key': idem } });
  assert.equal(asynchronous.object, 'image.generation.task');
  assert.equal(asynchronous.status, 'completed');
  row('async_extension_reuses_sync_job', { http_status: 202, extension: true });
  multimediaJobs = await exerciseMultimedia(client, configuration, image, row, videoSamples);
} finally {
  // Native fetch owns its pool. This bounded child process exits after all
  // response bodies are consumed; the harness owns and closes its process tree.
  if (typeof client.close === 'function') await client.close();
}
await writeFile(configuration.result, JSON.stringify({ sdk: 'openai-node', version, passed: true, cases,
  video_validation_samples: videoSamples,
  new_generation_jobs: 3 + multimediaJobs, requests_finished: true, minimum_inter_request_seconds: 3,
  production_openai_called: false }, null, 2) + '\n');
