# 原生工作台：实际请求与调用逻辑

[English](README.en.md) · [完整方案与约束](../../146-原生ComfyUI面板访问与独占操作.md) · [实际页面与录屏](../../media/origin-146/README.md)

通常直接访问 [原生工作台](http://43.165.167.179:39443/origin/)，用 Admin 已单独授权设备的 API Key 登录，在设备目录申请独占，再使用原始 ComfyUI 编辑器。面板自动处理会话、提交幂等和实时连接。标准业务调度继续使用 `/v1/models` 与异步业务 API。

| 顺序 | 请求及用途 |
| --- | --- |
| 1 | `POST /origin/api/session`，JSON 为 `{ "api_key": "YOUR_API_KEY" }`。保存服务返回的 HttpOnly Cookie 与 `csrf_token`，后续使用同一 Cookie 容器；Key 不放 URL。 |
| 2 | `GET /origin/api/devices` 获取 `data`，选择实际返回的 `slug`。无授权、离线、维护或已有占用的设备不能强行进入。 |
| 3 | `POST /origin/api/devices/{slug}/acquire`，带 `X-Origin-CSRF`，取得持久独占及 `url`。 |
| 4 | `POST /origin/{slug}/prompt`，带 Cookie、`X-Origin-CSRF` 及唯一 `Idempotency-Key`，提交 ComfyUI **API 格式**图。相同提交意图重用同一标识；未知回执不能换标识盲目重提。 |
| 5 | 通过 `/origin/{slug}/ws?clientId=YOUR_CLIENT_ID` 看实时事件，或 `/origin/{slug}/history/{prompt_id}` 查询自己的任务；输出描述对应 `/origin/{slug}/view` 下载，同样需要会话。 |
| 6 | 完成后 `POST /origin/api/devices/{slug}/release`。`closing` 表示尚待核对，只有确认全部提交结束及源队列空闲才为 `closed`。最后使用 CSRF 调用 `DELETE /origin/api/session`。 |

写请求使用自己的 CSRF 值。服务还会校验原 Key 状态、独立设备授权和占用；不能把别人的 `clientId`、`prompt_id`、文件名或设备代号当作授权。127 MiB 上传、遮罩原图归属、个人工作流/设置及 HEAD/Range 的规则见完整方案。自动化必须保留未完成提交和释放状态，退出登录不会终止任务。

[workflow-request.json](workflow-request.json) 来自本轮实际浏览器提交：`EmptyImage` 生成 64×64 青柠色图，再由 `SaveImage` 保存。保留原生 UI 工作流元数据，**仅将浏览器 client_id 替换为 `example-native-client`**；因此不能用该脱敏编号重新连接历史实时会话。服务还会将真实 clientId 按逻辑 Key 改写后发往源站。

[actual-browser-response.json](actual-browser-response.json) 是上述实际提交的回执，`prompt_id` 为该次真实任务。它不是新调用模板；重放样例会生成新任务。该次结果已在[编辑器截图](../../media/origin-146/origin-native-5070.png)与录屏中显示。独立 HTTP 检查生成的 [diagnostic-64x64.png](../../media/origin-146/diagnostic-64x64.png) 用于文件摘要和 Range 验证，属于另一任务。

本诊断不需要模型权重，不能代表模型画质或多模态全验收。原生任务不属于 `/v1/jobs`，不会自动上传云端，也不自动执行八小时删除。设备中已安装的自定义节点拥有主机执行权限，原生入口仅应授予可信操作者。
