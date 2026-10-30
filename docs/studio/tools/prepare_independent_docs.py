"""Prepare frontend-only documentation. Never writes either API repository."""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
FRONT = ROOT / 'infinite-canvas'
TARGET = FRONT / 'docs/studio'
HISTORICAL = ROOT / 'aggregator-service/docs/studio'
HISTORY_URL = 'https://github.com/copypasteltd/comfyUI-aggregator-proxy/blob/843aac47791f23755e71f976a00cf592380ddf99/docs/studio/'
LINK = re.compile(r'(!?\[[^\]\n]*\]\()([^\s\n)]+)(\))')
ACTIVE_MEDIA = ('media/studio-138/', 'media/studio-139/', 'media/studio-1392/', 'media/studio-140/', 'media/studio-141/', 'media/studio-142/', 'media/studio-143/', 'media/studio-144/', 'media/studio-145/', 'media/studio-146/', 'media/studio-147/', 'media/studio-148/')
ACTIVE_REPORTS = {
    'independent-package-1381.json', 'independent-delivery-1383.json',
    'independent-public-browser-1381.json', 'independent-live-1381.json',
    'independent-delivery-1382-failed.json',
    'independent-browser-2026-10-30T07-00-28-078Z.json',
    'clean-build-2026-10-30T06-48-37-964Z.json',
    'independent-review-1381.json',
    'independent-package-1391.json', 'independent-package-1392.json',
    'independent-delivery-1391.json', 'independent-delivery-1392.json',
    'independent-static-route-1392.json', 'independent-media-1392.json', 'independent-live-1392.json',
    'independent-generation-submit-1391.json', 'independent-generation-collect-1391.json',
    'independent-offline-resume-1392.json', 'independent-offline-resume-1392-attempt2.json',
    'independent-offline-resume-1392-attempt3.json', 'independent-offline-resume-1392-attempt4.json',
    'offline-identity-1392.json', 'independent-review-1392.json',
    'independent-browser-2026-10-30T08-00-35-492Z.json',
    'independent-browser-2026-10-30T08-24-37-388Z.json',
    'clean-build-2026-10-30T07-54-31-148Z.json', 'clean-build-2026-10-30T08-21-55-487Z.json',
    'task-faults-2026-10-30T09-35-00-159Z.json', 'task-faults-2026-10-30T09-35-22-232Z.json',
    'polling-ui-2026-10-30T09-41-29-131Z.json',
    'independent-batch-interrupted-140.json', 'independent-batch-resume-prepared-140.json',
    'independent-batch-verified-submit-140.json', 'independent-batch-verified-resume-prepared-140.json',
    'independent-batch-verified-collect-140.json', 'independent-host-unchanged-140.json',
    'independent-batch-verified-collect-140-attempt2.json', 'independent-batch-verified-collect-140-attempt3.json',
    'independent-original-140.json', 'independent-native-download-140.json',
    'independent-batch-backup-140.json',
    'independent-load-140.json',
    'independent-media-140.json', 'independent-live-140.json', 'independent-review-140.json',
}


def put(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def copy_public(source: Path, target: Path):
    if source.parent == DOCS / 'research/studio-implementation' and source.suffix == '.json' and target.is_file():
        # Preserve already-published receipt bytes across Git/Windows newline rules.
        assert source.read_bytes().replace(b'\r\n', b'\n') == target.read_bytes().replace(b'\r\n', b'\n'), f'Existing immutable receipt differs: {source.name}'
        return
    if target.is_file() and source.stat().st_size == target.stat().st_size and source.read_bytes() == target.read_bytes():
        return
    shutil.copyfile(source, target)


def main():
    assert FRONT.resolve().is_relative_to(ROOT.resolve())
    for version in ['141', '142', '143', '144', '145', '146', '147', '148']:
        current = ROOT / ('.local/studio' + version + '/receipts.json')
        if not current.is_file():
            continue
        names = json.loads(current.read_text(encoding='utf-8')).values()
        for name in names:
            assert re.fullmatch(r'[A-Za-z0-9-]+\.json', name) and (DOCS / 'research/studio-implementation' / name).is_file()
            ACTIVE_REPORTS.add(name)
        ACTIVE_REPORTS.add('independent-review-' + version + ('' if version in {'147', '148'} else '1') + '.json')
    ACTIVE_REPORTS.update({'archive-import-2026-10-30T10-42-03-468Z.json', 'archive-import-2026-10-30T10-43-48-579Z.json', 'archive-import-2026-10-30T10-50-23-649Z.json', 'clean-build-2026-10-30T10-28-45-254Z.json', 'clean-build-2026-10-30T10-37-49-073Z.json', 'clean-build-2026-10-30T10-46-27-970Z.json', 'independent-browser-2026-10-30T10-42-00-613Z.json', 'independent-browser-2026-10-30T10-50-22-944Z.json', 'independent-host-unchanged-141-preflight.json'})
    ACTIVE_REPORTS.update({'reference-probe-setup-failed-143.json', 'reference-deletion-2026-10-30T11-52-46-814Z.json', 'reference-deletion-2026-10-30T11-53-26-725Z.json'})
    ACTIVE_REPORTS.update({'media-tools-2026-10-30T12-24-11-634Z.json', 'media-tools-2026-10-30T12-25-29-257Z.json', 'media-tools-2026-10-30T12-26-55-868Z.json', 'media-tools-public-2026-10-30T12-28-24-006Z.json'})
    for language, master in [('zh', 'templates/independent-readme.zh.md.in'), ('en', 'templates/independent-readme.en.md.in')]:
        content = (DOCS / master).read_text(encoding='utf-8')
        readme = 'README.md' if language == 'zh' else 'README.en.md'
        put(ROOT / readme, content.replace('[LICENSE](LICENSE)', '[LICENSE](infinite-canvas/LICENSE)')
            .replace('[MIT许可](LICENSE)', '[MIT许可](infinite-canvas/LICENSE)')
            .replace('[MIT license](LICENSE)', '[MIT license](infinite-canvas/LICENSE)'))
        put(FRONT / readme, content.replace('(docs/', '(docs/studio/'))
    put(FRONT / 'CHANGELOG.studio.md', (DOCS / '23-独立画布更新记录.md').read_text(encoding='utf-8'))

    queue = [DOCS / 'README.md'] + [p for p in DOCS.glob('*.md') if p.name[:2].isdigit() and 6 <= int(p.name[:2]) <= 21]
    queue.append(DOCS / '23-独立画布更新记录.md')
    queue.append(DOCS / '24-真实故障恢复与离线素材验收.md')
    queue.append(DOCS / '25-轮询取消与真实批次验收.md')
    queue.append(DOCS / '26-画布ZIP整包导入与备份边界.md')
    next_document = DOCS / '27-历史素材引用备份与跨页面冲突.md'
    if next_document.is_file():
        queue.append(next_document)
    reference_document = DOCS / '28-历史引用原件删除保护与索引升级.md'
    if reference_document.is_file():
        queue.append(reference_document)
    media_document = DOCS / '29-本机原件引用检查与GLB动画.md'
    if media_document.is_file():
        queue.append(media_document)
    cleanup_document=DOCS/'30-新版原件安全清理与页面占用保护.md'
    if cleanup_document.is_file(): queue.append(cleanup_document)
    rollback_document = DOCS / '31-Web交付范围与兼容静态回退.md'
    if rollback_document.is_file(): queue.append(rollback_document)
    compatibility_document = DOCS / '32-Web浏览器兼容性与验证边界.md'
    if compatibility_document.is_file(): queue.append(compatibility_document)
    migration_document = DOCS / '33-Web混合媒体跨浏览器备份恢复.md'
    if migration_document.is_file(): queue.append(migration_document)
    ACTIVE_REPORTS.update(file.name for file in (DOCS/'research/studio-implementation').glob('managed-cleanup-*.json'))
    for pattern in ['safe-release-*.json', 'safe-rollback-Web-*.json', 'cleanup-refresh-*.json', 'Web-engine-diagnostic-*.json', 'Web-credentials-diagnostic-*.json', 'Web-migration-*.json']:
        ACTIVE_REPORTS.update(file.name for file in (DOCS / 'research/studio-implementation').glob(pattern))
    ACTIVE_REPORTS.update({'archive-import-public-firefox-2026-10-30T15-11-29-918Z.json', 'independent-browser-webkit-2026-10-30T15-11-49-620Z.json', 'archive-import-public-webkit-2026-10-30T15-09-41-429Z.json', 'cleanup-refresh-public-webkit-2026-10-30T15-07-56-788Z.json', 'managed-cleanup-ui-public-webkit-2026-10-30T15-08-49-813Z.json', 'managed-cleanup-ui-public-firefox-2026-10-30T15-09-08-729Z.json'})
    copied: set[Path] = set()

    def handle_link(match, source):
        raw = match[2]
        if raw.startswith(('#', 'http:', 'https:', 'mailto:', 'data:', 'app:')):
            return match[0]
        path_part, marker, fragment = unquote(raw).partition('#')
        local = (source.parent / path_part).resolve()
        if not local.is_relative_to(DOCS.resolve()):
            # Root README references in the docs index point to the independent frontend README.
            if local == ROOT / 'README.md':
                return match[1] + '../../README.md' + match[3]
            if local == ROOT / 'README.en.md':
                return match[1] + '../../README.en.md' + match[3]
            for repository in [ROOT / 'aggregator-service', ROOT.parent / '生图生视API封装']:
                if local.is_relative_to(repository.resolve()):
                    relative = local.relative_to(repository.resolve())
                    assert relative.parts[0] == 'docs' and (ROOT / 'aggregator-service' / relative).is_file()
                    url = HISTORY_URL.removesuffix('docs/studio/') + quote(relative.as_posix(), safe='/')
                    return match[1] + url + (marker + fragment if marker else '') + match[3]
            return match[0]
        relative = local.relative_to(DOCS.resolve()).as_posix()
        if local.is_dir():
            if relative == 'research/studio-implementation':
                return match[0]
            if (HISTORICAL / relative).is_dir():
                return match[1] + HISTORY_URL.replace('/blob/', '/tree/') + quote(relative, safe='/') + match[3]
            return match[0]
        if not local.is_file():
            # The final reviewer materializes its own receipt in both doc trees
            # before publication; all other missing targets remain fatal.
            if local in {DOCS / 'research/studio-implementation/independent-review-147.json', DOCS / 'research/studio-implementation/independent-review-148.json'}:
                return match[0]
            raise AssertionError(f'Missing documentation target: {source.name}: {raw}')
        current = relative.startswith(ACTIVE_MEDIA) or local.name in ACTIVE_REPORTS or relative.startswith('deployment/')
        if not current and (HISTORICAL / relative).is_file() and local.suffix.lower() not in {'.md'}:
            return match[1] + HISTORY_URL + quote(relative, safe='/') + (marker + fragment if marker else '') + match[3]
        queue.append(local)
        return match[0]

    while queue:
        source = queue.pop(0).resolve()
        if source in copied:
            continue
        assert source.is_file() and source.is_relative_to(DOCS.resolve())
        relative = source.relative_to(DOCS.resolve())
        target = TARGET / relative
        assert target.resolve().is_relative_to(TARGET.resolve())
        if source.suffix.lower() == '.md':
            text = source.read_text(encoding='utf-8')
            if source.name[:2].isdigit() and 6 <= int(source.name[:2]) <= 19 and not source.name.startswith('10-'):
                text = '> 历史融合文档：前端契约可继续参考；涉及原API的改造/发布不再执行。当前架构以文档20为准，独立部署只使用文档21。\n\n' + text
            text = LINK.sub(lambda match: handle_link(match, source), text)
            put(target, text)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_public(source, target)
        copied.add(source)

    # Include current public evidence and all current images/clips without a backend source mirror.
    media = [file for directory in ('studio-138', 'studio-139', 'studio-1392', 'studio-140', 'studio-141', 'studio-142', 'studio-143', 'studio-144', 'studio-145', 'studio-146', 'studio-147', 'studio-148') for file in (DOCS / 'media' / directory).rglob('*') if file.is_file() and (file.suffix.lower() != '.webm' or directory == 'studio-148' and file.parent.name == 'fixtures')]
    for source in media + [DOCS / 'research/studio-implementation' / n for n in ACTIVE_REPORTS]:
        if source.is_file():
            target = TARGET / source.relative_to(DOCS)
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_public(source, target)
    for source in (DOCS / 'research/studio-implementation').glob('independent-api-boundary-*.json'):
        copy_public(source, TARGET / source.relative_to(DOCS))

    tree = ast.parse((DOCS / 'tools/studio_independent_host.py').read_text(encoding='utf-8'))
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in {'NGINX', 'SERVICE'}}
    for name, filename in [('NGINX', 'nginx.conf'), ('SERVICE', 'infinite-canvas-static.service')]:
        for base in [DOCS, TARGET]:
            put(base / 'deployment' / filename, constants[name])
    print(json.dumps({'frontend_only': True, 'api_written': False, 'linked_files': len(copied), 'target': TARGET.relative_to(ROOT).as_posix()}))


if __name__ == '__main__':
    main()
