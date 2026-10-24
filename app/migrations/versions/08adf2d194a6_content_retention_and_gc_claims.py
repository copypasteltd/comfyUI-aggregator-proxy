"""content_retention_and_gc_claims"""
import sqlalchemy as sa
from alembic import op

revision = '08adf2d194a6'
down_revision = '91ab23915f5f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(op.f('uq_assets_tenant_id_project_id_key_id_id'), 'assets', ['tenant_id', 'project_id', 'key_id', 'id'])
    op.create_table('job_asset_references',
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('asset_id', sa.Uuid(), nullable=False),
    sa.Column('key_id', sa.Uuid(), nullable=False),
    sa.Column('tenant_id', sa.Uuid(), nullable=False),
    sa.Column('project_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_job_asset_references_asset_id_assets')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_asset_references_job_id_jobs')),
    sa.ForeignKeyConstraint(['key_id'], ['api_keys.id'], name=op.f('fk_job_asset_references_key_id_api_keys')),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id', 'key_id', 'asset_id'], ['assets.tenant_id', 'assets.project_id', 'assets.key_id', 'assets.id'], name=op.f('fk_job_asset_references_tenant_id_project_id_key_id_asset_id_assets')),
    sa.ForeignKeyConstraint(['tenant_id', 'project_id', 'key_id', 'job_id'], ['jobs.tenant_id', 'jobs.project_id', 'jobs.key_id', 'jobs.id'], name=op.f('fk_job_asset_references_tenant_id_project_id_key_id_job_id_jobs')),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_job_asset_references_tenant_id_tenants')),
    sa.PrimaryKeyConstraint('job_id', 'asset_id', name=op.f('pk_job_asset_references'))
    )
    op.create_index('ix_job_asset_references_asset', 'job_asset_references', ['asset_id'], unique=False)
    op.create_index(op.f('ix_job_asset_references_project_id'), 'job_asset_references', ['project_id'], unique=False)
    op.create_index(op.f('ix_job_asset_references_tenant_id'), 'job_asset_references', ['tenant_id'], unique=False)
    op.add_column('object_records', sa.Column('delete_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('object_records', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('object_records', sa.Column('gc_claim_token', sa.Uuid(), nullable=True))
    op.add_column('object_records', sa.Column('gc_claim_until', sa.DateTime(timezone=True), nullable=True))
    connection = op.get_bind()
    invalid = connection.scalar(sa.text("""
        SELECT count(*) FROM jobs j
        CROSS JOIN LATERAL jsonb_object_keys(COALESCE(j.execution->'asset_hashes', '{}'::jsonb)) k(identifier)
        LEFT JOIN assets a ON a.id = CASE WHEN k.identifier ~ '^asset_[0-9a-f]{32}$'
            THEN substring(k.identifier from 7)::uuid END
            AND a.tenant_id=j.tenant_id AND a.project_id=j.project_id AND a.key_id=j.key_id
        WHERE a.id IS NULL
    """))
    if invalid:
        raise RuntimeError("Frozen input references need reconciliation before content migration")
    op.execute(sa.text("""
        INSERT INTO job_asset_references (job_id, asset_id, key_id, tenant_id, project_id)
        SELECT j.id, substring(k.identifier from 7)::uuid, j.key_id, j.tenant_id, j.project_id
        FROM jobs j CROSS JOIN LATERAL jsonb_object_keys(COALESCE(j.execution->'asset_hashes', '{}'::jsonb)) k(identifier)
    """))
    op.execute(sa.text("""
        UPDATE object_records o SET expires_at=GREATEST(o.expires_at, r.retain_until)
        FROM (SELECT a.object_id, max(j.terminal_at + interval '24 hours') retain_until
              FROM job_asset_references r JOIN assets a ON a.id=r.asset_id
              JOIN jobs j ON j.id=r.job_id WHERE j.terminal_at IS NOT NULL
              GROUP BY a.object_id) r WHERE o.id=r.object_id
    """))


def downgrade() -> None:
    op.drop_column('object_records', 'gc_claim_until')
    op.drop_column('object_records', 'gc_claim_token')
    op.drop_column('object_records', 'deleted_at')
    op.drop_column('object_records', 'delete_requested_at')
    op.drop_index(op.f('ix_job_asset_references_tenant_id'), table_name='job_asset_references')
    op.drop_index(op.f('ix_job_asset_references_project_id'), table_name='job_asset_references')
    op.drop_index('ix_job_asset_references_asset', table_name='job_asset_references')
    op.drop_table('job_asset_references')
    op.drop_constraint(op.f('uq_assets_tenant_id_project_id_key_id_id'), 'assets', type_='unique')
