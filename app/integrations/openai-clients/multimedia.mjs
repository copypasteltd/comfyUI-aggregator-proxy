import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

export async function exerciseMultimedia(client, configuration, image, row, samples) {
  const videoBytes = await readFile(configuration.video);
  const audioBytes = await readFile(configuration.audio);
  const sha = value => createHash('sha256').update(value).digest('hex');
  const videoHash = sha(videoBytes);
  const audioHash = sha(audioBytes);
  const verifyVideo = value => {
    assert.match(value.id, /^rc_[a-f0-9]{32}$/);
    assert.equal(value.object, 'video');
    assert.ok(['queued', 'in_progress', 'completed', 'failed'].includes(value.status));
    assert.ok(Number.isInteger(value.created_at) && value.created_at > 0);
    assert.equal(value.seconds, '5');
    assert.equal(value.size, '1024x1024');
    assert.equal(value.poll_url, `/v1/videos/${value.id}`);
    assert.ok(!Object.hasOwn(value, 'progress'));
    samples.push(structuredClone(value));
  };
  async function terminal(id, video) {
    const deadline = performance.now() + 90000;
    for (let count = 0; count < 25; count++) {
      const value = await client.get(`${video ? '/videos/' : '/jobs/'}${id}`);
      if (video) verifyVideo(value);
      if (['completed', 'failed', 'cancelled', 'expired', 'succeeded'].includes(value.status)) {
        assert.equal(value.status, video ? 'completed' : 'succeeded');
        return value;
      }
      assert.ok(performance.now() < deadline, 'multimedia poll deadline');
    }
    throw new Error('sdk_multimedia_poll_limit');
  }
  const generation = { model: 'protocol-text-to-video', prompt: 'Synthetic Node SDK video', seconds: '5', size: '1024x1024' };
  const videoKey = 'sdk-node-video-0001';
  const created = await client.videos.create(generation, { headers: { 'Idempotency-Key': videoKey } }).withResponse();
  assert.equal(created.response.status, 202);
  assert.ok(created.request_id);
  verifyVideo(created.data);
  row('video_multipart_create_frozen_specification', { http_status: 202, seconds: '5', size: '1024x1024', runtime_fields_checked: true, official_type_validated: false });
  const replay = await client.post('/videos', { body: generation, headers: { 'Idempotency-Key': videoKey } });
  verifyVideo(replay);
  assert.equal(replay.id, created.data.id);
  row('video_json_multipart_replay', { same_job: true, stable_idempotency: true });
  await terminal(created.data.id, true);
  const videoResponse = await client.videos.downloadContent(created.data.id);
  assert.equal(videoResponse.status, 200);
  assert.equal(sha(Buffer.from(await videoResponse.arrayBuffer())), videoHash);
  row('video_sdk_binary_download', { sha256: videoHash, bytes: videoBytes.length, response_consumed: true });
  const range = await client.videos.downloadContent(created.data.id, {}, { headers: { Range: 'bytes=0-63' } });
  assert.equal(range.status, 206);
  assert.deepEqual(Buffer.from(await range.arrayBuffer()), videoBytes.subarray(0, 64));
  assert.equal(range.headers.get('content-range'), `bytes 0-63/${videoBytes.length}`);
  const head = await client.request({ method: 'head', path: `/videos/${created.data.id}/content` }).asResponse();
  assert.equal(head.status, 200);
  assert.equal((await head.arrayBuffer()).byteLength, 0);
  assert.equal(Number(head.headers.get('content-length')), videoBytes.length);
  row('video_range_and_head', { range_status: 206, head_status: 200, byte_range_verified: true });

  const assets = [];
  for (const index of [0, 1]) {
    const form = new FormData();
    form.append('file', new File([image], `sdk-reference-${index}.png`, { type: 'image/png' }));
    const asset = await client.post('/assets', { body: form, headers: { 'Idempotency-Key': `sdk-node-reference-${index}` } });
    assert.match(asset.id, /^asset_/);
    assets.push(asset.id);
  }
  assert.notEqual(assets[0], assets[1]);
  row('video_reference_asset_uploads', { extension: true, assets: 2 });
  const references = [{ asset_id: assets[0], role: 'character' }, { asset_id: assets[1], role: 'scene' }];
  const referenceBody = { ...generation, model: 'protocol-references-to-video', workflow_options: { references } };
  const referenceOptions = { body: referenceBody, headers: { 'Idempotency-Key': 'sdk-node-reference-video-0001' } };
  const reference = await client.post('/videos', referenceOptions);
  verifyVideo(reference);
  assert.equal((await client.post('/videos', referenceOptions)).id, reference.id);
  await terminal(reference.id, true);
  const referenceContent = await client.videos.downloadContent(reference.id);
  assert.equal(sha(Buffer.from(await referenceContent.arrayBuffer())), videoHash);
  row('multiple_reference_video_extension', { extension: true, references: 2, ordered_roles: ['character', 'scene'], same_job: true, sha256: videoHash });

  const audioBody = { model: 'protocol-text-to-audio', task_type: 'text_to_audio', input: { prompt: 'Synthetic Node SDK audio' },
    parameters: { quality: 'standard', seconds: 2 } };
  const audioOptions = { body: audioBody, headers: { 'Idempotency-Key': 'sdk-node-audio-0001' } };
  const audio = await client.post('/jobs', audioOptions);
  assert.equal(audio.object, 'job');
  assert.equal((await client.post('/jobs', audioOptions)).id, audio.id);
  row('audio_async_extension_replay', { extension: true, same_job: true, speech_endpoint_called: false });
  const finished = await terminal(audio.id, false);
  assert.equal(finished.artifacts.length, 1);
  const contentId = finished.artifacts[0].id;
  const metadata = await client.get(`/content/${contentId}`);
  assert.equal(metadata.media_type, 'audio/wav');
  assert.equal(metadata.sha256, audioHash);
  const audioResponse = await client.get(`/content/${contentId}/download`).asResponse();
  assert.equal(audioResponse.status, 200);
  assert.equal(sha(Buffer.from(await audioResponse.arrayBuffer())), audioHash);
  row('audio_metadata_and_binary_download', { extension: true, sha256: audioHash, bytes: audioBytes.length, media_type: 'audio/wav' });
  const audioRange = await client.get(`/content/${contentId}/download`, { headers: { Range: 'bytes=0-3' } }).asResponse();
  assert.equal(audioRange.status, 206);
  assert.equal(Buffer.from(await audioRange.arrayBuffer()).toString('ascii'), 'RIFF');
  const audioHead = await client.request({ method: 'head', path: `/content/${contentId}/download` }).asResponse();
  assert.equal(audioHead.status, 200);
  assert.equal((await audioHead.arrayBuffer()).byteLength, 0);
  assert.equal(Number(audioHead.headers.get('content-length')), audioBytes.length);
  const cancelled = await client.post(`/jobs/${audio.id}/cancel`);
  assert.equal(cancelled.status, 'succeeded');
  assert.equal(cancelled.id, audio.id);
  row('audio_range_head_and_terminal_cancel', { range_status: 206, head_status: 200, terminal_unchanged: true });
  return 3;
}
