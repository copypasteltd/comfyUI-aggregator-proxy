"""Allow fixed model inspection and persist the server receipt time."""
import sqlalchemy as sa
from alembic import op

revision = "e6215c902f30"
down_revision = "c4120e20d721"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("worker_commands", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f("ck_worker_commands_allowlisted_command"), "worker_commands", type_="check")
    op.create_check_constraint("allowlisted_command", "worker_commands", "command IN ('probe_channel','scan_inventory','pull_workflow','apply_workflow_release','apply_channel_config','collect_diagnostics','inspect_model_file')")


def downgrade() -> None:
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM worker_commands WHERE command='inspect_model_file') THEN RAISE EXCEPTION 'Model inspection records require forward recovery'; END IF; END $$")
    op.drop_constraint(op.f("ck_worker_commands_allowlisted_command"), "worker_commands", type_="check")
    op.create_check_constraint("allowlisted_command", "worker_commands", "command IN ('probe_channel','scan_inventory','pull_workflow','apply_workflow_release','apply_channel_config','collect_diagnostics')")
    op.drop_column("worker_commands", "completed_at")
