"""Write independent bilingual release documentation from completed composer receipts."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DOCS, PRIVATE, FRONT = ROOT / 'docs', ROOT / '.local/studio150', ROOT / 'infinite-canvas'
EVIDENCE = DOCS / 'research/studio-implementation'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
package, delivery, build = records['package'], records['delivery'], records['build']
assert package['passed'] and delivery['passed'] and build['unit_tests'] == 207
keys = ['local_chromium','local_firefox','public_chromium']
assert all(records[key]['passed'] and len(records[key]['checks']) == 23 for key in keys)
assert not records['public_firefox']['passed'] and not records['failed_public_firefox_retry']['passed']
assert not records['live']['passed'] and records['live_terminal']['error_code'] == 'preparation_transport_failure'
assert records['live_key_cleanup']['passed'] and records['media']['passed']
groups = sum(len(records[key]['checks']) for key in keys)
screens = sum(len(records[key]['screenshots']) for key in keys)
revision = package['source_revision']

def put(file, text): file.write_text(text, encoding='utf-8', newline='\n')

def insert(file, block):
    text = file.read_text(encoding='utf-8')
    marked = '<!-- independent150:start -->\n' + block.strip() + '\n<!-- independent150:end -->'
    if '<!-- independent150:start -->' in text:
        text = re.sub(r'<!-- independent150:start -->.*?<!-- independent150:end -->', lambda _: marked, text, count=1, flags=re.S)
    else:
        first = text.index('\n'); text = text[:first] + '\n\n' + marked + '\n' + text[first:]
    put(file, text)

def link(key, title): return f'[{title}](research/studio-implementation/{names[key]})'

newdoc = DOCS / '35-画布参考创作与生成表单.md'
text = newdoc.read_text(encoding='utf-8')
text = text.replace('本轮待完成的发布检查与最终页面证据将在交付前补齐。', '')
put(newdoc, text)
evidence = '\n'.join(f'| {title} | {link(key, "回执")} |' for key, title in [('build','207单元、类型和生产构建'),('dual_origin','11组原生双源协议'),('local_chromium','Chromium本机23组'),('local_firefox','Firefox本机23组'),('public_chromium','Chromium公网23组'),('public_firefox','Firefox公网未签收：首次17组后停滞'),('failed_public_firefox_retry','Firefox公网重跑未完成，0组'),('package','150.1静态包'),('recovery','精确复用149.1应用的150.0恢复包'),('delivery','全部新旧静态资源及20路由'),('media','完整所选公网页面MP4/GIF'),('process_cleanup','自有浏览器进程及资源关闭')])
insert(newdoc, f'''## canvas-20261031.150.1 · 已部署 / pending-test

最终源码 `{revision}`，源码摘要 `{package['source_sha256']}`。207项单元、类型和生产构建、11组原生双源协议通过。完整表单矩阵为 Chromium本机23组/公网23组、Firefox本机23组，共{groups}组、{screens}张通过截图。每次完整矩阵向独立合成API提交12项，覆盖11个已发布示例，产物按MIME/长度/SHA核对；不是GPU生成。公网矩阵明确通过测试转送到自有合成API，不作为真实CORS或鉴权证明；原生双源检查没有业务请求拦截。

Firefox公网首次完成17组后，上传后的下一次合成任务响应未观察到；只读状态观察也未返回。重建独立会话后在初始本机数据库准备阶段未完成，日志另出现SWGL帧缓冲映射失败。只关闭精确核对的自建无头浏览器，保留两次失败；**Firefox公网未签收**，不把17项部分进度并入69组通过或认定故障原因。当前按已验证的Chromium公网流程交付。

真实双参考视频仅一次POST，已受理但最终 `preparation_transport_failure`，该能力本轮未签收。真实调用绑定前一次冻结构建，后续仅补目录恢复修复，没有再发GPU请求；临时Key已停用并确认401。

静态包{package['archive_bytes']:,}字节，SHA `{package['archive_sha256']}`，{delivery['static_files_verified']}当前文件/{delivery['retained_previous_assets_verified']}旧资源和20路由逐项核对。兼容恢复150.0精确复用149.1应用并保留新旧资源；本轮未新增实际回退演练，未重跑未改动的ZIP/清理协议。完整v5存储契约、原API工作区/配置/指针及全部服务PID不变，零服务重启。完整Web签收 **HOLD**。

![实际参考创作页面](media/studio-150/live-2026-10-30T18-25-27-662Z/real-two-reference-form.png)

上图使用实际测试输入；对应视频在原服务准备阶段失败，没有把失败截图标作生成成功。[完整公网页面MP4](media/studio-150/canvas-reference-creation-public.mp4) / [GIF](media/studio-150/canvas-reference-creation-public.gif) 展示合成接口下的完整表单、预检、重试与结果回填，原始所选页全程保留。

| 验证内容 | 证据 |
| --- | --- |
{evidence}

[最终交付复查](research/studio-implementation/independent-review-150.json)。早期测试服务根路径、旧按钮定位/保存时序、Firefox目录晚到失败，以及由此修复的产品初始化问题均保留。跨浏览器完整生成、更多规格/蒙版/长离线/配额/资源峰值及F-01～F-46仍独立验收。''')

rows = '\n'.join(re.findall(r'^\| I-1(?:6[5-9]|7[0-2]) \|.*$', text, re.M))
assert len(rows.splitlines()) == 8
insert(DOCS / '10-开发进度与交付记录.md', f'''## 本轮实现 I-165～I-172 · Web静态150.1

[文档35](35-画布参考创作与生成表单.md)：继续改造既有开源画布，补齐选图到参考槽位、缩略图、合法规格、目录晚到初始化、全输入预检、上传恢复和实际来源连线。207单元/类型/构建，Chromium本机/公网与Firefox本机共{groups}组、{screens}截图及完整MP4/GIF通过；Firefox公网两次未完成，另列待验。一次真实双参考视频已受理但准备阶段失败 `preparation_transport_failure`，不计GPU成功；临时Key已禁用。仅独立静态150.1发布，原API及服务不改，完整Web签收HOLD。

| 编号 | 本轮已实现 | 验证与边界 |
| --- | --- | --- |
{rows}''')

updates = {
'F-04': '更多权限、额度及界面禁用组合待验',
'F-05': '持续发布、权限更新和目录刷新组合待验',
'F-06': '更多已发布预设待验；任意尺寸不作为已支持能力',
'F-08': '蒙版、更多已发布规格与浏览器恢复待验',
'F-09': '更多参考数量、角色、Workflow与浏览器恢复待验',
'F-12': '恢复原服务准备链路后再验真实视频；更多规格、参考数量及恢复组合待验',
'F-20': '素材到期、长离线及更多媒体恢复待验',
'F-35': '完整弹层、键盘、焦点及对比度仍待人工验收',
'F-44': '150实际回退、更多版本、并发/中断/损坏及已开页矩阵；不任意回退',
'F-45': 'HOLD：真实视频准备失败、WebKit/Safari、完整Web人工/配额/后台/故障；容量为外部依赖',
}
current_updates = {
'F-01': '150最终同锁原生隔离构建：207单元、类型与生产构建通过，未重复npm ci',
'F-02': f'150冻结构建、12发布守卫、11原生双源及Chromium本机/公网与Firefox本机{groups}组通过；Firefox公网未签收',
'F-04': '源站、Key和连接检测保留；150跳过无权首项规格，无可用规格模型禁用创建',
'F-05': '动态Schema/模型用法保留；150目录晚到初始化一次，不覆盖已初始化草稿',
'F-06': 'Schema/preset/slots与Key限制保留；150的11已发布示例表单/规格/结果回填通过',
'F-08': '历史真实编辑保留；150画布源图绑定、实际预览及全部原件预检通过',
'F-09': '历史真实双参考PNG保留；150自动填图、顺序/角色、换图和真实来源连线通过',
'F-12': '历史真实MP4保留；150双参考换序表单通过；本轮真实5070任务failed/preparation_transport_failure，未生成结果',
'F-14': '历史真实GLB/动画及148跨浏览器ZIP保留；150按Schema仅提交source_image并回填GLB卡通过',
'F-16': '历史双页首次发送竞争、真实未知响应恢复原ID保留；150一次真实任务正常受理，未替代重提',
'F-18': '证据148的三引擎混合ZIP/刷新保留；150表单恢复补充，不计为新的长离线生成验收',
'F-20': '历史真实上传恢复保留；150全部原件先预检、明确重试复用原Idem/asset及有序绑定通过',
'F-21': '历史14原生组/36查询错误组合与6冻结UI保留；150观察同一真实任务至准备失败，未改轮询/频控',
'F-24': '历史真实鉴权下载与149的78次原件下载保留；150合成结果按长度/MIME/SHA核对，不冒充真实GPU产物',
'F-35': f'青柠原始CSS/字体不变；150参考创作抽屉、真实输入页面及{screens}矩阵截图留证',
'F-37': f'150的Chromium本机/公网与Firefox本机{groups}组通过；Firefox公网两次未完成，未重新验WebKit',
'F-42': f'150.1的{delivery["static_files_verified"]}当前/{delivery["retained_previous_assets_verified"]}旧资源和20路由通过',
'F-44': '历史146实际兼容回退8项保留；150.0精确复用149.1并保留新旧资源，150未新增实际回退演练',
'F-45': f'150的{groups}表单组/{screens}截图、完整MP4/GIF及真实失败/Key停用留证；双语和全部46项同步',
}
file = DOCS / '10-开发进度与交付记录.md'
lines = file.read_text(encoding='utf-8').splitlines()
for i, line in enumerate(lines):
    match = re.match(r'^\| (F-\d+) \|', line)
    if match and (match[1] in updates or match[1] in current_updates):
        cells = line.split('|')
        if match[1] in updates: cells[4] = ' ' + updates[match[1]] + ' '
        if match[1] in current_updates: cells[3] = ' ' + current_updates[match[1]] + ' '
        lines[i] = '|'.join(cells)
put(file, '\n'.join(lines) + '\n')

insert(DOCS / '21-独立画布部署与使用.md', f'''**当前150.1已部署**：[参考创作与生成表单](35-画布参考创作与生成表单.md)。目录 `/opt/infinite-canvas/releases/canvas-20261031.150.1/`，编译源码 `{revision}`；包{package['archive_bytes']:,}字节，SHA `{package['archive_sha256']}`。{delivery['static_files_verified']}当前/{delivery['retained_previous_assets_verified']}旧资源与20路由通过，兼容150.0准备完成，未新增实际回退演练。207单元及{groups}组表单通过；一次真实视频准备传输失败，临时Key已停用。原API/配置/指针和所有服务PID不变，Firefox公网未签收，完整Web验收HOLD。下方149及更早记录均保留原时点。''')
insert(DOCS / '23-独立画布更新记录.md', f'''## canvas-20261031.150.1 · 画布参考创作 / pending-test

- [新增] 画布选图自动填参考槽位，统一选择与实际缩略图，结果连接实际使用的来源。
- [修复] 当前Key合法规格选择、无规格模型禁用和目录晚到初始化；刷新不覆盖已初始化草稿。
- [调整] 整份Schema和全部原件先预检，再上传；明确重试复用原上传身份，角色和数组顺序保持发布契约。
- [验证] 207单元/类型/构建、11双源及Chromium本机/公网与Firefox本机{groups}组，{screens}截图与完整所选页MP4/GIF；Firefox公网未完成。合成接口结果不替代GPU。
- [交付] 150.1静态与兼容150.0，全部资源/路由核对；原API/配置及全部服务PID不变。一次真实视频返回 `preparation_transport_failure`，未生成结果，测试Key已禁用；完整Web验收HOLD。

Canvas150.1 binds selected originals to published reference slots, shows previews, chooses an allowed complete preset, initializes a draft once after catalog restoration, validates all inputs before the first upload, and links actual sources. {groups} groups passed on local/public Chromium and local Firefox; public Firefox remains unaccepted after two incomplete runs. The one real video request was accepted but failed during original-service preparation; GPU output is not accepted. Only independent static files were promoted; compatible recovery is prepared, original API/services unchanged. Full Web acceptance remains HOLD.''')

for language in ['zh','en']:
    file = DOCS / f'templates/independent-readme.{language}.md.in'
    if language == 'zh':
        block = f'''**Web静态150.1已部署**：[画布参考创作与生成表单](docs/35-画布参考创作与生成表单.md)。直接改造既有开源画布：选图自动填槽位、缩略图、合法完整规格、目录恢复、全输入预检及实际来源连线。207单元/类型/构建、Chromium本机/公网与Firefox本机共{groups}组表单、{screens}截图通过；Firefox公网未完成，单列待验；[完整所选页MP4](docs/media/studio-150/canvas-reference-creation-public.mp4) / [GIF](docs/media/studio-150/canvas-reference-creation-public.gif)展示合成接口流程。一次真实双参考视频受理后由原服务返回 `preparation_transport_failure`，没有生成结果；测试Key已禁用。仅发布画布静态文件及兼容150.0恢复包，原API/服务/配置不改。完整Web验收仍 **HOLD**。

![实际双参考创作页面；本次视频准备失败](docs/media/studio-150/live-2026-10-30T18-25-27-662Z/real-two-reference-form.png)

操作：连接原API和业务Key → 在画布选择图片 → 新建创作 → 核对模型、完整规格和每张参考图 → 提交并查询原任务 → 成功后载入/另存原件。视频、音频和3D使用各自已发布Schema；不新建生成后端。下方149及更早证据保留原时点。'''
    else:
        block = f'''**Web static 150.1 is deployed:** [Canvas reference creation](docs/35-画布参考创作与生成表单.md). The existing open-source canvas now populates published slots from selected images, shows original previews, chooses an allowed complete preset, initializes once after catalog restoration, validates all inputs before upload and links actual sources. 207 units/type/build and {groups} synthetic UI groups passed on local/public Chromium and local Firefox, with {screens} screenshots. Public Firefox remains unaccepted after two incomplete runs. [Complete selected page MP4](docs/media/studio-150/canvas-reference-creation-public.mp4) / [GIF](docs/media/studio-150/canvas-reference-creation-public.gif). The one real two-reference video was accepted but ended with `preparation_transport_failure`; no GPU output was produced, and the temporary Key is disabled. Only independent static files changed, compatible 150.0 recovery is prepared, original API/services/configuration unchanged. Full Web acceptance remains **HOLD**.

![Actual reference form; this video failed during preparation](docs/media/studio-150/live-2026-10-30T18-25-27-662Z/real-two-reference-form.png)

Use: connect the original API and business Key → select canvas images → create → check the model, complete preset and each reference → submit and follow the original job → load/save the successful original. Video, Workflow audio and 3D follow their published schemas; no generation backend is added. The 149 and earlier evidence below is historical.'''
    insert(file, block)
    old = file.read_text(encoding='utf-8').replace('**当前Web静态149.1已部署**','**历史149.1交付**').replace('**Web static 149.1 is deployed:**','**Historical Web149.1 delivery:**').replace('独立画布149.1','独立画布150.1').replace('Independent canvas149.1','Independent canvas150.1')
    put(file, old)

for suffix in ['.mdx','.zh-CN.mdx']:
    for kind in ['todo','pending-test']:
        file = FRONT / f'docs/content/docs/progress/{kind}{suffix}'
        text = file.read_text(encoding='utf-8')
        block = f'''{{/* independent150:start */}}

Web150.1: selected canvas originals, reference previews, allowed presets, deferred catalog initialization, all-input preflight, stable upload retry and actual-source links are implemented for manual acceptance.207 units/type/build and {groups} synthetic UI groups passed on local/public Chromium and local Firefox; public Firefox remains unaccepted after two incomplete runs. One real video ended in preparation_transport_failure; it is not GPU success, and the temporary Key is disabled. Independent document35, screenshots, complete MP4/GIF and all F-01–F-46 are updated. Compatible150.0 is prepared; original API/services remain unchanged. Full Web acceptance, Windows WebKit/Safari, more specifications, quota/background/faults and actual video recovery remain HOLD.

{{/* independent150:end */}}'''
        if '{/* independent150:start */}' in text: text = re.sub(r'\{/\* independent150:start \*/\}.*?\{/\* independent150:end \*/\}', lambda _: block, text, count=1, flags=re.S)
        else:
            heading = re.search(r'^# .+$', text, re.M); assert heading; text = text[:heading.end()] + '\n\n' + block + text[heading.end():]
        put(file, text)

file = DOCS / 'README.md'; insert(file, '[当前150.1 · 画布参考创作与生成表单](35-画布参考创作与生成表单.md)：选图、预览、规格、预检、重试和来源连线；合成矩阵通过，真实视频准备失败单独记录。原API不改，完整Web签收HOLD。')
file = DOCS / '21-独立画布部署与使用.md'; put(file, file.read_text(encoding='utf-8').replace('**当前149.1覆盖更新**','**历史149.1交付记录**'))
print(json.dumps({'passed': True, 'release': 'canvas-20261031.150.1', 'groups': groups, 'screenshots': screens, 'api_written': False}))
