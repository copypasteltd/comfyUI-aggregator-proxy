# 实际音乐调用 / Actual music generation

线上 YuE2 音乐工作流已完成独立实机验证、普通业务调用和完整文件下载。此样本只传入风格文本，连接到输出的 `YuE2GenerateABC` 在工作流内部生成曲谱；没有传入调用方编写的 ABC。实际产物为 **30 秒、48 kHz、双声道 FLAC**，音频非静音，RMS 为 -18.237 dBFS。此例只证明该型号的调用和文件交付。

The production YuE2 workflow passed hardware validation, ordinary business generation and a complete verified download. This sample supplied a style prompt only; its connected `YuE2GenerateABC` generated the score inside the workflow. The result is **30 seconds of 48 kHz stereo FLAC**, with non-silent audio at -18.237 dBFS RMS. This proves this model's generation and delivery.

此前“使用原创 ABC 输入”的说明有误。[明确更正记录](../../evidence/2026-11-04/music-input-correction-144.json) 绑定原请求、任务、下载摘要和实际冻结链路；原始证据、音频字节及历史 `manifest.json` 保留，[修正后的分类清单](manifest-corrected-144.json) 替代旧清单的 `kind` 分类。另一条支持调用方 ABC 输入的型号为 `comfy-3080-00-yue2-full-bf16-text2music-77ea35`，两者用法见[音频调用说明](../../138-音频模型调用与验证.md)。

The earlier caller-supplied-original-ABC attribution was incorrect. The linked correction preserves the original evidence and media hashes. The corrected manifest supersedes only the historical manifest's input classification. The separate `…77ea35` model accepts a supplied ABC score.

公开版本仅移除内嵌工作流元数据，不重新编码音频；原始下载与公开版本的解码 PCM 摘要一致。两种文件分别记录 SHA-256。Cloud outputs expire after eight hours; this repository contains a separate public sample with metadata removed and identical decoded audio.

- [播放或下载音乐 / Play or download music](yue2-abc-music.flac)
- [实际请求、任务与下载校验 / Actual request, jobs and download verification](../../evidence/2026-11-04/catalog-audio-142-runtime.json)
- [公开文件摘要 / Public file hash](manifest.json)
- [模型目录与用法 / Model catalog and usage](../../134-新增模型目录与调用说明.md)
