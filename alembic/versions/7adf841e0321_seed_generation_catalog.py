"""Seed the generation catalog.

Revision ID: 7adf841e0321
Revises: 482bd3997556
"""

from alembic import op

revision = "7adf841e0321"
down_revision = "482bd3997556"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO models (slug, price) VALUES ('image-fast', 2), ('video-long', 25)"
    )
    op.execute("""
        INSERT INTO legs (
            model_slug, provider, priority, max_ref_images, skip_on_moderation
        )
        VALUES
          ('image-fast', 'alpha', 1, 4, false),
          ('image-fast', 'beta', 2, 10, false),
          ('video-long', 'beta', 1, 2, false),
          ('video-long', 'alpha', 2, 1, true)
    """)


def downgrade() -> None:
    op.execute("DELETE FROM legs WHERE model_slug IN ('image-fast', 'video-long')")
    op.execute("DELETE FROM models WHERE slug IN ('image-fast', 'video-long')")
