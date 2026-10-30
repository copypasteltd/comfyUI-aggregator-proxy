"""Document the completed independent theme/example release from immutable evidence."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DOCS, FRONT, PRIVATE = ROOT / 'docs', ROOT / 'infinite-canvas', ROOT / '.local/studio151'
EVIDENCE = DOCS / 'research/studio-implementation'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
build, package, delivery = [records[key] for key in ['build', 'package', 'delivery']]
assert build['passed'] and build['unit_tests'] == 218 and package['passed'] and delivery['passed']
assert records['local_chromium']['passed'] and records['public_chromium']['passed'] and records['media']['passed']
browsers = [key for key in ['local_chromium', 'local_firefox', 'public_chromium'] if key in records]
assert all(all(records[key]['cleanup'].values()) and not records[key].get('resource_close_errors') for key in browsers)
accepted = [key for key in browsers if records[key]['passed']]
assert all(len(records[key]['submissions']) == 8 for key in accepted)
checks = sum(len(records[key]['checks']) for key in accepted)
screens = sum(len(records[key]['screenshots']) for key in accepted) + len(records['gallery']['screenshots'])
firefox = 'Firefox本机通过' if records.get('local_firefox', {}).get('passed') else 'Firefox本机尚未签收，失败记录单列'
firefox_en = 'Local Firefox passed' if records.get('local_firefox', {}).get('passed') else 'Local Firefox remains unaccepted; its failure is retained separately'
revision, source_hash = package['source_revision'], package['source_sha256']
public_screens = {row['name']: row['file'].removeprefix('docs/') for row in records['public_chromium']['screenshots']}
public_screens.update({row['name']: row['file'].removeprefix('docs/') for row in records['gallery']['screenshots']})

def put(file, text):
    file.write_text(text, encoding='utf-8', newline='\n')

def prepend(file, text):
    old = file.read_text(encoding='utf-8')
    assert '<!-- independent151:start -->' not in old
    first, rest = old.split('\n', 1)
    put(file, first + '\n\n<!-- independent151:start -->\n' + text.strip() + '\n<!-- independent151:end -->\n' + rest)

implemented = [
    ('I-173', '持久化明暗主题', '页首按钮、键盘操作、刷新恢复及未知偏好回到浅色'),
    ('I-174', '青柠组件和画布主题统一', '节点、网格、工具栏、表单、选择器活动选中态、抽屉、弹窗及GLB预览'),
    ('I-175', '严格CSP下的主题启动', '同源外置启动文件；实际页面检查主题和文本对比度'),
    ('I-176', '八类可编辑案例入口', '画布列表和主导航均可进入；包含两张明确标注的示例输入'),
    ('I-177', '按当前发布与权限匹配', '匹配任务类型、参考数量及合法完整规格；缺失时明确提示'),
    ('I-178', '保存案例并继续创作', '用户确认前不上传/提交；重开只采用标记的原输入，排除生成图'),
    ('I-179', '冻结构建与独立静态发布', '218单元、类型和生产构建；当前静态和保留资源逐项核对'),
    ('I-180', '双语说明和完整演示', '截图、完整MP4/GIF、失败证据、资源关闭及全部46项同步'),
]
table = '| 编号 | 本轮已实现 | 验证与边界 |\n| --- | --- | --- |\n' + '\n'.join('| ' + ' | '.join(row) + ' |' for row in implemented)
summary = f'''## 本轮实现 I-173～I-180 · Web静态153.1

[明暗主题与案例](36-明暗主题与预设创作案例.md)：继续二次开发原开源画布，提供持久化明暗切换、严格CSP启动和八类可编辑案例。218单元、类型与生产构建通过；完整通过的本机/公网页面检查共{checks}组、{screens}截图，{firefox}。所有案例生成检查使用独立合成API，每轮8次明确提交，不属于真实GPU生成；公网Firefox本轮未重新验收。仅发布独立静态153.1及精确复用151.2应用的153.0兼容恢复包。原API及全部服务/配置不改；既有真实视频准备失败和完整Web签收HOLD保留。

{table}'''
progress = DOCS / '10-开发进度与交付记录.md'
old = progress.read_text(encoding='utf-8').replace('## 本轮实现 I-165～I-172 · Web静态150.1', '## 历史实现 I-165～I-172 · Web静态150.1')
put(progress, old)
prepend(progress, summary)
rows = progress.read_text(encoding='utf-8').splitlines()
updates = {
    'F-01': ('153最终冻结原生同锁构建：218单元、类型与生产构建通过；复用原同锁安装', None),
    'F-02': (f'153冻结构建及{checks}完整主题/案例页面组通过；150表单/守卫/原生双源证据保留；{firefox}', None),
    'F-35': (f'153明暗主题、CSP启动、键盘切换、8案例和{screens}通过截图留证；原青柠参考不改', '完整弹层/焦点/对比度人工验收及更多页面组合继续'),
    'F-36': ('153案例页明暗桌面及390px检查；小屏导航适配', '更多网页尺寸与真实触控/键盘；390px为视口模拟'),
    'F-37': (f'153完整通过{checks}组主题/案例检查；{firefox}；公网Firefox本轮未重验', '实际Safari、Windows WebKit既有Cookie/解码、公网Firefox和更多后台操作'),
    'F-42': (f"153.1的{delivery['static_files_verified']}当前/{delivery['retained_previous_assets_verified']}旧资源和{len(delivery['routes'])}路由逐项核对", None),
    'F-44': ('历史146实际回退8项保留；153.0精确复用151.2并保留新旧资源，本轮未新增实际回退演练', '153实际回退、更多版本/并发/中断/损坏与已开页验证'),
    'F-45': (f'153的218单元、{checks}完整页面组、{screens}截图及完整MP4/GIF；本轮无真实生成请求', 'HOLD：既有真实视频准备失败、WebKit/Safari、公网Firefox及完整人工/配额/后台/故障验收'),
}
for index, line in enumerate(rows):
    if not line.startswith('| F-'): continue
    columns = [part.strip() for part in line.split('|')[1:-1]]
    if columns[0] in updates:
        current, remaining = updates[columns[0]]
        columns[2] = current
        if remaining: columns[3] = remaining
        rows[index] = '| ' + ' | '.join(columns) + ' |'
put(progress, '\n'.join(rows) + '\n')

links = '\n'.join(f'| {key} | [证据](research/studio-implementation/{name}) |' for key, name in names.items())
prepend(DOCS / '36-明暗主题与预设创作案例.md', f'''## canvas-20261031.153.1 · 已部署 / pending-test

源码 `{revision}`，摘要 `{source_hash}`。218单元、类型和生产构建通过。完整通过的主题/案例页面检查{checks}组、{screens}截图；{firefox}。各次完整流程均明确提交8项合成任务；公网测试明确转送自有合成API，不作为生产鉴权/CORS或GPU成功证明。源码未冻结的中间构建、修正链接键盘焦点期间主动中止的自有类型检查，以及早期测试脚本漏等渲染/漏点“载入产物”的失败回执均单列保留，未用于发布。修正最终样式前通过的案例流程仅作为基线记录；151.1为未部署候选包，最终153.1增加图片上标签的实色背景。截图结束有限CSS过渡后取证；完整录像保留整段实际操作，取截图时的过渡结束动作也保留，未剪裁重组。

中间151.2在公网暴露了业务路由与图片目录重名的问题：`/studio/examples`返回301，尾斜杠返回403。该失败回执保留，不计为公网签收。最终153.1使用`/studio/library`，额外检查直接打开、刷新和尾斜杠；保留旧图片路径，不修改任何服务配置。

Firefox的重载流程暴露了Ant Design选项同时处于selected与active时，后者覆盖背景但保留深色文字的问题；两次失败均保留。最终版本在青柠全局作用域修正重叠状态，并用键盘进入该状态后实际检查颜色与对比度。152.1准备曾被恢复来源守卫拒绝，未切换指针。最终153.0恢复包精确复用当前151.2，保留该旧版已知路由/样式限制；中间151.2的HTTP原件核对仅用作恢复来源证明，不表示其页面问题已修复。本轮没有执行实际回退。

静态包{package['archive_bytes']:,}字节，{delivery['static_files_verified']}当前文件、{delivery['retained_previous_assets_verified']}保留资源和{len(delivery['routes'])}路由逐项核对；153.0兼容恢复包精确复用151.2应用字节，本轮未执行实际回退。完整v5契约、原API工作区/配置/指针、全部服务PID保持一致，零服务重启。完整Web签收仍HOLD。

| 浅色案例页 | 深色案例页 |
| --- | --- |
| ![浅色案例页面]({public_screens['examples-light-desktop']}) | ![深色案例页面]({public_screens['examples-dark-desktop']}) |

| 浅色三维预览 | 深色三维预览 |
| --- | --- |
| ![浅色GLB预览]({public_screens['glb-preview-light']}) | ![深色GLB预览]({public_screens['glb-preview-dark']}) |

[390px浅色]({public_screens['examples-light-390']}) / [390px深色]({public_screens['examples-dark-390']})为网页视口模拟，不代表实体触控设备验收。

主题对比图另在同一公网静态版本等待授权目录和全部延迟加载封面完成解码后拍摄；该补充流程没有上传或生成请求。完整案例流程的原始截图仍保留。

[完整所选公网页面MP4](media/studio-151/themes-and-examples-public.mp4) / [GIF](media/studio-151/themes-and-examples-public.gif)。录像为实际页面操作和合成接口结果，示例输入不标作本轮GPU产物。所有自建浏览器与临时服务均已关闭。

| 内容 | 回执 |
| --- | --- |
{links}

[交付复查](research/studio-implementation/independent-review-151.json)。交付工具：[冻结构建](tools/verify_studio_clean_build.mjs)、[页面验证](tools/verify_studio_theme_examples.mjs)、[兼容恢复包](tools/package_studio_v5_recovery.py)、[静态发布](tools/studio_static_release.py)、[静态逐项核对](tools/verify_studio_media_release.py)、[完整录屏](tools/record_studio_theme_examples_media.py)、[文档整理](tools/finalize_studio_theme_examples_docs.py)、[复查](tools/review_studio_theme_examples_delivery.py)。''')

zh = f'''**Web静态153.1已部署**：[明暗主题与八类预设案例](docs/36-明暗主题与预设创作案例.md)。右上角切换浅色/深色并保存在当前浏览器，页面、弹层和画布统一使用青柠主题；同源启动脚本在刷新时恢复选择。“案例”入口创建可编辑的独立本机画布，按当前Key的任务类型、参考槽位和完整合法规格匹配；用户确认后才上传和生成。保存后可以继续创作，不自动把生成图加入参考。

218单元/类型/生产构建通过；完整通过{checks}组页面检查、{screens}截图，{firefox}。[完整MP4](docs/media/studio-151/themes-and-examples-public.mp4) / [GIF](docs/media/studio-151/themes-and-examples-public.gif)使用合成API，不代表真实GPU成功。本轮只更新独立画布静态内容；完整Web验收HOLD，原服务视频准备失败及公网Firefox等待验仍保留。

| 浅色 | 深色 |
| --- | --- |
| ![浅色案例页](docs/{public_screens['examples-light-desktop']}) | ![深色案例页](docs/{public_screens['examples-dark-desktop']}) |'''
en = f'''**Web static 153.1 is deployed:** [Light/dark themes and eight editable examples](docs/36-明暗主题与预设创作案例.md). The header toggle saves the browser's theme; pages, overlays, canvas nodes and GLB previews share the Lime palette. A same-origin startup script restores it on reload under the existing CSP. Examples create independent editable local canvases, match the current Key's published task type/reference slots/complete allowed preset, and upload or generate only after explicit confirmation. Reopened examples retain their original inputs without adding generated images automatically.

218 units, types and production build passed, with {checks} complete UI groups and {screens} screenshots. {firefox_en}. [Full MP4](docs/media/studio-151/themes-and-examples-public.mp4) / [GIF](docs/media/studio-151/themes-and-examples-public.gif) use a synthetic API and do not prove GPU generation. Only independent frontend static files changed. Full Web acceptance remains **HOLD**; the earlier real video preparation failure and public Firefox gap remain open.

| Light | Dark |
| --- | --- |
| ![Light examples page](docs/{public_screens['examples-light-desktop']}) | ![Dark examples page](docs/{public_screens['examples-dark-desktop']}) |'''
for language, block in [('zh', zh), ('en', en)]:
    file = DOCS / f'templates/independent-readme.{language}.md.in'
    old = file.read_text(encoding='utf-8').replace('**Web静态150.1已部署**', '**历史150.1交付**').replace('**Web static 150.1 is deployed:**', '**Historical 150.1 delivery:**')
    put(file, old); prepend(file, block)
prepend(DOCS / '21-独立画布部署与使用.md', '当前独立静态版本 `canvas-20261031.153.1`：[明暗切换与预设案例](36-明暗主题与预设创作案例.md)。[画布](http://43.165.167.179:39445/studio/)右上角切换主题；“案例”页打开可编辑模板，确认后才请求原API。218单元及本轮页面检查通过，完整Web签收HOLD。')
prepend(DOCS / '23-独立画布更新记录.md', summary.replace('## 本轮实现', '## 153.1'))
prepend(DOCS / 'README.md', '最新：[36 · 明暗主题与预设创作案例](36-明暗主题与预设创作案例.md)。明暗偏好、严格CSP启动、八类编辑案例、完整演示与本轮证据；全部待办仍在[文档10](10-开发进度与交付记录.md)。')
for name in ['todo.mdx', 'todo.zh-CN.mdx', 'pending-test.mdx', 'pending-test.zh-CN.mdx']:
    file = FRONT / 'docs/content/docs/progress' / name
    old = file.read_text(encoding='utf-8'); assert '<!-- independent151:start -->' not in old
    block = f'\n<!-- independent151:start -->\n### 153.1 · 已实现，等待完整Web人工签收\n\n明暗偏好、CSP启动、八类可编辑案例及原输入继续创作已实现；218单元、{checks}完整页面组、{screens}截图通过。{firefox}。所有生成测试使用合成API，本轮无真实GPU请求；原API未修改。完整F-01～F-46及真实视频、公网Firefox、WebKit/Safari、后台/配额/故障待验保留。详见[本轮说明](../../../studio/36-明暗主题与预设创作案例.md)。\n<!-- independent151:end -->\n'
    if old.startswith('---\n'):
        split = old.index('\n---', 4) + 4; old = old[:split] + '\n' + block + old[split:]
    else: old = block + old
    put(file, old)
print(json.dumps({'passed': True, 'complete_ui_groups': checks, 'screenshots': screens, 'source_revision': revision, 'api_written': False, 'full_acceptance': 'HOLD/pending-test'}))
