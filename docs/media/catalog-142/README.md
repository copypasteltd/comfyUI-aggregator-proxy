# 实际音乐调用 / Actual music generation

线上 YuE2 ABC 音乐工作流已完成独立实机验证、普通业务调用和完整文件下载。测试使用本项目编写的 ABC 曲谱与风格文本，实际产物为 **30 秒、48 kHz、双声道 FLAC**，音频非静音，RMS 为 -18.237 dBFS。此例只证明该型号的调用和文件交付，不代表其余待验证音频流程已通过。

The production YuE2 ABC workflow passed hardware validation, ordinary business generation and a complete verified download. The test uses an original synthetic ABC score and style prompt. The result is **30 seconds of 48 kHz stereo FLAC**, with non-silent audio at -18.237 dBFS RMS. This proves this model's generation and delivery; other pending audio workflows retain their own acceptance status.

公开版本仅移除内嵌工作流元数据，不重新编码音频；原始下载与公开版本的解码 PCM 摘要一致。两种文件分别记录 SHA-256。Cloud outputs expire after eight hours; this repository contains a separate public sample with metadata removed and identical decoded audio.

- [播放或下载音乐 / Play or download music](yue2-abc-music.flac)
- [实际请求、任务与下载校验 / Actual request, jobs and download verification](../../evidence/2026-11-04/catalog-audio-142-runtime.json)
- [公开文件摘要 / Public file hash](manifest.json)
- [模型目录与用法 / Model catalog and usage](../../134-新增模型目录与调用说明.md)
