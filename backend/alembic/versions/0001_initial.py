"""Initial schema — patients table

Revision ID: 0001_initial
Revises:
Create Date: 2025-08-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("age", sa.Text(), nullable=True),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("chief_complaint", sa.Text(), nullable=True),
        sa.Column("symptoms", sa.Text(), nullable=True),
        sa.Column("temp", sa.Text(), nullable=True),
        sa.Column("bp", sa.Text(), nullable=True),
        sa.Column("pulse", sa.Text(), nullable=True),
        sa.Column("spo2", sa.Text(), nullable=True),
        sa.Column("medical_history", sa.Text(), nullable=True),
        sa.Column("family_history", sa.Text(), nullable=True),
        sa.Column("allergies", sa.Text(), nullable=True),
        sa.Column("tentative_doctor_diagnosis", sa.Text(), nullable=True),
        sa.Column("initial_llm_diagnosis", sa.Text(), nullable=True),
        sa.Column("medications", sa.Text(), nullable=True),
        sa.Column("transcript_summary", sa.Text(), nullable=True),
        sa.Column("ration_card_type", sa.Text(), nullable=True),
        sa.Column("income_bracket", sa.Text(), nullable=True),
        sa.Column("occupation", sa.Text(), nullable=True),
        sa.Column("caste_category", sa.Text(), nullable=True),
        sa.Column("housing_type", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("scheme_eligibility", sa.Text(), nullable=True),
        sa.Column("procedures", sa.Text(), nullable=True),
        sa.Column("icd10_codes", sa.Text(), nullable=True),
        sa.Column("procedure_codes", sa.Text(), nullable=True),
        sa.Column("billing_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.current_timestamp()),
    )


def downgrade() -> None:
    op.drop_table("patients")
