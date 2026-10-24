# 标准化参考输入与 Workflow 选项

状态：契约与组件实现已验证。适用于 WP-04/05/11；不代替五类真实 Workflow 和 10～20 台设备验收。

## 1. 对外输入及规范化

2026-10-23 核对 [OpenAI 视频创建接口](https://developers.openai.com/api/reference/resources/videos/methods/create) 与 [视频参考图说明](https://developers.openai.com/api/docs/guides/video-generation)。`input_reference` 的 JSON 形式为对象，multipart 形式为图片文件；该图片表达视频首帧引导。这里借用请求形式，本系统仍运行管理员发布的 ComfyUI Workflow，不调用 Sora 服务。

| 入口 | 输入 | 规范任务 |
| --- | --- | --- |
| 图片生成 | 原有字段，可附 `workflow_options` | `text_to_image` |
| 图片编辑 | 原有素材对象、JSON `images` 或 multipart `image`/`image[]` | 单图 `image_to_image`，2～4 图 `references_to_image` |
| 视频创建 | 无参考输入 | `text_to_video` |
| 视频创建 | JSON `input_reference` 或一个同名 multipart 文件 | `references_to_video`，唯一参考角色为 `first_frame` |
| 视频创建 | `workflow_options.references` 或原有顶层 `references` | `references_to_video`，2～4 个显式角色的参考对象 |

`workflow_options` 是本项目扩展。JSON 中必须是对象；multipart 中为严格 JSON 对象字符串。允许项：`seed`、`negative_prompt`；视频额外允许 `fps`、`references`；单图编辑额外允许 `strength`；多图编辑额外允许 `image_roles`。其他字段、显式 null、错误类型、重复 JSON 键、同一来源的混用均拒绝，不静默忽略。`negative_prompt` 写入规范 input，其余标量写入 parameters；仍按 JobCreate 的取值范围与已发布 Workflow 的 supported_parameters 校验。

`image_roles` 保留文档 35 的 `{index, role}` 形式，覆盖且唯一对应每张图片；`references` 每项为 `{asset_id, role, weight?}`。`input_reference` 支持 `{asset_id}` 的项目形式，以及 `{image_url}` 的标准形式；`file_id` 没有映射，不把任意上游文件 ID 当成本地素材。URL 仅接受 data URL 或同一配置 public_origin 的本人 `/v1/assets/{id}/content`，不抓取外部 URL。参考对象、数组顺序和权重保留到规范请求。

单参考首帧 JSON 示例（以下模型名和素材 ID 均为示意，须换成当前 Key 获授权的已发布模型及本人素材）：

```json
{
  "model": "example-first-frame-video-v1",
  "prompt": "合成示例：首帧场景中的云缓慢移动",
  "seconds": "4",
  "input_reference": {"asset_id": "asset_11111111111111111111111111111111"},
  "workflow_options": {"seed": 17, "fps": 24, "negative_prompt": "blur"}
}
```

多参考示例的模型须绑定两个槽位，并在指定权重时绑定相应权重输入：

```json
{
  "model": "example-multiple-reference-video-v1",
  "prompt": "合成示例：人物在指定场景中缓慢行走",
  "workflow_options": {
    "references": [
      {"asset_id": "asset_11111111111111111111111111111111", "role": "character", "weight": 1},
      {"asset_id": "asset_22222222222222222222222222222222", "role": "scene", "weight": 0.5}
    ]
  }
}
```

## 2. 单参考视频与五类能力

保留五种 task_type。`references_to_image` 仍要求 2～4 图；`references_to_video` 新允许 1～4 图，但单图仅允许 `first_frame`。这项首帧引导能力不作为原有多参考视频要求的替代验收。

Workflow 的参考槽位以冻结 bindings 中连续的 `reference_0`～`reference_3` 决定；单参考视频发布包至少绑定 `reference_0`，多参考图至少绑定前两个槽位。准入时参考数量必须恰好等于发布包已绑定的槽位数，禁止复制首帧填补多参考槽位、丢弃多余参考图或使用发布包的默认私有图片。执行编译再次检查所有参考输入实际被消费。已有双图及更多槽位的发布包行为保持其原有要求。

## 3. 副作用及资源边界

Provider 复用文档 35 的受控上传暂存、固定目标、主体归属、持久上传意图及生成预留路径。视频 JSON/data URL 与 multipart 都受有界上传容量、128 MiB 总体上限、20 MiB 单图上限和固定读取/总时限约束。先解析完整结构、选择受支持模型并检查当前权限，再上传；已有素材先核对原目标，不能在渠道之间搬运。标准字段与扩展字段规范化后进入同一幂等比较，重复提交不重复预留；变化的参数/参考顺序/权重产生冲突。临时文件在所有成功、错误、取消路径关闭并删除。

控制面兼容入口同步接受上述 Workflow 标量与视频素材形式；使用业务 Key 的当前素材归属及同一 JobCreate/准入服务，不接受网关身份自报。控制面 body 限制与 multipart 资源释放覆盖视频输入。素材持久上传前，通过只读 preflight 检查已发布模型、授权、Workflow 选项及参考槽位；正式准入在事务中复核，不把预检当作预留。控制面生成幂等键沿用 16～128 位可见 ASCII 规则。取消请求必须等待仍在使用素材的上传线程退出后再关闭文件和释放容量。数据库结构没有新增字段，不安排空迁移。

## 4. 验证与交付边界

[Go 组件报告](./evidence/2026-10-23/sub2api-ledger-tests-before-effects.json) 为 **131 项顶层、246 个子用例通过**，41.78 秒，其中编译 11.15 秒；另 1 项 helper 跳过。比上一增量新增 6 项顶层、16 个子用例。实际使用固定上游认证/仓储与原生 PostgreSQL、HTTPS 协议替身；304 项迁移、109 张测试表重放通过，备份可读、随机库已删除。已有 247～253 迁移和两处原版组合根/认证补丁保持原摘要。

[原生服务器编译](./evidence/2026-10-23/sub2api-provider-native-build-before-effects.json) 通过，16.28 秒；没有启动完整 Sub2API。[应用报告](./evidence/2026-10-23/sub2api-reference-inputs-application-checks.json) 为 **293 项 Python 测试通过**，168.87 秒；锁文件、Ruff、mypy（84 项源文件）、OpenAPI 导出、TypeScript 生成、vue-tsc 和 Vite 均通过。Vite 为 7.38 秒。新增 6 项 HTTP、2 项 Workflow 编译、5 项严格 JSON/取消清理和 3 项机器契约用例；视频 JSON/multipart/原生重放、角色/权重、归属/目标、容量/暂存、错误输入无预留均覆盖。107 个 OpenAPI 路径、137 个方法路径注册，130 项设计端点全部匹配。

元数据客户端没有改动，复用 [41 项顶层、179 个子用例的报告](./evidence/2026-10-23/sub2api-controlclient-tests-before-effects.json)，再次逐文件核对其当前源码摘要；未把它记为本轮新执行的客户端测试。OpenAPI 对可选参考输入与 Workflow 选项的声明已去除 null，与拒绝显式 null 的运行行为一致；修正后执行了上述最终应用检查。

保留 [首轮 Go 记录](./evidence/2026-10-23/reference-inputs-go-first-run.json)、[图片样本失败记录](./evidence/2026-10-23/reference-inputs-python-first-run.json) 和 [秒数字符串失败记录](./evidence/2026-10-23/reference-inputs-python-normalization-first-run.json)。Go 新增正例遗漏了既有计费断言需要的 6 秒参数，修正 fixture 后通过。旧合成 PNG 的 IDAT 校验和错误，已更换有效合成图，并将真实损坏 PNG 的 SyntaxError 转为受控 invalid_image；增加 HTTP 回归。秒数字符串最初转换为 Decimal，与 Schema 的浮点 multipleOf 不兼容；改为先校验十进制精度再转换为可序列化的数值。没有放宽图片、计费或输入校验。

[最终源码与运行核对](./evidence/2026-10-23/sub2api-reference-inputs-closeout.json) 覆盖 57 项维护/生成文件和 198 项应用/契约/产物摘要。开发库仍为 bc284d51e790、40 张业务表，未安排空迁移；最近的备份恢复证据保留在文档 37。只重启登记 API，保留六项 Runner/GC 身份，九项开发进程均匹配；测试/恢复库、本次测试进程和上传暂存为零。项目证书验证 live/ready/Admin 为 200，匿名集合/发现接口为 401，运行中 OpenAPI 与导出一致。文档静态校验及其文件摘要记录在同一最终核对报告中。

真实 GPU、五类导出 Workflow、设备画像、Sub2API 完整运行与私有对象存储联调仍待资源和验收；组件测试不能将这些状态改为已完成。
