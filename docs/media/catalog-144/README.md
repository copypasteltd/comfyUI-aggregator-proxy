# 实际语音调用 / Actual speech generation

以下样本来自线上平台的独立业务调用，各型号此前已完成实机验证和发布。输入使用本项目的合成参考声音、对应转写和中性的蓝色机器人台词。`seconds=30` 为最长时长，语音可以自然提前结束；这些样本证明生成与文件交付，不签收语义或音色质量。

These samples come from separate production business calls after hardware validation and publication. Inputs use synthetic reference voices, matching transcripts and neutral blue-robot dialogue. `seconds=30` is a maximum; speech can end naturally earlier. These records verify generation and file delivery, with no linguistic or voice-quality acceptance claim.

| 模型 / Model | Alias | 实际音频 / Actual audio | 播放 / Play |
| --- | --- | --- | --- |
| FireRedTTS2 单人语音 | `comfy-3080-03-fireredtts2-347a5d` | 4.80 秒，24 kHz，单声道 MP3 | [MP3](fireredtts2-dialogue.mp3) |
| MOSS-TTSD v1.0 单人语音 | `comfy-3080-01-moss-ttsd-v1-0-72f653` | 3.52 秒，24 kHz，单声道 MP3 | [MP3](moss-ttsd-dialogue.mp3) |

公开副本仅复制音频流并移除内嵌工作流元数据，原始下载与公开文件的解码 PCM 摘要完全相同。原始文件摘要和公开副本摘要分别记录。线上文件在任务成功后保留八小时；仓库中的公开样本独立保存。

The public MP3s copy the encoded audio stream and remove embedded workflow metadata. Their decoded PCM hashes match the original downloads exactly. Original and public file hashes are recorded separately. Cloud outputs expire eight hours after success; repository samples are retained independently.

- [真实请求、任务、下载与音频核对 / Actual requests, jobs, downloads and audio checks](../../evidence/2026-11-04/catalog-speech-144-runtime.json)
- [公开样本摘要 / Public sample hashes](manifest.json)
- [全部型号与当前调用说明 / Catalog and usage](../../134-新增模型目录与调用说明.md)
- [语音槽位和台词规则 / Speech slots and dialogue rules](../../137-模型家族与三维导出能力说明.md)
