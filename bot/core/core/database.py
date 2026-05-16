from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, DateTime, Float, Integer, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NodeStatus(Base):
    __tablename__ = "node_status"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_name: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16))
    uptime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class EarningsRecord(Base):
    __tablename__ = "earnings_record"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_name: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(16))
    usd_value: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class DailyReport(Base):
    __tablename__ = "daily_report"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    grass_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    rivalz_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    dawn_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    gradient_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    teneo_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    openloop_earnings: Mapped[float] = mapped_column(Float, default=0.0)
    total_usd: Mapped[float] = mapped_column(Float, default=0.0)
    uptime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlertLog(Base):
    __tablename__ = "alert_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(16))
    node_name: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Database:
    def __init__(self, db_url: str = "sqlite+aiosqlite:///data/depin_bot.db") -> None:
        self.engine = create_async_engine(db_url, future=True)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def save_status(self, node: str, status: str, uptime_hours: float) -> None:
        async with self.session_factory() as session:
            session.add(NodeStatus(node_name=node, status=status, uptime_hours=uptime_hours))
            await session.commit()

    async def save_earnings(self, node: str, amount: float, unit: str, usd: float) -> None:
        async with self.session_factory() as session:
            session.add(EarningsRecord(node_name=node, amount=amount, unit=unit, usd_value=usd))
            await session.commit()

    async def get_today_earnings(self) -> dict[str, float]:
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.session_factory() as session:
            stmt = select(EarningsRecord.node_name, func.sum(EarningsRecord.usd_value)).where(
                EarningsRecord.recorded_at >= start
            ).group_by(EarningsRecord.node_name)
            rows = (await session.execute(stmt)).all()
        return {name: float(total or 0.0) for name, total in rows}

    async def get_today_reward_totals(self) -> dict[str, dict[str, Any]]:
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        async with self.session_factory() as session:
            stmt = (
                select(EarningsRecord.node_name, EarningsRecord.unit, func.max(EarningsRecord.amount))
                .where(EarningsRecord.recorded_at >= start)
                .group_by(EarningsRecord.node_name, EarningsRecord.unit)
            )
            rows = (await session.execute(stmt)).all()
        out: dict[str, dict[str, Any]] = {}
        for node_name, unit, amount in rows:
            node_name = str(node_name)
            amount = float(amount or 0.0)
            if node_name not in out or amount > float(out[node_name].get("amount", 0.0)):
                out[node_name] = {"amount": amount, "unit": str(unit)}
        return out

    async def get_weekly_summary(self) -> dict[str, float]:
        start = datetime.utcnow() - timedelta(days=7)
        async with self.session_factory() as session:
            stmt = select(func.sum(EarningsRecord.usd_value)).where(EarningsRecord.recorded_at >= start)
            total = (await session.execute(stmt)).scalar()
        return {"weekly_total_usd": float(total or 0.0)}

    async def get_monthly_total(self) -> float:
        start = datetime.utcnow() - timedelta(days=30)
        async with self.session_factory() as session:
            stmt = select(func.sum(EarningsRecord.usd_value)).where(EarningsRecord.recorded_at >= start)
            total = (await session.execute(stmt)).scalar()
        return float(total or 0.0)

    async def save_alert(self, alert_type: str, node_name: str, message: str) -> None:
        async with self.session_factory() as session:
            session.add(AlertLog(alert_type=alert_type, node_name=node_name, message=message))
            await session.commit()

    async def close(self) -> None:
        await self.engine.dispose()
