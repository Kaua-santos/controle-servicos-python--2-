"""Modelos (tabelas) do sistema de Controle de Serviços."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database import Base


class Employee(Base):
    """Funcionário cadastrado."""

    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    salary = Column(Float, nullable=False, default=0)
    hire_date = Column(Date, nullable=True)
    active = Column(Boolean, nullable=False, default=True)

    expenses = relationship("Expense", back_populates="employee")
    absences = relationship(
        "Absence", back_populates="employee", cascade="all, delete-orphan"
    )
    advances = relationship(
        "Advance", back_populates="employee", cascade="all, delete-orphan"
    )
    payments = relationship(
        "Payment", back_populates="employee", cascade="all, delete-orphan"
    )


class Expense(Base):
    """Gasto/despesa da operação."""

    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, index=True)
    description = Column(String, nullable=False)
    category = Column(String, nullable=False, default="Geral")
    amount = Column(Float, nullable=False, default=0)
    date = Column(Date, nullable=False)
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )

    employee = relationship("Employee", back_populates="expenses")
    payments = relationship(
        "Payment", back_populates="expense", cascade="all, delete-orphan"
    )


class Payment(Base):
    """Pagamento mensal feito a um funcionário, separado de adiantamentos."""

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    expense_id = Column(
        Integer, ForeignKey("expenses.id", ondelete="CASCADE"), nullable=True
    )
    employee_id = Column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=True)
    amount = Column(Float, nullable=False, default=0)
    date = Column(Date, nullable=False)
    description = Column(String, nullable=True)

    expense = relationship("Expense", back_populates="payments")
    employee = relationship("Employee", back_populates="payments")


class Absence(Base):
    """Falta de um funcionário."""

    __tablename__ = "absences"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    date = Column(Date, nullable=False)
    days = Column(Integer, nullable=False, default=1)
    reason = Column(Text, nullable=False)
    justified = Column(Boolean, nullable=False, default=False)

    employee = relationship("Employee", back_populates="absences")


class Advance(Base):
    """Adiantamento de pagamento para um funcionário."""

    __tablename__ = "advances"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    amount = Column(Float, nullable=False, default=0)
    date = Column(Date, nullable=False)
    description = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")  # open | settled

    employee = relationship("Employee", back_populates="advances")


class Note(Base):
    """Anotação / lembrete da operação."""

    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MonthlySummary(Base):
    """Fechamento persistido do custo total de um mês."""

    __tablename__ = "monthly_summaries"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    total_cost = Column(Float, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
