# 132 · 双 GPU 工作流现状与 API 封装候选

核查日期：2026-11-04，Asia/Taipei。读取两台原生 Windows 的现场工作流、当前 ComfyUI 节点/模型选项、模型目录文件、既有验证记录，以及云端实际发布目录和正在运行的接口约束。此次仅盘点，不提交生成任务、不下载模型、不更改渠道或发布模型。

## 1. 数量与判断口径

| 编号 | 项目 | RTX 3080 | RTX 4090 |
| --- | --- | --- | --- |
| S01 | 现场服务 | 在线，采样时无运行或排队任务 | 在线，采样时无运行或排队任务 |
| S02 | 设备报告显存 | 10,240 MiB | 49,140 MiB，以设备实际报告为准 |
| S03 | 工作流目录 JSON | 100 份 | 299 份 |
| S04 | 其中真实工作流 | 100 份，含 UI/API 配对及重复预设 | 297 份；另 2 份为依赖/恢复报告 |
| S05 | 当前注册节点类型 | 1,692 | 3,809 |
| S06 | 模型目录文件 | 259 个，含配置及配套文件 | 263 个，含配置及配套文件 |
| S07 | 本轮静态检查通过的文件 | 100 份 | 230 份 |
| S08 | 已发布业务模型别名 | 5 个 | 3 个 |

两台合计 397 份工作流文件、330 份通过本轮静态检查；这些数字包含重复文件，不是独立模型数量，也不是已经上线的 API 数量。云端另有 5070 的 3 个发布别名，本轮不盘点该设备。已发布合计仍为 11 个。

静态检查针对最终输出可达链路：节点类型、模型/枚举选项、普通必填输入、动态输入展开及连接来源/输出槽位。当前节点的 `COMFY_AUTOGROW_V3` 使用展开字段，不能把不存在的父字段误判为缺输入。已有 API 导出只有在对应现场 UI 文件 SHA-256 相同时才复用；80 份新增 4090 画布由当前前端只读转换。3080 的 11 份新 3D 画布另有当前配套 API 文件，逐个检查配套文件。

检查不加载权重，不重新计算大模型摘要，不证明所有子模型完整、动态下载依赖可用、输出质量或显存峰值合格。既有成功记录单独标明，不能替代当前冻结版本经平台提交、回传、完整下载和隔离验证。

候选状态：**已上线**可按业务 Key 授权调用；**优先封装**表示现有任务类型可承载、仍需冻结/绑定/输出校验/实测批准；**扩展后封装**表示还缺公开输入或任务语义；**先修复**表示现有文件存在明确阻塞。

## 2. 现在已经有 API 的 8 个模型

| 编号 | 设备 | 别名 | 调用能力 |
| --- | --- | --- | --- |
| A01 | 3080 | `comfy-3080-image-lustify-krea2` | 文生图 |
| A02 | 3080 | `comfy-3080-image-edit-wai` | 单图重绘 |
| A03 | 3080 | `comfy-3080-image-refs-wai` | 两图参考生图 |
| A04 | 3080 | `comfy-3080-music-yue2` | 文本风格控制音乐，歌词来自冻结图 |
| A05 | 3080 | `comfy-3080-3d-trellis2` | 单图生成静态、自包含 GLB |
| A06 | 4090 | `comfy-4090-video-h3` | 文生视频 |
| A07 | 4090 | `comfy-4090-video-h3-i2v` | 单首帧生视频 |
| A08 | 4090 | `comfy-4090-video-h3-refs` | 两张参考图生视频 |

这些别名已发布；某个调用者实际可见和可用的范围，仍取决于其业务 Key、父级额度、模型/渠道授权和运行状态。使用 `GET /v1/models` 及模型 usage 查询，不能用设备目录替代公开发现。

## 3. 3080：图像与音频候选

| 编号 | 当前工作流/系列 | 输入与用途 | 封装判断及剩余事项 |
| --- | --- | --- | --- |
| B01 | `12_wai_illustrious_v17_exact` | 提示词 → SDXL 图片 | 优先封装；WAI 文生图独立于已上线的参考/重绘模型 |
| B02 | `10_noct_q_v4_qwen_image21` | 提示词 → NoctQ/Qwen 图像 | 优先封装；当前节点/模型选项通过，需测定 10 GiB 设备的冻结规格 |
| B03 | `30_NoctQ_单图文字编辑` | 一张图及编辑说明 → 图片 | 优先封装；绑定上传素材和正负提示 |
| B04 | `31_NoctQ_多图文字参考` | 多图及说明 → 图片 | 优先封装；冻结参考数量、顺序及各图角色 |
| B05 | `34_LUSTIFY_单图文字风格参考` | 单图及文字 → 风格参考生成 | 优先封装；不是保持所有原像素不变的局部编辑保证 |
| B06 | `35_LUSTIFY_多图文字风格参考` | 多图及文字 → 图片 | 优先封装；与已上线文生图另建冻结版本 |
| B07 | `qwen21_v10`、`qwenImage21EditflowLora_v10`、4 份 Anatomy 作者示例 | Qwen 图像编辑/LoRA 预设 | 可筛选封装；原件和 Safe-Validation 副本共 12 份，不应发布为 12 个重复模型 |
| B08 | `01_yue2_full_bf16_abc_music` | ABC 曲谱 → 音乐 | 候选；当前图有完整链路，需定义曲谱语义，不能将 prompt 风格说明冒充自由曲谱/歌词入口 |
| B09 | `20_soulx_official_voice_tts` | 文本及固定参考音色 → 语音 | 优先封装；固定音色由 Admin 管理，明确文本解析、时长及输出规格 |
| B10 | `21_soulx_authorized_voice_clone` | 文本及用户提供的参考音频 → 语音 | 扩展后封装；当前业务输入没有参考音频上传/绑定 |
| B11 | `MOSS-TTSD-v1.0` 播客系列 | 结构化双人对话及参考声音 → 音频 | 可先封装固定音色；自由声音输入需扩展，脚本/角色需要明确绑定 |
| B12 | `SoulX-Podcast-1.7B` 播客系列 | 双人脚本及参考声音 → 音频 | 同上；输出只选择一个正式音频，避免把 WAV/MP3 同时当成一个主产物 |
| B13 | `FireRedTTS2` 播客系列 | 双人脚本及参考声音 → 音频 | 同上；加载、配额、文本解析与回传分别验证 |

播客目录分为 Trio 6、Duo 12、Scenes5 30 个 JSON，共 48 个文件，包含 API/UI 配对；对应 3 个引擎的场景模板，不是 48 个基础模型。读取到 Duo 6 条、Scenes5 15 条既有完整解码通过记录，输出约 40.16～53.68 秒；本轮未重新生成或听音验收。

## 4. 3080：11 套 3D 流程

这些流程在 `2026-10-3D-All-3080` 中各有 UI/API 配对文件。现场还保留 TRELLIS2、Pixal3D、TripoSG、PartCrafter 的较早画布副本。本轮读取到 11 个 case 的既有成功生成及网格检查记录；这些是设备级记录，只有 A05 已经过平台发布。

| 编号 | 流程 | 当前输入与输出 | 封装判断及剩余事项 |
| --- | --- | --- | --- |
| D01 | TRELLIS2 | 单图 → 网格；完整画布还有 PBR 烘焙分支 | 基础版已上线 A05；PBR 分支需单独冻结、验证纹理和最终 GLB，不能沿用 A05 的颜色/纹理承诺 |
| D02 | Pixal3D | 单图 → 3D/纹理分支 | 优先封装；选择一个最终 GLB，检查配套图片及纹理是否内嵌 |
| D03 | Hunyuan3D21 | 单图 → 几何 GLB | 优先封装；当前图为几何链，不能据模型目录推断已提供完整 PBR 流程 |
| D04 | TripoSG | 单图 → 3D | 优先封装；输出与内存峰值需通过平台真实验证 |
| D05 | PartCrafter | 单图 → 多部件网格 | 优先封装；一个 GLB 内可有多个 mesh，分件数量和质量另验 |
| D06 | SAM3D | 单图 → 原始带色 GLB | 优先封装；当前使用 `SaveRawGLB3080`，需核对历史产物字段和平台受控文件提取 |
| D07 | Pixal3D-MV | 四视图 → 3D/纹理分支 | 扩展后封装；现有 `image_to_3d` 只允许单张 source_image |
| D08 | MV-SAM3D | 四视图 → GLB，节点另可导出 PLY | 扩展后封装；新增多视图语义和自定义导出适配；PLY 不属于现有 GLB 支持范围 |
| D09 | UniForge3D | 四视图 → 几何 GLB | 扩展后封装；多视角顺序、尺寸一致性、内存和输出需校验 |
| D10 | UltraShape1 | 图片及已有粗网格 → 精化 GLB | 扩展后封装；当前引用本机既有 GLB 路径，应改为受控网格输入或平台内部串联任务 |
| D11 | UltraTex | 已有网格及图片 → PBR GLB | 扩展后封装；当前图加载已有 3D 文件，需明确网格来源、输入授权及材质交付 |

多视角、网格精化和贴图不应伪装成已支持的单图建模。既有成功记录也不表示结果已满足拓扑、打印、骨骼、动画或游戏资产质量验收。另读到 SUPRA-Tex 的历史原生运行阻塞记录；未发现它作为完整的可用主工作流进入当前列表，不列为可发布项。

## 5. 4090：基础 H3 和扩展候选

| 编号 | 当前工作流/系列 | 用途 | 封装判断及剩余事项 |
| --- | --- | --- | --- |
| C01 | `01_larry_turbo_v4` | Turbo v4 八步音视频生成 | 优先封装为速度预设或独立别名；测定质量/尺寸/时长 |
| C02 | `02_t8_native_audio` | 原生声音与画面联合生成 | 优先封装；选择 MP4 主产物，声音存在及参数含义需说明 |
| C03 | `04_easy_t2va` | 简化文本生成入口 | 可封装；与 A06 去重，避免单纯换 UI 就新增模型 |
| C04 | `09b_clipproj_4b` | 4B 编码器与 ClipProj 投影生成 | 优先封装；固定相匹配的编码器、投影及底模 |
| C05 | `09c_veda_t2va_windows`、`90_veda_after_headers_retry` | Veda 原生 Windows 稀疏注意力生成 | 优先筛选成功版；两份作为实现版本，不重复计数 |
| C06 | PinkCherry v1.5、Hybrid v1.8、NaughtyTimes full/pruned | 不同 H3 底模/LoRA 配置 | 文本/图片输入模式可优先封装；每个版本需自己的冻结依赖及运行批准 |
| C07 | Hearmeman 13 种 LoRA 配置 | 同一 H3 链的模型预设 | 作为 Admin 可选配置或少量明确别名；内容策略和 Key 范围需明示，不因为存在权重就自动发布 |
| C08 | All-Modes 的 t2v、i2v、ref_image、multi_image | 文本、单图或两图输入 | 可沿现有图片素材接口封装；冻结图角色和数量，不承诺任意参考方式等价 |
| C09 | All-Modes 的 ref_video、multi_ref | 视频参考，或视频与两张图混合参考 | 扩展后封装；当前生成输入没有 source_video 绑定 |
| C10 | `05_motion_context_continuation` | 沿用上下文继续拍摄 | 扩展后封装；需要父任务/context 资源、所有权、保留及恢复逻辑；不能使用服务器固定 latent 文件代替用户上下文 |
| C11 | `06_viggle_performance_transfer` | 源视频动作迁移到目标人物 | 扩展后封装；视频加图片的输入和分段/回传策略 |
| C12 | `07_akatz_character_swap_v15` | 视频人物替换 | 扩展后封装；源视频、人物参考及声音策略 |
| C13 | 新增人物替换 v1.5 标准运行版、人物与场景替换实验 | 视频加参考图进行人物/场景替换 | 使用运行副本；实验版和实测版分开，需视频输入及自定义视频产物适配 |
| C14 | `09a_t8_lock_source_audio` | 沿用源视频声音条件生成画面 | 扩展后封装；读取源视频/音频的受控输入及声音语义 |

All-Modes 有 17 组配置 × 6 种模式 = 102 个文件。其中 13 组为 Hearmeman，另 4 组为 PinkCherry v1.5、Hybrid v1.8、NaughtyTimes full/pruned。中文说明画布、项目镜头与服装场景副本还会进一步重复；不能将 297 份文件全部登记成 297 个公共模型。

## 6. 4090：社区原生出片候选逐项

建议从 `社区精选H3_原参数原生出片_20261103` 的运行副本选择，保留作者原件。下表“既有结果”取现场说明记录，不是本轮重新生成的结果；当前 22 份运行副本中有 18 份通过静态检查。`08a～08h` 曾省略可分离超分/补帧，必须以实际尺寸描述，不能按文件名标成已具备 2K/4K 输出。

| 编号 | 流程 | 既有结果：尺寸 / 时长 | 判断 |
| --- | --- | --- | --- |
| H01 | 01a 虚空折叠·标准二采 | 1504×832 / 7.292 秒 | 候选；两张图，选定二采主视频并适配 VHS 输出 |
| H02 | 01b 虚空折叠·戏曲版 | 1504×832 / 7.292 秒 | 候选；图片输入及声音来源需明确 |
| H03 | 01c 虚空折叠·唐僧版 | 1504×832 / 7.292 秒 | 扩展后封装；一张图及参考音频输入 |
| H04 | 02a 服装展示·DaSiWa | 验证阻塞 | 跳过；缺指定 `Upscale_Mod_20260911` LoRA |
| H05 | 02b 服装展示·Singularity | 验证阻塞 | 跳过；缺指定 `_0909` 版本底模，不能静默换相近模型 |
| H06 | 02c 服装展示·EasyMedia 循环长视频 | 896×1184 / 14.792 秒 | 候选；确定循环段控制和一个最终视频 |
| H07 | 03b WORK-FISHER 本地版 | 1472×832 / 5.167 秒 | 候选；一张图，需选择 H.264 主输出及 VHS 提取 |
| H08 | 08a Sigma 强化双采·2K 名称版 | 1920×1088 / 5.167 秒 | 候选；按实际尺寸发布 |
| H09 | 08b SelfLift·2K 名称版 | 1376×768 / 5.167 秒 | 候选；当前为原生输出，不是 2K |
| H10 | 08c OpenVDN·2K 名称版 | 1344×768 / 5.167 秒 | 候选；当前为原生输出，不是 2K |
| H11 | 08d Sigma 强化双采·4K 名称版 | 1920×1088 / 5.167 秒 | 候选；当前不是 4K |
| H12 | 08e SelfLift·4K 名称版 | 1376×768 / 5.167 秒 | 候选；当前不是 4K |
| H13 | 08f OpenVDN·4K 名称版 | 1344×768 / 5.167 秒 | 候选；当前不是 4K |
| H14 | 08g SelfLift·768 | 1376×768 / 5.167 秒 | 优先候选；一张图，已有完整解码记录 |
| H15 | 08h OpenVDN·768 | 1344×768 / 5.167 秒 | 优先候选；当前两张图分别连接 first_frame/last_frame，可封装首尾帧控制 |
| H16 | 08i SeedVR2 与 DLSS 后期超分 | 验证阻塞 | 跳过；存在对当前 SaveVideo 不存在的输出槽位引用，还涉及外部增强节点 |
| H17 | 10a Ai 随风高动态武戏 | 验证阻塞 | 跳过；前端导出中存在未解析 class_type 的节点 |
| H18 | 11a 青橙 Lab·SelfLift 参考生成 | 1376×768 / 15.083 秒 | 优先候选；两图输入及受控角色绑定 |
| H19 | 12a Hybrid·合理动态 4+5 双采 | 1376×768 / 8.000 秒 | 优先候选；两图输入，锁定双采参数 |
| H20 | 12b DaSiWa·强动态 4+5 双采 | 1376×768 / 10.125 秒 | 优先候选；与缺权重的 H04 是不同流程 |
| H21 | 13a EasyMedia 上下文循环长视频 | 1376×768 / 34.157 秒 | 候选；需要循环参数、内部 LLM/上下文控制及最终段合成策略 |
| H22 | YZ 金鱼·终极篇文武双全 | 1664×928 / 12.250 秒 | 扩展后封装；当前图含 6 个图片、3 个音频及 1 个视频加载节点，超出现有输入能力 |

图像加载数量来自最终产物可达执行图，不是“每张图都由用户必填”的承诺；其中可能包含固定辅助图、遮罩或预处理。正式封装需要明确上传槽位和保留的固定资源。

## 7. 明确先跳过或迁移的文件

| 编号 | 范围 | 发现的问题 | 处理 |
| --- | --- | --- | --- |
| X01 | H04/H05 及对应作者原件 | 指定 LoRA/底模选项不存在 | 跳过，不替换作者模型凑完整 |
| X02 | H16 | 当前输出槽位失效 | 修复节点与产物链后再测 |
| X03 | H17 | 存在未解析节点 | 补齐受控实现或重新设计后再评估 |
| X04 | Director 原图及 46 份项目镜头副本 | `cache_frames_codec=false` 不属于当前枚举 | 迁移旧字段；项目镜头按同一模型的请求参数管理，不批量公开为模型 |
| X05 | Alissonerdx v1.1 及中文副本 | 缺当前 LatentUpscaler 的 `enable_chunking` 输入 | 先迁移图；再处理视频上传和输出适配 |
| X06 | 部分作者原件、人物替换原件 | 旧控件序列导致 `device=fp16`、precision 为布尔值等错位 | 用独立适配副本迁移，保留原件及参数审计 |
| X07 | SUPRA-Tex 历史尝试 | 记录为原生运行阻塞 | 不作为当前可发布工作流 |

## 8. 当前平台还需补的能力

已读取实际云端 `linux-20261030.137.1` 的安装源码；此次没有替换运行版本。当前保留七类任务类型。Workflow 参数绑定仍为 prompt、负提示、seed、图像尺寸/时长/fps/strength、source_image/mask、最多四个图片参考槽和 output_prefix；3D 只允许单图来源与受控质量预设。

| 编号 | 封装缺口 | 受影响候选 |
| --- | --- | --- |
| P01 | 参考音频/源视频/输入网格的上传、校验、鉴权、存储、清理及 Worker 绑定 | 声音克隆、动作迁移、人物替换、视频参考、网格精化/贴图 |
| P02 | 多视角 3D 任务语义和图序约束 | Pixal3D-MV、MV-SAM3D、UniForge3D |
| P03 | 首尾帧角色、结构化音频脚本和曲谱的明确映射 | 两帧控制、多人播客、ABC 音乐 |
| P04 | 父任务/context/latent 资源及链式任务恢复 | 上下文续拍、长视频、从上一个建模结果精化 |
| P05 | VHS、自定义 GLB 节点和多输出图的受控主产物选择 | 社区视频、SAM3D/MV-SAM3D、含预览/纹理/音频的多输出画布 |
| P06 | 每个冻结版本的依赖锁、规格、显存/执行预算、真实业务调用和下载验证 | 全部新增候选 |

建议首批增量：WAI 文生图、NoctQ 单/多图编辑、LUSTIFY 单/多图参考；Pixal3D、Hunyuan3D21、TripoSG、PartCrafter、SAM3D；4090 Larry Turbo、ClipProj/Veda、SelfLift-768、OpenVDN-768、青橙 Lab、Hybrid/DaSiWa 双采；SoulX 固定音色先于自由声音输入。一次只发布有明确用途、当前依赖和平台实测证明的版本。

## 9. 证据与收尾

[脱敏盘点证据](evidence/2026-11-04/workflow-api-inventory-132.json) 记录数量、来源摘要、静态检查边界、云端别名、既有成功记录摘要及本次浏览器清理。工作流原件、示例音视频、完整历史和登录会话不提交公共仓库；逐文件清单保留于本地文档私有目录。

两台服务保留。只读前端转换在 finally 中关闭本次 page/context/browser，没有操作用户原有标签页。未使用 Docker、WSL 或虚拟化；未安装依赖、重启进程、加载模型或新建业务 Key。文档与证据门禁不提升任何候选为已通过真实平台运行验收。

## English reading guide

This is a read-only inventory dated 2026-11-04. The two machines contain 100 and 297 actual workflow files; the 4090 directory also contains two JSON reports. These counts include UI/API pairs, translated canvases and scene presets. Eight business model aliases are already published across these machines. The 330 static passes are file-level dependency/graph checks, not 330 published or newly executed models.

Tables A list existing API aliases. B and D cover image, audio and 3D candidates on the 3080. C and H cover H3 variants and community workflows on the 4090. X identifies blocked originals, missing exact weights, stale widget mappings and invalid links. P identifies platform work required before accepting video/audio/mesh inputs, multi-view 3D, structured scripts or parent-context resources. Historical successful output records are explicitly separated from this inspection. Native-output dimensions take precedence over misleading 2K/4K filenames. No new generation, channel change, model publication or runtime deployment was performed.
