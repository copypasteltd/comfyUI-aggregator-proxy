# 90 · 固定 SDK 客户端兼容矩阵

2026-10-25 开始，2026-10-26 完成本轮验证。关联完整待办 30、31 及 DEV-05/12/14/15/18/25/26/29/31/32。固定实际客户端版本，验证本项目已承诺的 OpenAI 图片协议子集和显式项目扩展；不把 SDK 的宽松响应构造当成字段类型正确的证据。

## 实现与验证约束

- Python SDK 固定 `openai==3.37.0`，Node SDK 固定 `openai@7.43.0`；分别使用项目锁文件及独立 Node 客户端目录。Python SDK 只在显式 sdk-compat 开发依赖组，Node SDK 不进入 Admin 或运行发布依赖。命令不得隐式安装、更新客户端或调用 OpenAI 公网生成服务。
- 复用独立原生演练的实际 Sub2API、控制 API、Windows Worker 和临时 PostgreSQL。ComfyUI、Redis 与对象存储仍为已标明的本机协议夹具；实际 GPU、真实 Workflow 质量、私有 S3 和生产缓存仍未验收。
- 调用 SDK 的图片生成、编辑、模型发现和错误处理，核对响应类型、HTTP 状态、请求关联、幂等重放/冲突及重试边界。Python 使用严格响应验证；合成产物必须验证实际字节摘要。失败不得用宽松解析或更改正式协议逃避。
- 示例关闭自动重试，显式传稳定 Idempotency-Key。需要验证 SDK 重试时，只向本次运行创建的本机资源注入受控故障，保留原请求与任务绑定；不能重新提交到别的机器或扩大 Worker 的生成重试预算。
- 标准 images.generate / images.edit 只接收最终图片响应；异步图片、六类 /v1/jobs 和内容接口是本项目扩展。视频保留既有历史协议边界；不因当前 SDK 暴露方法就宣称 OpenAI 托管视频可用。ComfyUI 音频继续统一异步任务，不承诺 /v1/audio/speech。
- SDK 请求只使用本次演练创建的合成 Key 和 loopback 服务。原始日志与临时凭证留在忽略目录；发布证据只保留固定事实、计数和摘要。客户端、连接、线程和本次进程必须清理，数据库/凭证/素材不得遗留；不调用浏览器、Docker、WSL 或其他虚拟化。

## 官方依据

已读取 [Python SDK](https://developers.openai.com/api/reference/python)、[图片生成方法](https://developers.openai.com/api/reference/python/resources/images/methods/generate) 和 [TypeScript SDK](https://developers.openai.com/api/reference/typescript)。SDK 支持自定义 API 地址，默认会重试部分网络与 HTTP 错误；本项目示例显式设置重试和时间预算。公开字段支持范围仍由 [03](./03-接口与Sub2API接入规范.md) 冻结，不能将 SDK 接受的全部参数等同于本项目能力。

## 运行入口

在已有原生开发环境的 app/ 下执行。以下依赖安装是显式操作；演练命令本身不安装软件。保留 release-build 组以便继续使用现有发布工具。Node SDK 在独立目录安装，禁用依赖安装脚本。

```powershell
.\.venv\Scripts\uv.exe sync --frozen --group release-build --group sdk-compat
$sdkNode = (Resolve-Path '.local/toolchains/node-24.22.1/node-v24.22.1-win-x64').Path
$env:Path = $sdkNode + ';' + $env:Path
Push-Location integrations/openai-clients
try {
    & (Join-Path $sdkNode node.exe) (Join-Path $sdkNode node_modules/npm/bin/npm-cli.js) ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'SDK dependency installation failed' }
} finally { Pop-Location }
$sdkEvidence = '../docs/evidence/sdk-client-' + [guid]::NewGuid().ToString('N')
$sdkGoEvidence = '../docs/evidence/sdk-go-' + [guid]::NewGuid().ToString('N')
$sdkBuildEvidence = '../docs/evidence/sdk-build-' + [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe -X utf8 scripts/test-sub2api-ledger.py --evidence-dir $sdkGoEvidence
if ($LASTEXITCODE -ne 0) { throw 'Provider component checks failed' }
.\.venv\Scripts\python.exe -X utf8 scripts/build-sub2api-provider.py --evidence-dir $sdkBuildEvidence
if ($LASTEXITCODE -ne 0) { throw 'Provider build failed' }
.\.venv\Scripts\python.exe -X utf8 scripts/test-sub2api-native.py --openai-sdk --provider-build-report "$sdkBuildEvidence/sub2api-provider-native-build.json" --evidence-dir $sdkEvidence
```

预检要求两个精确 SDK 版本及固定 Node 24.22.1；不满足时在创建数据库前失败。演练只创建自己命名的临时数据库，核对实际 loopback 身份并持有项目迁移锁；保留既有开发服务。报告目录必须位于 docs/evidence 且尚不存在，历史报告不覆盖。读取新目录中的 openai-sdk-native-protocol.json；只有 passed、来源前后摘要、执行结果及清理全部通过，才能引用为本机协议证据。

## 接入用法

业务客户端使用实际已发布模型别名、模型能力目录中允许的规格和自己的业务 Key。API 地址指向控制 API 或已接线的 Sub2API 对外 /v1；这里的 Key 不用于管理会话。Python 3.37.0 使用 httpx2，自定义 HTTP 客户端必须使用对应传输库。

```python
import os
from openai import OpenAI

with OpenAI(
    api_key=os.environ['GENERATION_API_KEY'],
    base_url=os.environ['GENERATION_BASE_URL'],
    max_retries=0,
    timeout=70,
) as client:
    result = client.images.generate(
        model=os.environ['GENERATION_MODEL'],
        prompt='A small green robot on a warm white background',
        n=1,
        size='1024x1024',
        quality='standard',
        response_format='b64_json',
        extra_headers={'Idempotency-Key': os.environ['GENERATION_REQUEST_ID']},
    )
    encoded_image = result.data[0].b64_json
```

GENERATION_REQUEST_ID 由调用方在首次提交前持久化，同一操作恢复时重用；不同内容必须换新值。请求超时不表示原任务取消，不能换 Key 盲目重提。同步入口最长等待 60 秒，客户端和代理应留传输余量；超时后按错误中的原任务 ID 查询，或在原幂等键上恢复。

Node 7.43.0 的对应写法如下；明确检查配置，避免缺少 baseURL 时回落到 SDK 默认公网地址。以下为服务端 JavaScript 客户端，不在 Admin 浏览器中保存业务 Key。

```javascript
import OpenAI from 'openai';
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}
const client = new OpenAI({
  apiKey: required('GENERATION_API_KEY'),
  baseURL: required('GENERATION_BASE_URL'),
  maxRetries: 0,
  timeout: 70000,
});
const result = await client.images.generate({
  model: required('GENERATION_MODEL'), prompt: 'A small green robot',
  n: 1, size: '1024x1024', quality: 'standard', response_format: 'b64_json',
}, { headers: { 'Idempotency-Key': required('GENERATION_REQUEST_ID') } });
const encodedImage = result.data[0].b64_json;
```

workflow_options 是显式项目扩展。JSON 生图可通过 Python extra_body 或 Node 请求正文传对象；multipart 编辑传同名表单字段的 JSON 字符串，避免 SDK 将嵌套对象展开成 workflow_options[seed] 等不在契约中的字段。多图编辑使用 SDK 的文件数组，图像角色和顺序仍由已发布 Workflow 支持范围决定。图片异步与音频任务使用明确的项目路由，不能将其结果交给 ImagesResponse 或语音响应解析器。

## 状态

首次实际 SDK 严格解析在模型集合失败：14 个上游普通模型各缺少 created、object、owned_by，共 42 项类型/必填校验错误。保留 [首次运行](./evidence/2026-10-25/openai-sdk-native-90-first/openai-sdk-native-protocol.json)，所属进程、临时库、观察线程、素材和凭证清理通过。此前六类协议生成已执行成功，不能替代 SDK 兼容通过。

修复契约：仅在合并的普通模型缺少字段时补充 object=model、created=0、owned_by=sub2api。0 明确表示上游未提供创建时间，不伪装为模型创建日期；sub2api 表示该目录条目的代理归属，不声称模型原作者。原有正确字段及扩展字段保留；已存在但无效的核心字段使普通目录校验失败并返回已有 catalog_unavailable，不能悄悄覆盖。Codex client_version 分支及未归属 Provider 的单模型透传仍保留原行为，未将其纳入本项目图片模型兼容承诺。标准模型集合的机器形状见 [SDK 模型列表契约](./contracts/sdk-model-list.schema.json)。

[第五次运行](./evidence/2026-10-25/openai-sdk-native-90-fifth/openai-sdk-native-protocol.json) 完整通过：24 项原生场景，其中 Python 11 项、Node 9 项实际 SDK 检查。生成、重放、错误及跨入口恢复最终只新增预期的 6 个任务、6 个 Provider intent 和 6 条 usage_logs；报告中的 new_unique_settlements 表示本次新增 usage_logs 数量，不替代客户账单对账。源码前后摘要一致，所属进程、临时数据库、观察线程、素材及凭证清理全部通过。完整待办 30 的真实资源出口及首版完整验收继续开放。

| 客户端场景 | Python 3.37.0 | Node 7.43.0 | 本机验证范围 |
| --- | --- | --- | --- |
| 模型集合与 Provider 单模型 | 通过，严格响应解析 | 通过，逐项核对核心字段 | 普通模型缺省元数据补齐，保留扩展与正确字段 |
| Base64 同步图片生成 | 通过 | 通过 | HTTP 200、整数 created、实际图片摘要 |
| 相同幂等键重放 | 通过 | 通过 | 相同请求恢复，未新增生成任务 |
| 同键不同正文、关闭重试 | 通过，1 次 HTTP 请求 | 通过，1 次 HTTP 请求 | 409 task_conflict，错误关联 ID 存在 |
| 同键冲突、启用 1 次重试 | 通过，2 次 HTTP 请求 | 通过，2 次 HTTP 请求 | SDK 自动重试保留原 Idempotency-Key |
| 不支持的 n=2 | 通过 | 通过 | 400 invalid_request；未静默接受 |
| 单图 multipart 编辑 | 通过 | 通过 | SDK 文件编码、JSON 字符串 workflow_options |
| 多参考图 multipart 编辑 | 通过 | 通过 | 文件数组、角色顺序、JSON 字符串扩展 |
| 图片异步扩展 | 通过 | 通过 | 通用 SDK 请求返回 202，恢复原同步任务 |
| URL 输出与路径下载 | 通过 | 本轮未覆盖 | 同一任务；内部 Provider 路径授权下载通过、匿名 401 |
| 异步 Python 客户端重放 | 通过 | 不适用 | AsyncOpenAI 严格解析、传输上下文释放 |

URL 场景只核对配置的 HTTPS 对外 URL，并通过本次所属内部 HTTP Provider 请求该下载路径；public_url_tls_frontend_verified=false，不能将其写成已验收对外 TLS 前置代理。Python 错误使用 request_id；Node 错误使用 requestID，成功 withResponse 使用 request_id。两个 SDK 均关闭默认重试，只有指定的冲突用例显式启用一次；每次请求至少间隔三秒，保持原限额。

[第二次运行](./evidence/2026-10-25/openai-sdk-native-90-second/openai-sdk-native-protocol.json) 已通过普通模型的严格响应解析，但演练驱动沿用了上一场景已撤销的合成 Key，无法发现 Provider 模型。修正为使用独立、仍有授权的第二个合成 Key，保留原撤销及跨 Key 拒绝验证；未恢复已撤销 Key 或放宽正式鉴权。所属临时资源再次全部清理。

[第三次运行](./evidence/2026-10-25/openai-sdk-native-90-third/openai-sdk-native-protocol.json) 在 Python 异步重放阶段触发 SDK RateLimitError。此前图片生成、单/多图编辑及冲突检查已运行，但整轮失败；原生 SDK 驱动改为请求间至少三秒，并在授权 URL 下载和异步重放前保留同样间隔。租户/项目/Key 的原 rpm=120、burst=10 和网关限制均未提高，生产重试及数据库时间预算均不变。第三轮所属资源全部清理。

[第四次运行](./evidence/2026-10-25/openai-sdk-native-90-fourth/openai-sdk-native-protocol.json) 完成 Python 全部检查后，在 Node 错误断言失败。实际 Node 7.43.0 的 APIError 使用 requestID，成功 withResponse 使用 request_id；驱动修正为对应属性，服务端 X-Request-ID 不变。需要修改来源时主动结束仍在 Python 阶段的旧 CI，核对本次 PID/启动时间/可执行文件后结束其所属进程，不能把该次当作完整回归结果。所属 SDK 演练临时资源全部清理。

## 版本化验证与候选

| 层级 | 本轮实际结果 | 证据 |
| --- | --- | --- |
| Python 专项 | 206 通过，零失败/错误/跳过；包含 13 个 SDK 目标边界检查 | [专项摘要](./evidence/2026-10-25/openai-sdk-focused-90.json) |
| Provider Go 组件 | 173 顶层/331 子测试通过；预期的 TestProcessBoundaryHelper 子进程入口单独 skip，按原结果保留 | [组件复验](./evidence/2026-10-25/openai-sdk-go-90/sub2api-ledger-tests.json) |
| 实际 SDK / 原生协议 | Python 11、Node 9、整轮原生 24 项；源码一致、临时资源清理通过 | [第五轮](./evidence/2026-10-25/openai-sdk-native-90-fifth/openai-sdk-native-protocol.json) |
| 精确候选构建 | rc-20261025.90.1，501 个源输入核对一致、169 个载荷文件、58 wheels、63 运行契约；SDK 开发依赖未进入运行包 | [构建摘要](./evidence/2026-10-25/openai-sdk-release-build-90.json) |
| 精确候选安装 | 28 项通过、四个应用角色退出码 0、journal 保留/重开；临时库、目录及凭证清理通过 | [安装](./evidence/2026-10-25/openai-sdk-release-install-90/native-release-installation.json) |
| 安装后浏览器 | 桌面/手机共 10 项通过；两个 context 与所属 browser 均关闭 | [浏览器](./evidence/2026-10-25/openai-sdk-release-install-90/native-release-installed-browser.json) |
| 完整原生 CI | 16 门禁/3696 Python 通过，零失败/错误/跳过；Python 门禁 1082.096 秒，原预算 1200 秒 | [最终 CI](./evidence/native-ci/ci90-20261025-sdk-clients-analyzed/summary.json) |

候选 ZIP SHA-256：caa6627952733f8cff32eab72ff01c0f980d65c155e7d279afd589b071ead785。无 DDL、公开路由或 UI 改动；保留 45 表/head 3da02e7b5941、190 设计端点、156 OpenAPI 路径/197 方法和第 87 的现有图库。60 schema/959 fixture 与 63 运行契约按最终文档门禁核对。新 SDK 演练不启动浏览器，安装浏览器由安装驱动 finally 释放；全程没有 Docker、WSL 或其他虚拟化。

此前 [中断记录](./evidence/2026-10-25/openai-sdk-ci90-interrupted.json) 保留 11 个已通过门禁，Python 未完成，既不是完整通过，也不能当作回归失败原因。

完整运行 ci90-20261025-sdk-clients-final 的 JUnit 为 3696 项通过，零失败/错误/跳过，测试套件耗时 1192.183 秒；原生门禁在 1200.013 秒触发原有 1200 秒截止时间。即使最终退出码为 0，该门禁仍失败、后四项未执行；不能写成完整 CI 通过。[预算失败摘要](./evidence/2026-10-25/openai-sdk-ci90-budget-timeout.json) 保留原报告、JUnit/日志摘要和 606 个源码输入的独立复核。相较 89 多出的 25 项为 13 个 SDK 目标边界检查和 12 个新契约 fixture。

只读检查发现专用测试库存在累积死元组和陈旧统计。按 [86 的受控维护边界](./86-数据库故障脱敏诊断与并发观测.md) 取得原 CI 文件锁和数据库 advisory 锁，核验 render_control_test / 127.0.0.1 / 55438 及 45 张允许表。一次 [普通维护](./evidence/2026-10-25/openai-sdk-test-database-maintenance-90.json) 完成 9 表后在 audit_events 触发原 30 秒语句预算/57014，立即停止；[只读保全复核](./evidence/2026-10-25/openai-sdk-test-database-maintenance-preservation-90.json) 确认 35 条可见合成记录、filenode 和触发器摘要均未变，未将失败改成维护完成。

随后显式进行 [仅刷新统计](./evidence/2026-10-25/openai-sdk-test-database-analyze-90.json)：45 表、1.604 秒通过，前后可见行数/filenode/触发器一致，估计死元组保持 134385。未改变数据、DDL、逐例清理、全局 PostgreSQL 配置或 CI/请求预算，也未加入自动 CI 维护。该次环境实验不证明历史慢查询或本次预算超时的完整根因。

独立复验 ci90-20261025-sdk-clients-analyzed 完成 16 项门禁，3696 项 Python 零失败/错误/跳过，Python 门禁 1082.096 秒。全部 606 项 CI、271 项 SDK 原生、501 项候选源输入与当前字节一致；组件、构建和 SDK 的 73 项 Go overlay 摘要一致，71 项维护源码和全部生成工作区逐项核对，Provider 二进制与候选清单一致。16 个门禁所属进程树关闭、私有日志完整；安装的四应用角色正常退出，两个 context 和 browser 关闭。汇总见 [90 最终验证](./evidence/2026-10-25/openai-sdk-verification-90.json)。该次通过不证明维护是完整根因或提高真实资源/首版验收；69 项保持开放。

## 后续多媒体矩阵

第 91 增量继续使用相同 SDK 和原生演练入口，扩展视频与 ComfyUI 音频，新增不可变视频规格迁移、严格项目响应校验及官方 Video 类型已知差异。见 [91](./91-多媒体SDK与视频冻结规格.md)。上文 20 项/24 场景属于第 90 运行的历史结果，不代表后续源码或完整客户端矩阵已验收。
