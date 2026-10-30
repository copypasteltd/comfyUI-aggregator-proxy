"""Update only independent Web documentation from the completed 149 receipts."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS, PRIVATE, FRONT = ROOT / 'docs', ROOT / '.local/studio149', ROOT / 'infinite-canvas'
EVIDENCE = DOCS / 'research/studio-implementation'
names = json.loads((PRIVATE / 'receipts.json').read_text(encoding='utf-8'))
records = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in names.items()}
assert all(record['passed'] for record in records.values())
assert records['media_local']['functional_groups_passed'] == records['media_public']['functional_groups_passed'] == 36
assert records['media']['owned_browsers_closed_before_encoding'] and len(records['media']['clips']) == 4
package, delivery = records['package'], records['delivery']
revision = package['source_revision']
assert revision == json.loads((PRIVATE / 'code-revision.json').read_text(encoding='utf-8'))['code_revision']


def put(file, text): file.write_text(text, encoding='utf-8', newline='\n')


def insert(file, block):
    text = file.read_text(encoding='utf-8')
    marked = '<!-- independent149:start -->\n' + block.strip() + '\n<!-- independent149:end -->'
    if '<!-- independent149:start -->' in text:
        text = re.sub(r'<!-- independent149:start -->.*?<!-- independent149:end -->', lambda _: marked, text, count=1, flags=re.S)
    else:
        first = text.index('\n')
        text = text[:first] + '\n\n' + marked + '\n' + text[first:]
    put(file, text)


def link(key, title): return f'[{title}](research/studio-implementation/{names[key]})'


public_chromium, public_webkit = records['public_chromium'], records['public_webkit']
shot = public_chromium['screenshots'][0]['file'].removeprefix('docs/')
webkit_shot = public_webkit['screenshots'][0]['file'].removeprefix('docs/')
doc = f'''# 34 · Web媒体原件下载与播放失败处理

当前版本 **canvas-20261031.149.1**，入口 [Studio](http://43.165.167.179:39445/studio/)。只交付Web前端，原API仍137.1；不增加转发、队列、原生客户端或ComfyUI连接。完整Web验收 **HOLD/pending-test**。

## 用户操作

1. 在图层列表定位目标图片、视频或音频，确认操作栏属于当前节点，再选择下载。批次图片可展开后分别下载。
2. 原件保留本机保存的字节，后缀优先按Blob的MIME选择：JPEG为`.jpg`、WebP为`.webp`、WebM为`.webm`、MP4为`.mp4`，WAV/FLAC同理。未知类型保留`.bin`，不伪装成MP4；MIME映射不是内容嗅探或解码器。
3. 浏览器原生解码失败时，节点显示“当前浏览器无法播放此素材”和“下载原件”。可以另存后使用支持该格式的播放器。提示仅说明本机播放问题，不改变已成功任务，也不提交新生成请求。
4. 替换音频/视频后按新来源重新判断播放；旧来源的错误不会覆盖新素材。Windows WebKit仍不能解码本轮四个有效音视频样片，替换后的原件仍可保存、下载。
5. 有本机存储键却缺少原件时明确拒绝下载；不会从残留Blob URL凑出“成功”副本。重新载入原件或从完整ZIP恢复后再操作。API结果仍核对原连接身份，不绕过Key分区。

云端八小时到期、本机保留及安全清理规则不变。普通原件下载不重新编码，也不主动清除源文件；下载不是ZIP备份、云同步或服务端任务取消。

## 实现及边界

| 编号 | 实现 | 约束 |
| --- | --- | --- |
| M-01 | 普通节点与主/备选批次图片共用原件下载逻辑 | 读取受控本机/既有旧库/API结果存储，优先真实Blob MIME；已知键缺件则拒绝 |
| M-02 | 原生video/audio错误触发可见提示和下载按钮 | 不注入虚假播放事件，不改生成状态，不添加计时器/重试/新限制 |
| M-03 | 错误按当前来源识别，实际替换文件后重新载入 | 支持的浏览器恢复解码；不支持的浏览器继续提示，原件保留 |
| M-04 | 图层定位同步选择、操作栏及对应面板 | 不再残留上一节点操作栏；下载按钮阻止鼠标拖动入口，不额外打开编辑面板 |
| M-05 | 保留04·青柠原始CSS/字体和令牌 | 提示卡复用现有节点色彩，原始样式文件不改 |

编译源码 `{revision}`，源摘要 `{package['source_sha256']}`。只发布独立静态目录；原API工作区、协议、Worker、调度、数据库、Admin、配置、发布指针及服务PID均未改变。

## 验证

| 编号 | 结果 | 实际范围 |
| --- | --- | --- |
| V-01 | {link('build', '203单元/类型/生产构建')} | 相同依赖锁的原生隔离安装回执复用；本轮重跑源码检查，未再次npm ci |
| V-02 | {link('media_local', '本机三引擎36组')} / {link('media_public', '公网三引擎36组')} | 每引擎12组/13次实际下载；合计78次下载逐份长度/SHA/后缀一致，18张通过截图 |
| V-03 | {link('fixtures', '六份有效/两份故意无效样片')} | 16×12 JPEG/WebP、1秒WAV/FLAC/MP4/WebM；两份85字节无效内容用于真实原生错误，不是GPU结果 |
| V-04 | {link('protocol', '18原生协议')} / {link('dual_origin', '11双HTTP源')} / {link('release_guard', '12发布守卫')} | 数据保护/完整契约、Bearer/noCookie、Schema Worker；鉴权专项仅Chromium，不外推Windows WebKit |
| V-05 | {link('archive_local', '14本机ZIP')} / {link('archive_public', '14公网ZIP')} / {link('cleanup_local', '10本机清理')} / {link('cleanup_public', '10公网清理')} | 实际文件输入、下载、刷新/引用/占用保护；合成故障不是实际配额耗尽 |
| V-06 | {link('delivery', '全部静态字节与路由')} | 当前{delivery['static_files_verified']}文件、保留{delivery['retained_previous_assets_verified']}资源、{len(delivery['routes'])}路由；发布只读检查原API健康/CORS |
| V-07 | {link('prepared', '兼容恢复包准备')} / {link('promoted', '静态指针切换')} | 149.0精确复用146.1应用字节并保留新旧资源；完整v5契约相同。149未重新演练实际回退，历史146演练保留 |
| V-08 | {link('media', '两MP4/两GIF')} / {link('process_cleanup', '浏览器关闭')} | 两个完整所选公网页面录像，全部文件完整解码；自有page/context/browser/HTTP夹具已关闭 |
| V-09 | {link('api_boundary_before', 'API源码前')} / {link('api_boundary_after', 'API源码后')} | 两个原API仓库只读基线保持一致，未创建业务Key或GPU任务 |

旧版146.1实际下载把WebM命名为MP4的[失败回执](research/studio-implementation/media-actions-baseline-chromium-2026-10-30T16-26-31-823Z.json)保留。[首轮TS类型检查失败](research/studio-implementation/clean-build-2026-10-30T16-03-32-219Z.json)、测试静态根路径/节点选择/冷页面观察、旧操作栏问题及早期Firefox下载等待超时也保留，不算通过；中间构建不冒充最终构建。Windows WebKit首次390px检查在布局完成前观察到930px，后续使用同一严格宽度条件等待并复测通过；这是测试观察修正，不是放宽产品断言。

Windows WebKit的四份有效音视频仍不可解码，Cookie隔离仍沿用证据147的未签收结论；本轮仅证明正确失败提示和原件保存。实际Safari、完整播放/声音/后台、键盘触控、大文件及全部Web人工/故障门禁继续，不能用迁移/下载通过代替。

![公网Chromium媒体与原生失败提示]({shot})

![公网Windows WebKit保留原件并显示失败提示]({webkit_shot})

[Chromium完整所选页MP4](media/studio-149/Chromium-original-download-and-replacement.mp4) / [GIF](media/studio-149/Chromium-original-download-and-replacement.gif) · [WebKit完整所选页MP4](media/studio-149/WebKit-native-failure-and-original-download.mp4) / [GIF](media/studio-149/WebKit-native-failure-and-original-download.gif)。录像为合成样片交互，不是GPU生成或Safari播放验收。

## English

Release 149.1 fixes original media filenames, main/alternate batch-image downloads and native playback-error handling. Stored Blob MIME selects the extension; a missing keyed original is refused instead of using a cached URL. Native decoder errors show an original-download action without changing successful task state or submitting generation. Real file replacements reset source-specific errors; layer focus now updates the correct action bar and closes an unrelated panel. Downloads do not open an editor.

Three native Windows engines passed 36 local and 36 public UI groups, verifying 78 actual original downloads and 18 screenshots. Chromium/Firefox load the four valid audio/video samples; Windows WebKit still cannot decode them and is accepted only for explicit failure handling and byte-identical originals. Its previously observed Cookie issue is unresolved; actual Safari, full playback, keyboard/touch/background, large files and full Web acceptance remain HOLD. The original API and every service/configuration remain unchanged; only independent static files were promoted with a compatible 149.0 recovery package. Earlier failed observations are preserved separately. This release has no native app, virtualization, new business Key or GPU generation.

[全部46项](10-开发进度与交付记录.md) · [独立部署](21-独立画布部署与使用.md) · [最终复查](research/studio-implementation/independent-review-149.json)
'''
put(DOCS / '34-Web媒体原件下载与播放失败处理.md', doc)
implemented = '''## 本轮实现 I-157～I-164 · Web静态149.1

[文档34](34-Web媒体原件下载与播放失败处理.md)：原件后缀、播放失败提示和图层操作栏已修复。三引擎本机/公网各36组，78次实际下载、18张通过截图及两MP4/两GIF。Windows WebKit解码/Cookie和实际Safari仍未签收；完整Web门禁HOLD。原API不改。

| 编号 | 本轮已实现 | 验证与边界 |
| --- | --- | --- |
| I-157 | 普通和批次媒体按原件MIME下载 | WebM/MP4/WAV/FLAC/JPEG/WebP/未知bin实际字节与后缀核对；旧WebM固定mp4复现并修复 |
| I-158 | 原生音视频失败提示与原件按钮 | 有效/无效素材均按真实错误处理；生成任务状态与原件不变，零生成POST |
| I-159 | 缺件拒绝、替换恢复及图层操作修复 | 不用残留URL冒充原件；实际文件选择后新来源重新判断，操作栏跟随当前节点，下载不打开面板 |
| I-160 | 三引擎本机和公网实际页面验证 | 36+36组、78次下载、18截图；Chromium/Firefox载入正常，Windows WebKit只签收失败提示和原件 |
| I-161 | 最终冻结构建和相关回归 | 203单元/类型/构建、18协议、11双源、12发布守卫及ZIP/清理页面；早期失败和中间构建保留 |
| I-162 | 独立149.1静态交付与149.0恢复包 | 完整v5契约/全部新旧资源核对；不改API、配置和服务PID，149未新增实际回退演练 |
| I-163 | 页面截图、完整所选页MP4/GIF及资源关闭 | 两MP4两GIF完整解码，所有测试浏览器/页面/HTTP夹具关闭，不关闭用户标签 |
| I-164 | 双语说明与全部46项同步 | 文档34、部署/更新说明、待办与复查回执同步；仅独立功能分支，完整Web验收HOLD |
'''
insert(DOCS / '10-开发进度与交付记录.md', implemented)
updates = {
    'F-01': ('同锁原生隔离构建：203单元、类型与生产构建本轮重跑通过', '其他宿主系统的Web构建可复现性另验；无原生客户端'),
    'F-02': ('149.1冻结构建/18协议/12守卫及三引擎72媒体组、相关ZIP/清理通过', '完整Web人工/故障继续；WebKit Cookie/解码和实际Safari未签收'),
    'F-10': ('历史真实512×288/1.625秒MP4、浏览器播放及原件SHA另存通过', '更多合法视频规格/格式、Web浏览器完整生成和异常恢复组合'),
    'F-11': ('历史独立首帧模型真实MP4、播放及原件SHA另存通过', '更多首帧规格、输入格式、浏览器及故障恢复组合'),
    'F-12': ('历史独立双参考模型真实MP4及少图/非法角色/未声明权重422通过；角色为语义标签，数组位置决定槽位', '更多参考数量/顺序/角色、已发布模型规格与Web浏览器恢复组合'),
    'F-24': ('历史鉴权下载保留；新增三引擎本机/公网78次实际原件下载SHA/后缀一致', '物理链路截断与更多媒体/真实鉴权下载组合，合成原件不替代GPU'),
    'F-26': ('WebM及批次JPEG/WebP后缀已修复；原生失败提示/下载、缺件拒绝与替换恢复72组通过', 'Windows WebKit实际解码、更多编码/容器/贴图/压缩、完整播放/声音/后台'),
    'F-35': ('青柠原始CSS/字体不变；媒体提示/18截图，图层操作栏及下载误开面板已修复', '完整弹层、键盘焦点与对比度；失败截图另保留'),
    'F-36': ('三引擎本机/公网390px无文档横向溢出；实际桌面文件输入/下载通过', '更多网页尺寸与真实触控/键盘；390px是视口模拟，无原生App'),
    'F-37': ('证据147/148保留，149三引擎各本机/公网12媒体组通过并关闭全部浏览器', 'Windows WebKit Cookie/解码未签收；实际Safari、键盘/触控/后台'),
    'F-38': ('本机LAN及公网普通HTTP实际原件/提示/替换通过，静态字节和既有CORS复查', 'WebKit Cookie/解码、实际Safari及更多真实媒体HTTP组合'),
    'F-42': (f"149.1的{delivery['static_files_verified']}当前/{delivery['retained_previous_assets_verified']}旧资源与{len(delivery['routes'])}路由逐项通过", '更多子路径、已开旧页与其他平台浏览器'),
    'F-44': ('历史146实际兼容回退8项保留；149.0精确复用146.1并保留新旧资源，发布守卫通过', '149实际回退及更多版本、并发/中断/损坏、已开页矩阵；不任意回退'),
    'F-45': ('新增72媒体组/78实际下载/18截图、两MP4两GIF和失败回执；双语/全部46项同步', 'HOLD：WebKit隔离/解码、实际Safari及Web人工/真实配额/故障；容量为API外部依赖'),
}
progress = DOCS / '10-开发进度与交付记录.md'
lines = progress.read_text(encoding='utf-8').splitlines()
for index, line in enumerate(lines):
    parts = line.split('|')
    if len(parts) == 6 and parts[1].strip() in updates:
        evidence, remaining = updates[parts[1].strip()]
        parts[3], parts[4] = ' ' + evidence + ' ', ' ' + remaining + ' '
        lines[index] = '|'.join(parts)
put(progress, '\n'.join(lines) + '\n')

for language in ['zh', 'en']:
    file = DOCS / f'templates/independent-readme.{language}.md.in'
    text = file.read_text(encoding='utf-8')
    if language == 'zh':
        text = text.replace('| 独立画布146.1 |', '| 独立画布149.1 |').replace('当前146.1交付 **Web端**', '此前146.1交付 **Web端**')
        summary = f'''**当前Web静态149.1已部署**：[原件下载与播放失败处理](docs/34-Web媒体原件下载与播放失败处理.md)。普通WebM、JPEG/WebP批次原件后缀已修复；原生解码失败显示原件下载，缺件明确拒绝，替换后重新判断。图层操作栏跟随当前节点，下载不误开编辑面板。三引擎本机/公网各36组、78次实际下载、18截图，203单元/类型/构建及相关回归通过。Windows WebKit解码与Cookie、实际Safari及完整Web签收仍 **HOLD**。仅切独立静态目录，兼容149.0恢复包准备完成，原API与全部服务/配置不变。

![公网媒体原件与失败提示](docs/{shot})

[实际下载/替换MP4](docs/media/studio-149/Chromium-original-download-and-replacement.mp4) / [GIF](docs/media/studio-149/Chromium-original-download-and-replacement.gif) · [WebKit失败提示MP4](docs/media/studio-149/WebKit-native-failure-and-original-download.mp4) / [GIF](docs/media/studio-149/WebKit-native-failure-and-original-download.gif)。完整所选页面录像、合成小文件，本轮没有GPU生成；下载通过不代表WebKit播放通过。下方146/147/148是历史证据。'''
    else:
        text = text.replace('| Independent canvas146.1 |', '| Independent canvas149.1 |').replace('Current146.1 delivers a **Web application**', 'Earlier146.1 delivered a **Web application**')
        summary = f'''**Web static 149.1 is deployed:** [Original downloads and native playback errors](docs/34-Web媒体原件下载与播放失败处理.md). General WebM and main/alternate JPEG/WebP downloads now preserve the stored original format. Native decode failures show an original-download action; missing keyed originals are refused, and real file replacement resets source-specific errors. Layer focus keeps the correct action bar and downloads do not open an editor. Three engines passed 36 local plus 36 public groups, 78 actual downloads and 18 screenshots; 203 units/type/build and related regressions passed. Windows WebKit decoding/Cookie isolation, actual Safari and full Web acceptance remain **HOLD**. Only independent static files were promoted, with compatible 149.0 recovery prepared; the original API and all services/configuration stay unchanged.

![Public original media and native failure handling](docs/{shot})

[Actual download/replacement MP4](docs/media/studio-149/Chromium-original-download-and-replacement.mp4) / [GIF](docs/media/studio-149/Chromium-original-download-and-replacement.gif) · [WebKit failure-handling MP4](docs/media/studio-149/WebKit-native-failure-and-original-download.mp4) / [GIF](docs/media/studio-149/WebKit-native-failure-and-original-download.gif). Complete selected pages, small synthetic fixtures, no GPU generation. Download success does not establish WebKit playback. The 146/147/148 evidence below is historical.'''
    text = text.replace('canvas-20261030.146.1/`', 'canvas-20261031.149.1/`')
    put(file, text)
    insert(file, summary)

insert(DOCS / 'README.md', '[34 · 当前Web149.1原件下载与播放失败处理](34-Web媒体原件下载与播放失败处理.md)：WebM/批次图片后缀、原生失败提示、缺件拒绝/替换和图层操作栏已修复；三引擎本机/公网72组、78次下载及完整截图/MP4/GIF。原API/服务不改，WebKit隔离/解码与完整Web签收仍HOLD。以下146/147/148条目保留历史时点。')
put(DOCS / 'README.md', (DOCS / 'README.md').read_text(encoding='utf-8').replace('当前146.1独立上线，114当前/688旧资源与15路由通过', f"当前149.1独立上线，{delivery['static_files_verified']}当前/{delivery['retained_previous_assets_verified']}旧资源与{len(delivery['routes'])}路由通过"))
insert(DOCS / '21-独立画布部署与使用.md', f'''**当前149.1覆盖更新**：[文档34](34-Web媒体原件下载与播放失败处理.md)给出原件格式、失败提示、图层操作及72组/78下载/18截图的使用与验收。当前目录`/opt/infinite-canvas/releases/canvas-20261031.149.1/`，包{package['archive_bytes']:,}字节、SHA `{package['archive_sha256']}`，编译源码`{revision}`。{delivery['static_files_verified']}当前/{delivery['retained_previous_assets_verified']}旧资源、{len(delivery['routes'])}路由通过；兼容恢复149.0复用146.1应用并保留新旧资源，未重新演练149实际回退。原API137.1与所有服务/配置不变。WebKit解码/Cookie及完整Web人工门禁HOLD。下方146发布表与V-01～V-10保留原时点，不作为当前149的构建证据。''')
text = (DOCS / '21-独立画布部署与使用.md').read_text(encoding='utf-8').replace('当前交付 **canvas-20261030.146.1**', '历史146交付 **canvas-20261030.146.1**').replace('## 服务器与静态发布', '## 历史146服务器与静态发布').replace('## 当前Web验证', '## 历史146 Web验证')
put(DOCS / '21-独立画布部署与使用.md', text)
for number in [20, 31, 32, 33]:
    file = next(DOCS.glob(str(number) + '-*.md'))
    insert(file, '**149.1补充**：[文档34](34-Web媒体原件下载与播放失败处理.md)已修复普通WebM/批次图片后缀并实现原生播放失败提示、缺件拒绝及替换恢复；三引擎本机/公网共72组。Windows WebKit解码与Cookie仍未签收。仅独立静态更新，原API不改；下方保留各自历史证据，当前部署见文档21。')
insert(DOCS / '23-独立画布更新记录.md', '''## canvas-20261031.149.1 · Web原件下载与播放失败处理 / pending-test

- [修复] 普通WebM及批次JPEG/WebP下载使用本机原件MIME/字节；缺少存储原件明确拒绝，不回退残留URL。
- [新增] 原生video/audio失败提示和原件下载，真实替换后按新来源判断；图层操作栏同步当前节点，下载不打开编辑面板。
- [验证] 三引擎本机/公网36+36组、78次下载、18截图；203单元/类型/构建、18协议、11双源、12守卫及ZIP/清理回归。
- [交付] 独立静态149.1与兼容149.0，全部新旧资源/路由核对；双语、文档34、两MP4两GIF和失败留证更新。原API/全部服务/配置不变。
- [待验] Windows WebKit解码/Cookie、实际Safari、完整播放/声音/后台、键盘触控、配额/故障及全部46项；完整Web签收HOLD。

Web149.1 preserves original media formats, refuses missing keyed bytes, and handles native playback errors without failing a completed task. Real replacement and layer action selection are verified. Three engines passed 72 local/public groups with 78 actual downloads. Compatible recovery is prepared; only independent static files changed. Windows WebKit decoding/Cookie isolation, actual Safari and full Web acceptance remain HOLD.''')
for suffix in ['.mdx', '.zh-CN.mdx']:
    for kind in ['todo', 'pending-test']:
        file = FRONT / f'docs/content/docs/progress/{kind}{suffix}'
        text = file.read_text(encoding='utf-8')
        marker = '{/* independent149:start */}'
        block = marker + '\n\nWeb 149.1: original WebM/JPEG/WebP filenames, native audio/video error fallback, missing-original refusal, real replacement and focused layer actions are implemented. Three engines passed 36 local + 36 public groups, 78 actual downloads and 18 screenshots; 203 units/type/build, related regressions and compatible 149.0 static recovery are recorded in independent document 34. Two complete selected public page MP4/GIF recordings and earlier failures are retained. Windows WebKit decoding/Cookie isolation, actual Safari, full playback/voice/background, keyboard/touch/quota/faults and all F-01–F-46 remain HOLD/pending-test. Web only; original API/services unchanged, no native app or GPU generation.\n\n{/* independent149:end */}'
        if marker in text: text = re.sub(r'\{/\* independent149:start \*/\}.*?\{/\* independent149:end \*/\}', lambda _: block, text, count=1, flags=re.S)
        else:
            position = text.index('\n', text.index('# '))
            text = text[:position] + '\n\n' + block + '\n' + text[position:]
        put(file, text)
print(json.dumps({'passed': True, 'document': '34', 'implemented_ids': 'I-157–I-164', 'all_backlog_ids_preserved': True, 'api_written': False}))
