"""Saved period work reports (Development > Reports).

One row per Build. Refresh reads the newest row for a period + project selection;
Build writes a new row. The collated snapshot the analysis was based on is kept
beside the analysis so a report can be re-read without re-running the model.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from . import Base
from .time import utc_now_naive


class WorkReport(Base):
    __tablename__ = "work_reports"
    __table_args__ = (
        Index("ix_work_reports_period_projects", "period_key", "project_key", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    period_key = Column(String, nullable=False)  # "YYYY-MM-DD_YYYY-MM-DD" (Africa/Johannesburg dates)
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    project_key = Column(String, nullable=False, default="all")  # "all" or sorted ids, e.g. "2,9"
    provider = Column(String, nullable=False, default="")
    model = Column(String, nullable=False, default="")
    status = Column(String, nullable=False, default="complete")
    analysis = Column(Text, nullable=False, default="{}")  # JSON from the model
    snapshot = Column(Text, nullable=False, default="{}")  # JSON collated totals the analysis used
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
