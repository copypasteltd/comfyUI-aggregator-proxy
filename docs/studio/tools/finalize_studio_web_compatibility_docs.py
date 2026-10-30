"""Publish Web-only compatibility scope, without changing frozen application sources."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
FRONT = ROOT / 'infinite-canvas'
NAMES = json.loads((ROOT / '.local/studio147/receipts.json').read_text(encoding='utf-8'))
EVIDENCE = DOCS / 'research/studio-implementation'
RECORDS = {key: json.loads((EVIDENCE / name).read_text(encoding='utf-8')) for key, name in NAMES.items()}
assert all(value['passed'] for value in RECORDS.values())
assert not RECORDS['credentials_webkit']['omit_cookies_absent']
assert RECORDS['credentials_firefox']['omit_cookies_absent'] and RECORDS['credentials_chromium']['omit_cookies_absent']


def put(file, text):
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding='utf-8', newline='\n')


def link(key):
    return f'[回执](research/studio-implementation/{NAMES[key]})'


def screenshot(key, suffix):
    return next(item['file'].removeprefix('docs/') for item in RECORDS[key]['screenshots'] if item['file'].endswith(suffix))


firefox_image = screenshot('archive_firefox', 'archive-restored-canvas-desktop.png')
webkit_image = screenshot('cleanup_webkit', 'public-desktop-cleaned-and-retained.png')
firefox_small = screenshot('archive_firefox', 'archive-restored-canvas-390.png')
webkit_small = screenshot('cleanup_webkit', 'public-390-bottom-original-controls.png')
rows = [
    ('I-142', '原生Web浏览器验收工具', 'SDK与浏览器revision匹配、独立持久profile、ASCII路径realpath核对和finally关闭；仅测试工具变化'),
    ('I-143', '修正损坏的测试PNG', '旧样片IDAT CRC损坏；两份69字节RGB样片经Pillow校验/解码，Firefox实际恢复图片通过；产品不改'),
    ('I-144', 'Firefox新增38组通过', '公网快照3、实际清理10、ZIP14；本机原生双HTTP源目录/Schema/无Cookie11；含390px视口'),
    ('I-145', 'Windows WebKit新增27组通过', '公网快照3、实际清理10、ZIP14；真实文件输入/下载/刷新及并发引用保护通过，鉴权Cookie隔离未签收'),
    ('I-146', 'Cookie差异最小对照', '不加载产品的两端口fetch、协议/HTTP两种Cookie来源、include正对照：Chromium/Firefox omit正常；Windows WebKit仍携带Cookie'),
    ('I-147', '页面证据和完整所选页录屏', '新增Firefox/WebKit截图，两MP4两GIF完整解码；合成文件、零生产业务API/生成请求，旧失败保留'),
    ('I-148', '冻结产品与线上只读复查', '继续146.1/db56cae；原API137.1源码/配置/指针/全部服务PID及静态服务/入口摘要未变，无重部署/重启'),
    ('I-149', '双语说明与完整待办同步', '文档32、README、更新说明、四份待办/待测及F-01～F-46同步，发布独立功能分支；完整Web验收HOLD'),
]
implementation_table = '\n'.join(['| 编号 | 本轮已实现 | 验证与边界 |', '| --- | --- | --- |'] + [f'| {a} | {b} | {c} |' for a, b, c in rows])

detail = f'''# 32 · Web浏览器兼容性与验证边界

当前网页继续使用 **canvas-20261030.146.1**，源码 `db56cae3634da0361074e84024ccd5b428d189fa`；[打开独立画布](http://43.165.167.179:39445/studio/)。本次是证据147与文档更新，没有新的产品版本、构建、部署或数据库变更。只考虑Web端，原生客户端与应用商店不在范围。原API137.1保持原样；完整Web签收仍 **HOLD/pending-test**。

## 已验证到什么程度

| 编号 | Windows原生浏览器引擎 | 本轮完成的专项 | 当前结论 |
| --- | --- | --- | --- |
| WB-01 | Chromium153.0.8010.12 | 沿用146.1已发布的203单元/类型/构建、原生/公网与兼容回退证据；新增最小Cookie对照 | 已有Web基线保留，本轮没有重跑整套构建 |
| WB-02 | Firefox155.0 / revision1543 | 公网3组最新快照、10组清理、14组ZIP；本机双原生HTTP源11组目录/Schema/请求隔离，共38组 | 这些操作通过，包含实际文件输入/下载和390px视口；未覆盖全部媒体/后台/故障 |
| WB-03 | WebKit26.6 / revision2359 | 公网3组最新快照、10组清理、14组ZIP，共27组 | 本机原件/恢复功能通过；跨源Cookie隔离失败，不能视为完整浏览器支持 |
| WB-04 | macOS/iOS Safari及其他实际浏览器平台 | 本轮未运行 | Windows WebKit不等同于实际Safari；网页触控、键盘/后台及更多媒体继续验收，不增加原生App需求 |

以上65组是新增专项通过数，三个最小Cookie诊断不混入功能通过数。390px是网页视口模拟，没有据此宣称实际触控设备通过。公网用例读取当前静态应用；目录用例使用本机两个不同端口的原生HTTP服务和合成既有API协议，未请求生产业务API、未创建Key或GPU任务。

{implementation_table}

## 为什么调整测试方式

此前工具使用的SDK1.62.1与本机已安装的Firefox1543/WebKit2359不匹配。现在使用已有、只读的Playwright Core1.63.0，逐项核对其browser revision与原生可执行文件并记录元数据SHA。SDK需要匹配浏览器二进制，见[Playwright官方说明](https://playwright.dev/docs/browsers)。未安装或启动Docker、WSL、虚拟机，也未在其他工程写文件。

WebKit临时上下文写入IndexedDB的Blob/File出现UnknownError，换成独立持久profile后完成写入。官方问题也确认临时会话中的这一限制，并列出了持久profile的验证结果，见[Playwright问题42795](https://github.com/microsoft/playwright/issues/42795)。本次没有将Blob改成字符串、降低事务持久性或添加产品超时。

测试profile及临时文件实际位于本根目录的忽略`.local/studio147/`。使用既有ASCII目录联接前，核对它与`.local`的realpath相同。Firefox需要先创建测试页，再关闭启动时的空白页，以保留其浏览器窗口；所有自建页、context、浏览器和夹具最终关闭，不操作用户标签。20秒只是工具的单步观察窗口，不是产品超时、重试或大小限制。

旧1×1测试PNG含损坏IDAT校验码，Firefox拒绝解码。新普通/参考样片均为有效RGB PNG，独立长度/SHA与Pillow校验、解码保留；使用同一份冻结产品后，恢复图片及刷新检查通过。原件保存仍核对原始字节，不通过重编码掩盖产品损坏。{link('fixture_validation') if 'fixture_validation' in NAMES else '[样片校验](research/studio-implementation/Web-fixtures-147.json)'}。

## Cookie隔离的未完成项

产品请求仍明确使用`credentials: omit`、Bearer、禁止自动重定向；没有借用Admin会话。新增目录验收先在独立测试浏览器安装合成Cookie，再以服务器实际接收的请求头核对。Firefox的11组全部通过；Windows WebKit的页面操作完成，但GET与预检均出现Cookie，所以整份目录回执判为失败。

为判断差异是否来自产品，另运行不导入产品代码的两HTTP端口`fetch`最小对照：分别通过自动化协议、HTTP Set-Cookie设入合成Cookie；同时检查无鉴权头/带Bearer的omit以及include正对照。Chromium与Firefox的omit不带Cookie，Windows WebKit仍带Cookie。诊断回执的`passed`只表示对照完整运行，**WebKit的`omit_cookies_absent=false`明确不等于隔离通过**。

该观察定位到当前Windows WebKit运行环境，不能推导所有Safari版本有相同问题，也不能签收Safari。没有清理用户Cookie、放宽断言、改原API或增加代理。后续在实际Safari网页环境核对F-30/F-37/F-38；产品协议保持既有边界。

| 编号 | 实际回执 | 性质 |
| --- | --- | --- |
| WB-05 | {link('refresh_firefox')} / {link('refresh_webkit')} | 各3组真实事务及两个编辑页连接，晚到旧快照不覆盖最新状态 |
| WB-06 | {link('cleanup_firefox')} / {link('cleanup_webkit')} | 各10组有效PNG/音频文件输入、备份下载、引用复核、确认保留、原生占用及配额注入保护 |
| WB-07 | {link('archive_firefox')} / {link('archive_webkit')} | 各14组、12类坏ZIP；整包提交、原件SHA、新键/有序重复引用、刷新/显式重试/双页冲突保留 |
| WB-08 | {link('catalog_firefox')} | 本机两个原生HTTP源、11组目录/真实Schema Worker/390px/无Cookie请求，不是生产API实测 |
| WB-09 | [Windows WebKit目录失败](research/studio-implementation/independent-browser-webkit-2026-10-30T15-11-49-620Z.json) | 完成10组页面操作，最终请求头断言失败；不计入通过数 |
| WB-10 | {link('credentials_chromium')} / {link('credentials_firefox')} / {link('credentials_webkit')} | 各7次最小fetch对照，omit与include分别判定，不加载产品代码 |
| WB-11 | {link('host')} / {link('api_boundary')} | 原服务/静态服务/配置/源码/指针和入口摘要只读核对；零部署和重启 |
| WB-12 | {link('media')} / [交付复查](research/studio-implementation/independent-review-147.json) | 截图/MP4/GIF、源摘要、链接、全部F编号及已知私有Key排除 |

## 页面、录像及保留的失败

| 网页操作 | Firefox | Windows WebKit |
| --- | --- | --- |
| 桌面页面 | [合成作品恢复]({firefox_image}) | [仅清理未引用原件]({webkit_image}) |
| 390px视口 | [恢复的网页画布]({firefox_small}) | [滚动到底部下载控制]({webkit_small}) |
| 完整所选测试页录像 | [MP4](media/studio-147/Firefox-Web-project-restore.mp4) / [GIF](media/studio-147/Firefox-Web-project-restore.gif) | [MP4](media/studio-147/WebKit-Web-local-cleanup.mp4) / [GIF](media/studio-147/WebKit-Web-local-cleanup.gif) |

![Firefox恢复的合成画布]({firefox_image})

录像保留完整所选页面时间段并完整解码；它们没有涵盖全部并行页面，不展示真实生成，也不代表WebKit鉴权隔离通过。

先前失败保持原样：[临时WebKit Blob失败](research/studio-implementation/Web-engine-diagnostic-webkit-ascii-d943c6d5-11ac-4a23-945a-8e437fe23678.json)、[Firefox启动空窗口失败](research/studio-implementation/cleanup-refresh-public-firefox-2026-10-30T15-07-56-788Z.json)、[旧PNG恢复失败](research/studio-implementation/archive-import-public-firefox-2026-10-30T15-11-29-918Z.json)及WB-09的Cookie失败。其他诊断/已替代回归也保留，不覆盖失败、不重复累计通过数。

## 复现与后续

在规范融合工作区根目录运行工具，依赖已有冻结构建与原生SDK/浏览器。工具镜像用于审阅；不会安装浏览器或执行后端生成。`--sdk`可指定与已装原生引擎匹配的现有Playwright Core目录。工具提前核对revision，不能以更换二进制掩盖失败。

```powershell
node docs/tools/verify_studio_cleanup_refresh_race.mjs docs/research/studio-implementation/{NAMES['build']} --public --evidence-version=147 --browser=firefox
node docs/tools/verify_studio_managed_cleanup_ui.mjs docs/research/studio-implementation/{NAMES['build']} --public --evidence-version=147 --browser=webkit
node docs/tools/verify_studio_archive_import.mjs docs/research/studio-implementation/{NAMES['build']} --public --suite=147 --browser=firefox
# 目录与最小Cookie对照需显式指定本机现有的非回环IPv4；不修改系统hosts/防火墙。
```

源码：[原生浏览器工具](tools/studio_web_browser.mjs)、[启动/Blob诊断](tools/diagnose_studio_web_engines.mjs)、[文件清理界面](tools/verify_studio_managed_cleanup_ui.mjs)、[ZIP界面](tools/verify_studio_archive_import.mjs)、[快照竞态](tools/verify_studio_cleanup_refresh_race.mjs)、[双源目录](tools/verify_independent_canvas.mjs)、[最小Cookie对照](tools/diagnose_studio_web_credentials.mjs)、[媒体编码](tools/record_studio_web_compatibility_media.py)、[只读宿主复查](tools/verify_independent_host_unchanged.py)、[交付复查](tools/review_studio_web_compatibility.py)。

F-15/F-27/F-34/F-36/F-37/F-38补入上述有限证据；真实配额/跨进程、长后台/离线、更多媒体和实际Safari网页仍有待验收。旧普通原件迁移/暂存孤儿、派生预览及不确定占用恢复保留F-27；完整剩余事项见[全部46项](10-开发进度与交付记录.md)。原API的设备容量、01移交即删和可选助手能力继续按外部依赖处理，不以Web兼容工作改写转发系统。
'''
put(DOCS / '32-Web浏览器兼容性与验证边界.md', detail)

progress = DOCS / '10-开发进度与交付记录.md'
text = progress.read_text(encoding='utf-8')
text = text.replace('## 本轮实现 I-134～I-141', '## 前轮实现 I-134～I-141', 1)
section = f'''## 本轮实现 I-142～I-149 · 证据147，静态版本保持146.1

仅Web端，冻结产品源码不变。本轮新增Firefox38组、Windows WebKit27组通过；Windows WebKit Cookie隔离明确未签收。完整证据、截图和两MP4/两GIF见[文档32](32-Web浏览器兼容性与验证边界.md)。原API与静态服务均未修改、重启或重新部署；203单元/类型/构建沿用146.1冻结回执，不冒充本轮重测。

{implementation_table}

'''
text = text.replace('## 前轮实现 I-134～I-141', section + '## 前轮实现 I-134～I-141', 1)
text = text.replace('代码/合成检查、线上协议、真实GPU与物理手机分别记录。', '代码/合成检查、线上协议、真实GPU与实际网页设备分别记录；原生App不在范围。', 1)
updates = {
    2: ('继承203单元/类型/冻结构建、18原生及12发布守卫；新增Firefox38/WebKit27组专项通过', '完整Web人工/故障验收继续；WebKit Cookie隔离未签收'),
    7: ('历史1024×1024和720×1080实际PNG、原件SHA/另存/载入/刷新通过', '更多规格/故障及跨浏览器完整生成链路；历史单项1,458秒非SLA'),
    8: ('历史720×1080实际编辑、原件SHA与8小时时限通过；丢响应仍同一任务', '蒙版、更多已发布规格及浏览器恢复另验'),
    9: ('历史720×1080双参考PNG、原件摘要与非法角色/未声明权重拒绝通过', '更多Workflow、参考数量/角色和Web浏览器组合'),
    15: ('同库文档/原件保护及索引升级保留；新增Firefox/WebKit实际原件输入、ZIP提交/中止/引用复核', '真磁盘/配额、跨进程及更多版本故障；注入不等于真实耗尽'),
    16: ('历史双页首次发送竞争及真实未知响应恢复原ID保留；本轮未提交生成', '长离线、不同浏览器独立库及并发/故障矩阵'),
    17: ('历史实际GPU丢响应刷新只读找回原job，正文/Idem/摘要不变', '长离线/后台、其他媒体与浏览器；未查到不补发'),
    18: ('历史真实离线原件恢复保留；Firefox/WebKit实际ZIP新键、主图/嵌套图和刷新各通过', '更长离线/后台、全部媒体与更多平台；不宣称全站离线安装'),
    20: ('历史真实上传丢响应后复用原Idem/asset ID/SHA且不提前生成；28专项保留', '素材到期、长离线与更多媒体恢复'),
    21: ('历史14原生组/36查询错误组合及6冻结UI通过；本轮仅Web存储/目录专项', '真实长断网/后台、跨引擎轮询/权限与服务故障'),
    22: ('原生取消竞态及历史真实排队取消、丢响应按原ID确认终态通过', '实际运行/完成瞬间取消、其他设备和媒体；合成不替代GPU实测'),
    23: ('历史合成/原生及真实成功/取消成员、原ID恢复与v2备份保留', '真实失败兄弟项、其他媒体、长离线与Web浏览器'),
    24: ('历史真实等长损坏/截短拒绝、正常原件SHA一致；新增两引擎合成原件下载字节一致', '物理链路截断、更多媒体和跨引擎真实鉴权下载'),
    27: ('新受控原件事务回收/最新快照保护保留；Firefox/WebKit各10清理+3竞态通过，旧原件保留', '旧库迁移/暂存孤儿、派生预览、不确定占用恢复及真实配额耗尽'),
    30: ('历史真实双Key404/Admin401/只读生成403保留；Firefox带Cookie目录仍只发Bearer且不带Cookie', 'Windows WebKit omit最小对照失败，实际Safari和更多授权组合待验'),
    31: ('原执行入口/CSP不变；两引擎各12类坏ZIP、脚本形状文本不执行/不重建任务通过', '全部不可信导入/持久/开发入口负向矩阵'),
    33: ('历史导出凭证过滤与清单保留；Firefox/WebKit实际ZIP剔除结构化Cookie/header/fragment测试凭证', '完整导出扫描/异常配置；用户提示词原文仍需自行保管'),
    34: ('Firefox/WebKit各14组ZIP、新键/有序重复引用/再导出/刷新/冲突分支备份通过', '跨引擎实际导出互导、跨版本/更多媒体灾备；云同步仅既有API能力否则外部依赖'),
    35: ('原青柠CSS/字体保留；Firefox/WebKit桌面/390px清理与恢复截图更新', '完整弹层、键盘焦点及对比度'),
    36: ('两引擎390px网页无横向溢出，滚动可达下载/保留/返回控制，桌面回归通过', '更多网页尺寸、实际触控/键盘交互；原生客户端不在范围'),
    37: ('Chromium基线保留；新增Firefox38/WebKit27专项，匹配SDK与独立持久profile且全部关闭', 'Windows WebKit Cookie隔离未签收；实际Safari网页、键盘/触控/后台及更多媒体继续'),
    38: ('公网普通HTTP两引擎原件/ZIP/刷新通过；Firefox双原生HTTP源Schema/无Cookie通过', 'Windows WebKit Cookie隔离、实际Safari及更多媒体HTTP组合'),
    42: ('146.1的114当前/688旧资源及15路由保留；两引擎实际公网canvas/maintenance懒加载通过', '更多子路径、已开旧页、其他平台浏览器'),
    43: ('v4→v5和旧调用门禁保留；两引擎清理夹具实际从v4进入v5、数据保留', '跨宿主/更多版本升级、中断与故障；禁止降级'),
    45: ('新增65组Web专项、三引擎Cookie对照、截图及完整所选页两MP4/两GIF；全部46项保留', 'HOLD/pending-test：WebKit隔离、Web人工/真实配额与故障；容量属原API外部依赖'),
    46: ('原服务已有600秒空闲回收实现和历史三渠道证据；本轮未发释放命令', '其他媒体/渠道长期保护继续观察；原API外部事项，不修改转发或Worker'),
}
for number, (implemented, remaining) in updates.items():
    pattern = rf'^\| F-{number:02} \| ([^|]+) \|[^\n]+$'
    def row(match, implemented=implemented, remaining=remaining, number=number):
        title = match[1].strip()
        if number == 37: title = 'Web浏览器/触控/键盘'
        return f'| F-{number:02} | {title} | {implemented} | {remaining} |'
    text, count = re.subn(pattern, row, text, flags=re.M)
    assert count == 1, number
put(progress, text)

index = DOCS / 'README.md'
text = index.read_text(encoding='utf-8')
text = text.replace('# 无限画布文档\n\n', '# 无限画布文档\n\n[32 · Web浏览器兼容性与验证边界](32-Web浏览器兼容性与验证边界.md)：证据147继续沿用146.1；Firefox新增38组、Windows WebKit本机功能27组通过，Cookie隔离未签收。有效PNG、全部原生浏览器关闭、两MP4/两GIF及失败回执保留；原API/部署不改，全部46项和Web人工门禁继续。\n\n', 1)
put(index, text)
for name in ['20-独立画布与既有API融合最终边界.md', '21-独立画布部署与使用.md', '31-Web交付范围与兼容静态回退.md']:
    file = DOCS / name
    text = file.read_text(encoding='utf-8')
    position = text.index('\n\n') + 2
    note = '补充[证据147：Web浏览器兼容性](32-Web浏览器兼容性与验证边界.md)：继续同一146.1冻结产品；Firefox38组、Windows WebKit本机功能27组通过，Cookie隔离未签收。此后续证据不重写下方原发布回执，也不增加原生客户端范围。\n\n'
    put(file, text[:position] + note + text[position:])

changelog = DOCS / '23-独立画布更新记录.md'
text = changelog.read_text(encoding='utf-8')
position = text.index('\n\n') + 2
note = '''## 证据147 · Web浏览器验收补充，产品仍146.1 / pending-test

- [验证] Firefox新增38组、Windows WebKit本机功能27组通过；包括真实文件输入/下载、ZIP原件/引用/刷新、双页保护及390px视口。
- [修正] 测试样片IDAT CRC损坏已替换为校验通过的RGB PNG；原生SDK/revision匹配、持久测试profile和无用启动页生命周期整理，产品源码不改。
- [待验] Windows WebKit携带Cookie的问题由不加载产品代码的最小fetch复现；Chromium/Firefox omit与include正对照正常。WebKit目录总回执失败并保留，不宣称实际Safari支持。
- [文档] 文档32、双语README、全部46项、截图及完整所选页两MP4/两GIF同步。原API与静态服务/指针/配置不变，无部署、重启、业务Key或生成请求。

Evidence147 keeps the frozen146.1 application. Firefox adds38 passed groups, Windows WebKit27 local-original/project groups; WebKit Cookie isolation remains unaccepted and reproduces without product code. Matching native browsers, valid PNG fixtures, screenshots and two complete selected page MP4/GIF recordings are documented. No new product release, rebuild, deployment, API/config/service change or GPU work. Full Web acceptance remains HOLD.

'''
put(changelog, text[:position] + note + text[position:])

for language in ['zh', 'en']:
    file = DOCS / f'templates/independent-readme.{language}.md.in'
    text = file.read_text(encoding='utf-8')
    if language == 'zh':
        text = text.replace('![公网Web本机安全清理](docs/media/studio-146/cleanup-2026-10-30T14-38-50-017Z/public-desktop-cleaned-and-retained.png)', f'![Firefox公网恢复的合成画布](docs/{firefox_image})', 1)
        text = text.replace('当前验收以Chromium为准；Firefox/WebKit未完成并保留记录，390px仅视口模拟。', '补充证据147新增Firefox38组、Windows WebKit本机功能27组通过；WebKit Cookie隔离未签收，实际Safari未验，390px仅视口模拟。', 1)
        marker = '下面为先前139.2/证据140的真实生成记录，本轮没有重新生成：'
        note = f'**Web兼容性补充（产品仍146.1）**：[文档32](docs/32-Web浏览器兼容性与验证边界.md)记录实际文件输入/下载、ZIP新键与引用、刷新、双页保护和Cookie差异。Firefox/[WebKit清理页面](docs/{webkit_image})及[390px网页](docs/{webkit_small})分别留证。[Firefox恢复MP4](docs/media/studio-147/Firefox-Web-project-restore.mp4) / [GIF](docs/media/studio-147/Firefox-Web-project-restore.gif) · [WebKit清理MP4](docs/media/studio-147/WebKit-Web-local-cleanup.mp4) / [GIF](docs/media/studio-147/WebKit-Web-local-cleanup.gif)。合成文件，完整所选页面录像，零生产业务API/GPU请求。修正损坏测试PNG后两引擎原件恢复通过；Windows WebKit无产品代码的最小omit测试仍带Cookie，因此不宣称完整WebKit/Safari支持。原API及静态产品均未重新部署。\n\n'
    else:
        text = text.replace('![Public Web local-original cleanup](docs/media/studio-146/cleanup-2026-10-30T14-38-50-017Z/public-desktop-cleaned-and-retained.png)', f'![Synthetic project restored on public Web in Firefox](docs/{firefox_image})', 1)
        text = text.replace('Current browser evidence is Chromium; Firefox/WebKit checks were interrupted and remain unaccepted.390px is viewport emulation.', 'Evidence147 adds38 passed Firefox groups and27 Windows WebKit local-original/project groups. WebKit Cookie isolation remains unaccepted; actual Safari is untested.390px is viewport emulation.', 1)
        marker = 'The following actual generation evidence comes from earlier139.2/evidence140, with no new generation this turn:'
        note = f'**Web compatibility supplement (application still146.1):** [Document32](docs/32-Web浏览器兼容性与验证边界.md) covers native file inputs/downloads, ZIP keys/references, reloads, concurrent editor protection and the Cookie difference. [WebKit cleanup](docs/{webkit_image}) and [390px Web viewport](docs/{webkit_small}) accompany [Firefox restore MP4](docs/media/studio-147/Firefox-Web-project-restore.mp4) / [GIF](docs/media/studio-147/Firefox-Web-project-restore.gif) and [WebKit cleanup MP4](docs/media/studio-147/WebKit-Web-local-cleanup.mp4) / [GIF](docs/media/studio-147/WebKit-Web-local-cleanup.gif). These complete selected page recordings use synthetic files and no production business API/GPU requests. Valid PNGs restore in both engines; minimal native fetch without product code still sends Cookies under omit in Windows WebKit. Full WebKit/Safari support is therefore unaccepted. No product/API deployment or restart.\n\n'
    assert marker in text
    put(file, text.replace(marker, note + marker, 1))

for language, suffix in [('en', '.mdx'), ('zh', '.zh-CN.mdx')]:
    for kind in ['pending-test', 'todo']:
        file = FRONT / f'docs/content/docs/progress/{kind}{suffix}'
        text = file.read_text(encoding='utf-8')
        if language == 'zh':
            text = text.replace('F-37其他Web浏览器和键盘/触控/后台（Firefox/WebKit自动化未完成）', 'F-37实际Safari网页、Windows WebKit Cookie隔离和键盘/触控/后台（证据147已完成Firefox38/WebKit本机功能27组）')
            note = '- 证据147继续冻结146.1，仅Web端：Firefox新增38组、Windows WebKit本机功能27组通过，实际文件/ZIP/刷新/双页保护与390px截图及两MP4/两GIF同步。WebKit Cookie隔离最小对照仍失败，实际Safari网页及完整人工/故障验收继续HOLD；参见独立文档32和全部F-01～F-46。无产品源码/构建/部署、API或服务变更。'
        else:
            text = text.replace('F-37 other Web engines and keyboard/touch/background behavior (Firefox/WebKit automation was incomplete)', 'F-37 actual Safari Web, Windows WebKit Cookie isolation and keyboard/touch/background behavior (evidence147 completed38 Firefox and27 WebKit local-original groups)')
            note = '- Evidence147 keeps frozen146.1 and Web-only scope:38 new Firefox groups and27 Windows WebKit local-original/project groups passed, with native files/ZIP/reload/editor protection,390px screenshots and two complete selected page MP4/GIF recordings. WebKit Cookie isolation still fails in a minimal native diagnostic; actual Safari Web and full manual/fault acceptance remain HOLD. See independent document32 and all F-01–F-46. No product source/build/deployment, API or service change.'
        # Keep frontmatter and the current Web-only scope paragraph before the new note.
        marker = 'Independent docs10/20/31 define the current scope and all46 items.' if language == 'en' else '最新范围及全部46项以独立文档10/20/31为准。'
        assert marker in text
        text = text.replace(marker, marker.replace('10/20/31', '10/20/31/32') + '\n\n' + note, 1)
        put(file, text)
print(json.dumps({'written': True, 'new_product_release': False, 'application_sources_changed': False, 'implemented_ids': [item[0] for item in rows], 'all_backlog_ids': 46}))
