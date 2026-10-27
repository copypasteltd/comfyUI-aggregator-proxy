from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from generation_application.ports import AdminIdentity
from generation_contracts.catalog import WorkflowSync
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint, parse_id, public_id
from generation_infrastructure.admin_catalog import CatalogAdministration
from generation_infrastructure.commands import CommandRepository
from generation_infrastructure.models import AdminUser, WorkflowBinding, WorkflowRelease
from generation_infrastructure.security import SecretVault
from generation_infrastructure.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('mode,receipt', [('api','api'), ('api','native'), ('api','wrong_digest'), ('native','api'), ('native','native')])
def test_sync_receipt_cannot_cross_dependency_modes(pg: sessionmaker[Session], catalog: dict[str, Any], test_settings: Settings, mode: str, receipt: str) -> None:
    with pg.begin() as session:
        user = AdminUser(username='http-sync-admin', password_hash='!', role='administrator')
        session.add(user)
        session.flush()
        actor = AdminIdentity(user.id, user.username, user.role, uuid4())
        release = session.get(WorkflowRelease, catalog['release_id'])
        manifest = dict(release.manifest)
        manifest.setdefault('dependency_lock', {'comfy_commit':'a'*40,'models':[],'custom_nodes':[]})
        if mode == 'api':
            manifest['dependency_lock'] = {'mode':'comfy-api-only-v1','comfy_version':'synthetic-1','node_classes':sorted({n['class_type'] for n in manifest['workflow_api'].values()}),'model_inputs':[]}
        candidate = WorkflowRelease(tenant_id=release.tenant_id, project_id=release.project_id, workflow_id=release.workflow_id, version=2, digest=fingerprint(manifest), manifest=manifest, validated=True, validation={'test_only':True})
        session.add(candidate)
        session.flush()
        catalog.update(release_id=candidate.id, release_digest=candidate.digest)
    payload = WorkflowSync.model_validate({'direction':'push','release_id':public_id('wfr', catalog['release_id']),'release_digest':catalog['release_digest'],'targets':[{'channel_id':public_id('ch',catalog['channel_ids'][0]),'expected_revision':1}]})
    CatalogAdministration(pg,test_settings).synchronize(actor,payload,'http-sync-case','test')
    commands = CommandRepository(pg,SecretVault(test_settings.encryption_key.get_secret_value()))
    command = commands.claim(catalog['node_id'],catalog['boot_id'])
    result = {'success':True,'applied_revision':1,'release_digest':catalog['release_digest'],'environment_verified':receipt=='native'}
    if receipt != 'native':
        result.update(api_profile_verified=True, environment_digest='0'*64 if receipt=='wrong_digest' else fingerprint({'mode':'comfy-api-only-v1','dependency_lock':manifest['dependency_lock']}))
    if mode == receipt:
        commands.receipt(catalog['node_id'],parse_id('cmd',command['command_id']),catalog['boot_id'],command['command_digest'],result)
    else:
        with pytest.raises(DomainError, match='Workflow'):
            commands.receipt(catalog['node_id'],parse_id('cmd',command['command_id']),catalog['boot_id'],command['command_digest'],result)
    with pg() as session:
        binding = session.scalar(select(WorkflowBinding).where(WorkflowBinding.channel_id==catalog['channel_ids'][0], WorkflowBinding.release_id==catalog['release_id']))
        assert (binding.verified_digest == catalog['release_digest']) is (mode == receipt)
