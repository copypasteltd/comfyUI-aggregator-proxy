"""Bind current Studio documentation to the completed, immutable 132 receipts."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "aggregator-service"
EVIDENCE = ROOT / "docs/research/studio-implementation"


def update(path, replacements):
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in text, (str(path), old)
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def rows(path, values):
    lines = path.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        indices = [index for index, line in enumerate(lines) if line.startswith("| " + key + " |")]
        assert len(indices) == 1, key
        lines[indices[0]] = value
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    receipts = {
        "units": "unit-2026-10-30T00-08-24-142Z.json",
        "types": "typecheck-2026-10-30T00-08-24-139Z.json",
        "build": "build-2026-10-30T00-11-21-335Z.json",
        "browser": "browser-2026-10-30T00-28-15-671Z.json",
        "storage": "storage-2026-10-30T00-32-21-360Z.json",
        "task_backup": "task-backup-2026-10-30T00-30-53-585Z.json",
        "submission_lookup": "submission-lookup-2026-10-30T00-27-20-776Z.json",
        "static_build": "static-build-studio-20261030.132.2.json",
        "static_promotion": "static-promotion-1322.json",
        "health": "live-final-health-1322.json",
        "public_current_restart": "public-storage-after-2026-10-30T01-01-52-608Z.json",
        "live_lookup": "live-submission-lookup-1321.json",
    }
    for name in receipts.values():
        assert json.loads((EVIDENCE / name).read_text(encoding="utf-8"))["passed"], name
    runtime = json.loads((BACKEND / "docs/evidence/studio-release-132/linux-20261030.132.2-build.json").read_text())
    notes = {
        "passed": True, "at": datetime.now(UTC).isoformat(),
        "scope": "136 frontend units, type/build, synthetic HTTP with actual browser storage, native PostgreSQL and current public high-port HTTP; no new GPU or physical-phone generation",
        "source_sha256": "3d62cdd587b520be229972660cf2dd79755bcca60571183942534ff4389e5b16",
        "backend_release": "linux-20261030.132.2", "static_release": "studio-20261030.132.2",
        "runtime_commit": runtime["application_source_revision"],
        "passing_package_evidence": receipts,
        "backend_focused": "docs/evidence/studio-release-132/studio-fcd6464d8b7548fabc7126b95e3d5b78.json",
        "backend_compatibility": "docs/evidence/studio-release-132/studio-8eb8130972b846d6bf39443b4a876a71.json",
        "admin_generated_binding_check": "docs/evidence/studio-release-132/admin-generated-types-1322.json",
        "failed_backend_reports_preserved": ["docs/evidence/studio-release-132/studio-c6c6b234938e416291fad2b549b049e7.json", "docs/evidence/studio-release-132/studio-f1b0ae0df9a54a1e9ea3b15129ca0031.json"],
        "failed_public_reports_preserved": ["public-storage-after-2026-10-30T01-00-19-355Z.json"],
        "failure_notes": [
            "The first focused run exposed a test fixture reading the wrong issued-Key field; the fixture was corrected without changing lookup behavior.",
            "The first compatibility run retained one stale evidence index and three existing 30-second native subprocess timeouts during parallel cold build work. The index was refreshed and the same budgets passed on the sequential 1486-case rerun.",
            "The SSH-only Python environment lacks httpx; the public lookup script stopped before creating any Key. The successful run used the existing app Python with the read-only SSH site-packages on PYTHONPATH.",
            "Admin type generation initially hit Redocly's non-ASCII Windows URL handling, then a Vite realpath/root mismatch. An owned verified alias and explicit workspace root fixed the harness. All failed private logs are retained. Only generated API types differ from the passing Python compatibility snapshot; type/build passed and all 45 Admin output files match the verified seed.",
            "The first 132.1 static manifest silently declared database v2 from a report with no version. Actual UI data stayed v3. New 132.2 packaging requires an observed version, uses the passing v3 journal report and preserves all 96 UI file bytes; the failed public assertion and both releases are retained.",
        ],
        "superseded_before_publication": "linux-20261030.132.1 (generated Admin contract alignment)",
        "forward_manifest_correction": "static-manifest-correction-1322.json",
        "recording_scope": "Synthetic HTTP with production UI and native IndexedDB. The manifest scope was corrected before publication; this is not actual GPU or public lost-response evidence.",
        "full_acceptance": "HOLD", "no_new_gpu_submission": True,
        "no_disabled_key_reenabled": True, "temporary_lookup_key_disabled_and_401": True,
        "test_postgres_stopped": True, "production_fsync_and_synchronous_commit": "on",
        "browser_storage_version": 3, "browser_storage_downgrade_supported": False,
        "virtualization_used": False,
    }
    with (EVIDENCE / "submission-lookup-validation-notes-1321.json").open("x", encoding="utf-8") as output:
        output.write(json.dumps(notes, indent=2) + "\n")

    chinese_validation = "当前前端136单元、类型/构建、18组界面、22组存储、25组任务清单和15组只读恢复通过；后端112专项与1,486兼容回归通过，随后仅刷新生成的Admin类型并另验类型/构建，45个Admin文件与已核对种子一致。公网只读Key五项、数据保留/重启三项和四入口健康通过；[当前证据索引](docs/research/studio-implementation/submission-lookup-validation-notes-1321.json)保留失败及修正。"
    english_validation = "Current evidence covers 136 frontend units, type/build, 18 UI groups, 22 storage groups, 25 journal groups and 15 read-only recovery groups; backend scoped/compatibility suites passed 112/1,486 cases. The sole later source change is regenerated Admin types, checked separately with type/build and all 45 emitted files equal to the verified seed. Public read-only Key checks (five), preservation/restart checks (three) and all four health endpoints passed. The [current evidence index](docs/research/studio-implementation/submission-lookup-validation-notes-1321.json) preserves failures and corrections."
    for base in (ROOT, BACKEND):
        update(base / "README.md", [("原提交只读核对（132 候选）", "原提交只读核对（132.2 已部署）")])
        update(base / "README.en.md", [("Read-only submission recovery (132 candidate)", "Read-only submission recovery (132.2 deployed)")])
    update(ROOT / "README.md", [("后端131.1、静态131.9", "后端132.2、静态132.2"), ("原生后端专项82/82、兼容1,456/1,456的运行代码未变。当前前端126单元、类型/构建、18组界面、22组存储与25组任务清单检查通过；公网保留升级和原件SHA恢复另验。", chinese_validation)])
    update(ROOT / "README.en.md", [("backend 131.1/static 131.9", "backend 132.2/static 132.2"), ("Backend runtime is unchanged from the 82 scoped/1,456 compatibility checks. Current frontend verification covers 126 units, type/build, 18 UI groups, 22 storage groups and 25 task-journal groups. Public upgrades and original-SHA restoration are separate evidence.", english_validation)])
    for name, text in (("README.md", chinese_validation), ("README.en.md", english_validation)):
        with (BACKEND / name).open("a", encoding="utf-8") as output:
            output.write("\n" + text.replace("docs/research/", "docs/studio/research/") + "\n")
    update(ROOT / "docs/media/studio-131/README.md", [("132 候选：只读核对原提交", "132.2 已部署：只读核对原提交")])
    update(ROOT / "docs/media/studio-131/README.en.md", [("132 candidate: read-only submission lookup", "132.2 deployed: read-only submission lookup")])
    update(ROOT / "docs/14-原提交只读核对与恢复.md", [("当前候选的 136", "当前已部署版本的 136"), ("全兼容回归与线上发布单独记录", "1,486 项后端兼容回归、Admin 类型/构建、公网只读 Key 五项及浏览器数据保留/重启三项已通过，详见[具名索引](research/studio-implementation/submission-lookup-validation-notes-1321.json)")])
    with (BACKEND / "docs/132-画布原提交只读核对.md").open("a", encoding="utf-8") as output:
        output.write("\n已发布后端 `linux-20261030.132.2` 与静态 `studio-20261030.132.2`。[1,486项兼容回执](evidence/studio-release-132/studio-8eb8130972b846d6bf39443b4a876a71.json)通过；其后仅生成的Admin类型改变，[单独校验](evidence/studio-release-132/admin-generated-types-1322.json)确认类型/构建通过，45个产物与种子一致。249个安装轮文件逐项核对；新生产备份18,620,889字节及归档清单/SHA核对通过。任务41/内容67前后相同，生产持久性保持on，Worker未重启。\n\n[公网只读Key五项](studio/research/studio-implementation/live-submission-lookup-1321.json)、[当前健康](studio/research/studio-implementation/live-final-health-1322.json)和[完整索引](studio/research/studio-implementation/submission-lookup-validation-notes-1321.json)记录实际范围。新临时Key已停用并返回401，未重启用旧Key，未提交GPU生成。首次静态清单错误声明v2，真实UI保持v3；132.2只修正清单且96个UI文件完全一致，失败和旧包保留。完整故障、真机、容量验收仍HOLD。\n")
    update(BACKEND / "CHANGELOG.md", [("Studio 132.1（候选）", "Studio 132.2"), ("保留首轮失败和原始预算，全兼容回归及部署复验中。", "1,486项兼容回归、Admin类型/构建及公网只读Key五项、保留重启三项通过；部署132.2，任务41/内容67不变、Worker未重启。保留首轮失败和原始预算。"), ("compatibility and live deployment are tracked separately.", "112 scoped and 1,486 compatibility cases, Admin type/build, five public protocol checks and three public preservation/restart checks passed. Backend/static 132.2 are deployed; the new temporary Key is disabled and returns 401. A forward package-metadata correction preserved all 96 UI file bytes and actual v3 data.")])

    progress = ROOT / "docs/10-开发进度与交付记录.md"
    rows(progress, {
        "I-03": "| I-03 | 专用业务客户端、动态模型发现、隔离 Worker 中的 Schema 2020-12 | 当前136项单元检查通过；11个历史模型描述不代表当前设备在线 |",
        "I-14": "| I-14 | Key 授权规格禁用、旧工具栏统一创作入口 | 当前单元136/136通过；无新增独立产品限额 |",
        "I-34": "| I-34 | 132专项、兼容与页面回归 | 136单元/类型/构建、112原生专项、1,486兼容、Admin类型/构建、18页面、22存储、25清单及15只读恢复通过；非完整GPU故障矩阵 |",
        "F-02": "| F-02 | 类型与构建 | 同一冻结源码136单元、类型/构建，112/1,486原生后端通过；生成Admin类型单独复验 | 后续代码变更继续回归；不以静态检查替代完整可用性验收 |",
        "F-03": "| F-03 | 原生轻量部署 | 静态132.2为96文件，4,185,372原始字节/1,357,230字节归档 | 首屏、实际生成延迟与吞吐尚待量测；部署不新增常驻后端 |",
        "F-04": "| F-04 | 中转连接 | 规范源站、Key和连接检测；线上jobs:read仅核对/禁止生成通过 | 更多权限、限额及界面禁用组合另验 |",
        "F-17": "| F-17 | 未知提交核对 | 原请求只读核对及跨页首次发送实现；112原生专项、15浏览器专项、公网只读Key五项通过 | 真实GPU响应丢失、长离线/后台完整恢复矩阵仍待验；未查到不补发 |",
        "F-44": "| F-44 | 发布回滚 | 历史后端/静态往返及当前132.2兼容v3向前发布通过，任务/内容不变、Worker未重启 | v3不能回退旧恢复逻辑；更多兼容恢复故障矩阵另验 |",
    })
    text = progress.read_text(encoding="utf-8")
    marker = "\n## 当前具名证据"
    assert marker in text and "| I-36 |" not in text
    text = text.replace(marker, "\n| I-36 | 原生后端/静态132.2已发布及公网复验 | 新备份18,620,889字节；249轮文件、四入口/任意源、只读Key五项和保留重启三项通过；41任务/67内容不变、原Worker未重启；首次错误清单及失败保留 |\n" + marker, 1)
    text = text.replace("当前250个来源文件", "来源文件逐项摘要见交付清单")
    text = text.replace("最终记录保存在中转交付仓库docs/evidence/studio-release-131。未修改的后端运行代码沿用已通过的82/1,456回归，本轮任务清单修复不重复计为新一轮GPU或后端全量运行。", "历史记录保存在中转交付仓库docs/evidence/studio-release-131，本轮追加studio-release-132。当前后端112/1,486已运行通过；随后仅刷新生成Admin类型并单独完成类型/构建校验，不计为新GPU实测。")
    old_deploy = "后端linux-20261030.131.1的248个安装轮文件逐项匹配源码；当前静态studio-20261030.131.9为96文件、4,183,137原始字节、1,355,716字节归档，包SHA为ffcb3a911bea5019b104be0e744fd7607dbd8fb6b586d39ca6b25a344c47bc07。"
    assert old_deploy in text
    text = text.replace(old_deploy, "后端linux-20261030.132.2的249个安装轮文件逐项匹配源码97459bc3ce83551bfec8c0ee65506a7eb02e60b9；当前静态studio-20261030.132.2为96文件、4,185,372原始字节、1,357,230字节归档，包SHA为f5d792f9c75587c41e3308681ca40308d971a89abd59a93a5775f6d0100d0739。")
    progress.write_text(text, encoding="utf-8")
    current_rows = {
        "当前前端": "| 当前前端 | 136/136、类型与生产构建通过 | 同源摘要3d62cdd587b520be229972660cf2dd79755bcca60571183942534ff4389e5b16；[当前索引](research/studio-implementation/submission-lookup-validation-notes-1321.json) |",
        "合成 HTTP 浏览器": "| 合成 HTTP 浏览器 | 18组页面、22组存储、25组清单和15组只读核对通过 | 实际生产96文件摘要、真实IndexedDB与桌面/390px；不是GPU或物理手机 |",
        "原生浏览器存储": "| 原生浏览器存储 | 22组通过，真实IndexedDB及两个页面 | [当前回执](research/studio-implementation/storage-2026-10-30T00-32-21-360Z.json)；事务、配额故障注入，不是磁盘真实耗尽 |",
        "任务清单专项": "| 任务清单专项 | 25组通过；新15组只读核对补充无ID找回 | [清单](research/studio-implementation/task-backup-2026-10-30T00-30-53-585Z.json)、[只读恢复](research/studio-implementation/submission-lookup-2026-10-30T00-27-20-776Z.json)；无额外生成/上传/取消 |",
        "公网浏览器升级": "| 公网浏览器升级 | 历史v2→v3保留；132.2继续保留/重启/深链三项通过 | [当前回执](research/studio-implementation/public-storage-after-2026-10-30T01-01-52-608Z.json)；原JSON、两份PNG摘要和预算保留，未使用Key或新建任务 |",
        "原生后端": "| 原生后端 | 专项112/112、兼容1,486/1,486 | 后续唯一生成Admin类型变更单独类型/构建通过；45产物等同种子；非崩溃持久性测试 |",
        "生产部署": "| 生产部署 | 当前后端132.2/静态132.2 | [部署回执](research/studio-implementation/production-deployment-1322.json)、[当前健康](research/studio-implementation/live-final-health-1322.json)；249轮文件核对，41任务/67内容不变，无迁移/Worker重启 |",
        "生产备份": "| 生产备份 | 本次新18,620,889字节归档清单与重新SHA通过 | [发布前回执](research/studio-implementation/production-deployment-1322.json)；不等于完整灾难恢复 |",
        "临时Key收尾": "| 临时Key收尾 | 历史两把及本次新只读Key均已停用/401 | [本次回执](research/studio-implementation/live-submission-lookup-1321.json)；未重启用旧Key，未停用用户Key |",
        "最终在线状态": "| 最终在线状态 | 四入口200、四服务active、两任意源预检通过 | [只读复查](research/studio-implementation/live-final-health-1322.json)；当前132.2/132.2，原Worker不变 |",
    }
    rows(progress, current_rows)

    operations = ROOT / "docs/11-线上使用与原生发布运维.md"
    update(operations, [("静态使用 `static-upgrade-1321`", "静态使用 `static-upgrade-1322`（132.1清单修正后继续向前发布）"), ("131.9的浏览器数据库为v3", "当前132.2的浏览器数据库为v3"), ("static-build-studio-20261030.131.9.json", "static-build-studio-20261030.132.2.json"), ("live-final-health-1319.json", "live-final-health-1322.json")])
    rows(operations, {
        "后端": "| 后端 | linux-20261030.132.2 | 源码97459bc3ce83551bfec8c0ee65506a7eb02e60b9，249安装轮文件逐项匹配；没有数据库迁移 |",
        "静态画布": "| 静态画布 | studio-20261030.132.2 | 96文件，4,185,372原始字节，1,357,230归档字节；归档SHA f5d792f9c75587c41e3308681ca40308d971a89abd59a93a5775f6d0100d0739 |",
        "前端输入": "| 前端输入 | 3d62cdd587b520be229972660cf2dd79755bcca60571183942534ff4389e5b16 | 136单元/类型/构建、18界面、22存储、25清单和15只读核对通过；非完整GPU签收 |",
        "当前静态升级": "| 当前静态升级 | 131.9→132.1→132.2，向前切换 | 132.1实际数据保持v3，但清单误声明v2；132.2修正清单，96个UI文件一致，保留失败和两包 |",
        "生产持久性": "| 生产持久性 | PostgreSQL原生服务 | fsync/synchronous_commit保持on；本次新备份18,620,889字节，清单与再次摘要通过，非完整灾难恢复 |",
    })
    index = ROOT / "docs/README.md"
    with index.open("a", encoding="utf-8") as output:
        output.write("\n- [原提交只读核对、无ID恢复和首次发送保护](14-原提交只读核对与恢复.md) · [132.2当前证据](research/studio-implementation/submission-lookup-validation-notes-1321.json)\n")
    for name, text in (("README.md", "\n[132 · 原提交只读核对](132-画布原提交只读核对.md)：当前后端/静态132.2，原生112/1,486及公网协议已验；完整签收仍HOLD。\n"), ("README.en.md", "\n[132 · Exact read-only submission lookup](132-画布原提交只读核对.md): backend/static132.2 are deployed with native112/1,486 and public protocol evidence. Full acceptance remains HOLD.\n")):
        with (BACKEND / "docs" / name).open("a", encoding="utf-8") as output:
            output.write(text)
    print(json.dumps({"passed": True, "notes_written": True, "current_release": "132.2", "full_acceptance": "HOLD"}))
