"""gateway_admission_and_settlement"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '91ab23915f5f'
down_revision = 'ef48934b7180'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(op.f('uq_jobs_tenant_id_project_id_key_id_id'), 'jobs', ['tenant_id', 'project_id', 'key_id', 'id'])
    op.create_table('gateway_services',
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('issuer', sa.String(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('allowed_cidrs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('credential_identifier', sa.String(length=32), nullable=False),
    sa.Column('credential_verifier', sa.String(length=64), nullable=False),
    sa.Column('pepper_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gateway_services')),
    sa.UniqueConstraint('credential_identifier', name=op.f('uq_gateway_services_credential_identifier'))
    )
    op.create_table('external_identity_bindings',
    sa.Column('gateway_id', sa.Uuid(), nullable=False),
    sa.Column('key_id', sa.Uuid(), nullable=False),
    sa.Column('external_tenant_ref', sa.String(length=128), nullable=False),
    sa.Column('external_key_ref', sa.String(length=128), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['gateway_id'], ['gateway_services.id'], name=op.f('fk_external_identity_bindings_gateway_id_gateway_services')),
    sa.ForeignKeyConstraint(['key_id'], ['api_keys.id'], name=op.f('fk_external_identity_bindings_key_id_api_keys')),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id', 'key_id'], ['api_keys.tenant_id', 'api_keys.project_id', 'api_keys.id'], name=op.f('fk_external_identity_bindings_tenant_id_project_id_key_id_api_keys')),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'], name=op.f('fk_external_identity_bindings_tenant_id_project_id_projects')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_external_identity_bindings_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_identity_bindings')),
    sa.UniqueConstraint('gateway_id', 'external_tenant_ref', 'external_key_ref', name=op.f('uq_external_identity_bindings_gateway_id_external_tenant_ref_external_key_ref')),
    sa.UniqueConstraint('gateway_id', 'tenant_id', 'project_id', 'key_id', 'id', name=op.f('uq_external_identity_bindings_gateway_id_tenant_id_project_id_key_id_id')),
    sa.UniqueConstraint('key_id', name=op.f('uq_external_identity_bindings_key_id')),
    sa.UniqueConstraint('tenant_id', 'project_id', 'id', name=op.f('uq_external_identity_bindings_tenant_id_project_id_id'))
    )
    op.create_index(op.f('ix_external_identity_bindings_project_id'), 'external_identity_bindings', ['project_id'], unique=False)
    op.create_index(op.f('ix_external_identity_bindings_tenant_id'), 'external_identity_bindings', ['tenant_id'], unique=False)
    op.create_table('gateway_admissions',
    sa.Column('gateway_id', sa.Uuid(), nullable=False),
    sa.Column('binding_id', sa.Uuid(), nullable=False),
    sa.Column('key_id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('provider_request_id', sa.String(length=128), nullable=False),
    sa.Column('external_task_ref', sa.String(length=128), nullable=False),
    sa.Column('intent_digest', sa.String(length=64), nullable=False),
    sa.Column('price_version', sa.String(length=64), nullable=False),
    sa.Column('authorization_ref', sa.String(length=128), nullable=False),
    sa.Column('key_revision', sa.Integer(), nullable=False),
    sa.Column('binding_revision', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.CheckConstraint("state IN ('pending','confirmed','aborted','expired','rejected')", name=op.f('ck_gateway_admissions_state')),
    sa.ForeignKeyConstraint(['binding_id'], ['external_identity_bindings.id'], name=op.f('fk_gateway_admissions_binding_id_external_identity_bindings')),
    sa.ForeignKeyConstraint(['gateway_id', 'tenant_id', 'project_id', 'key_id', 'binding_id'], ['external_identity_bindings.gateway_id', 'external_identity_bindings.tenant_id', 'external_identity_bindings.project_id', 'external_identity_bindings.key_id', 'external_identity_bindings.id'], name=op.f('fk_gateway_admissions_gateway_id_tenant_id_project_id_key_id_binding_id_external_identity_bindings')),
    sa.ForeignKeyConstraint(['gateway_id'], ['gateway_services.id'], name=op.f('fk_gateway_admissions_gateway_id_gateway_services')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_gateway_admissions_job_id_jobs')),
    sa.ForeignKeyConstraint(['key_id'], ['api_keys.id'], name=op.f('fk_gateway_admissions_key_id_api_keys')),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id', 'key_id', 'job_id'], ['jobs.tenant_id', 'jobs.project_id', 'jobs.key_id', 'jobs.id'], name=op.f('fk_gateway_admissions_tenant_id_project_id_key_id_job_id_jobs')),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id'], ['projects.tenant_id', 'projects.id'], name=op.f('fk_gateway_admissions_tenant_id_project_id_projects')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_gateway_admissions_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gateway_admissions')),
    sa.UniqueConstraint('gateway_id', 'external_task_ref', name=op.f('uq_gateway_admissions_gateway_id_external_task_ref')),
    sa.UniqueConstraint('gateway_id', 'provider_request_id', name=op.f('uq_gateway_admissions_gateway_id_provider_request_id')),
    sa.UniqueConstraint('job_id', name=op.f('uq_gateway_admissions_job_id')),
    sa.UniqueConstraint('tenant_id', 'project_id', 'id', name=op.f('uq_gateway_admissions_tenant_id_project_id_id'))
    )
    op.create_index(op.f('ix_gateway_admissions_project_id'), 'gateway_admissions', ['project_id'], unique=False)
    op.create_index(op.f('ix_gateway_admissions_tenant_id'), 'gateway_admissions', ['tenant_id'], unique=False)
    op.add_column('jobs', sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE jobs SET accepted_at = created_at WHERE state <> 'admission_pending'")
    op.create_index('uq_gateway_settlement_job', 'outbox_events', ['aggregate_id'], unique=True, postgresql_where=sa.text("kind = 'gateway.settlement'"))


def downgrade() -> None:
    op.drop_index('uq_gateway_settlement_job', table_name='outbox_events', postgresql_where=sa.text("kind = 'gateway.settlement'"))
    op.drop_column('jobs', 'accepted_at')
    op.drop_index(op.f('ix_gateway_admissions_tenant_id'), table_name='gateway_admissions')
    op.drop_index(op.f('ix_gateway_admissions_project_id'), table_name='gateway_admissions')
    op.drop_table('gateway_admissions')
    op.drop_constraint(op.f('uq_jobs_tenant_id_project_id_key_id_id'), 'jobs', type_='unique')
    op.drop_index(op.f('ix_external_identity_bindings_tenant_id'), table_name='external_identity_bindings')
    op.drop_index(op.f('ix_external_identity_bindings_project_id'), table_name='external_identity_bindings')
    op.drop_table('external_identity_bindings')
    op.drop_table('gateway_services')
