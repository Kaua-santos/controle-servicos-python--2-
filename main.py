"""Controle de Serviços — aplicação FastAPI + Jinja2 + SQLite.

Sistema de controle de serviços com cadastro de funcionários,
controle de salários, gastos, faltas, adiantamentos e anotações.
"""

from datetime import date, datetime

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from models import Absence, Advance, Employee, Expense, Note

# Cria as tabelas no banco (se ainda não existirem)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Controle de Serviços")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def format_money(value) -> str:
    value = value or 0
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_date(value) -> str:
    if not value:
        return "Não informado"
    return value.strftime("%d/%m/%Y")


templates.env.filters["money"] = format_money
templates.env.filters["data"] = format_date

CATEGORIES = ["Materiais", "Transporte", "Ferramentas", "Alimentação", "Administrativo", "Outros"]
MONTHS = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_date(value: str | None) -> date | None:
    """Converte string 'YYYY-MM-DD' vinda de formulário HTML em date."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def get_session() -> Session:
    return SessionLocal()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/")
def dashboard(request: Request, month: int | None = None, year: int | None = None):
    db = get_session()
    try:
        today = date.today()
        month = month or today.month
        year = year or today.year

        employees = db.query(Employee).all()
        active_employees = [e for e in employees if e.active]
        monthly_payroll = sum(e.salary for e in active_employees)

        month_expenses = (
            db.query(Expense)
            .filter(extract("month", Expense.date) == month, extract("year", Expense.date) == year)
            .all()
        )
        monthly_expenses_total = sum(e.amount for e in month_expenses)

        month_advances = (
            db.query(Advance)
            .filter(extract("month", Advance.date) == month, extract("year", Advance.date) == year)
            .all()
        )
        monthly_advances_total = sum(a.amount for a in month_advances)

        open_absence_days = sum(a.days for a in db.query(Absence).filter(Absence.justified.is_(False)).all())
        open_advances_count = db.query(Advance).filter(Advance.status == "open").count()

        recent_expenses = db.query(Expense).order_by(Expense.date.desc()).limit(5).all()

        summary = {
            "month": month,
            "year": year,
            "total_employees": len(employees),
            "active_employees": len(active_employees),
            "monthly_payroll": monthly_payroll,
            "monthly_expenses": monthly_expenses_total,
            "monthly_total": monthly_payroll + monthly_expenses_total,
            "monthly_advances": monthly_advances_total,
            "open_absences": open_absence_days,
            "open_advances": open_advances_count,
            "notes_count": db.query(Note).count(),
        }

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "page": "dashboard",
                "summary": summary,
                "recent_expenses": recent_expenses,
                "months": MONTHS,
                "years": [year - 1, year, year + 1],
                "today": today,
            },
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Funcionários
# ---------------------------------------------------------------------------

@app.get("/funcionarios")
def list_employees(request: Request, status: str = "all", q: str = ""):
    db = get_session()
    try:
        query = db.query(Employee)
        if status == "active":
            query = query.filter(Employee.active.is_(True))
        elif status == "inactive":
            query = query.filter(Employee.active.is_(False))
        employees = query.order_by(Employee.name).all()

        if q:
            needle = q.lower()
            employees = [
                e for e in employees
                if needle in e.name.lower() or needle in e.role.lower()
            ]

        return templates.TemplateResponse(
            "funcionarios.html",
            {
                "request": request,
                "page": "funcionarios",
                "employees": employees,
                "status": status,
                "q": q,
            },
        )
    finally:
        db.close()


@app.post("/funcionarios/novo")
def create_employee(
    name: str = Form(...),
    role: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    salary: float = Form(0),
    hire_date: str = Form(""),
    active: str | None = Form(None),
):
    db = get_session()
    try:
        employee = Employee(
            name=name.strip(),
            role=role.strip(),
            phone=phone or None,
            email=email or None,
            salary=salary,
            hire_date=parse_date(hire_date),
            active=bool(active),
        )
        db.add(employee)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/funcionarios", status_code=303)


@app.post("/funcionarios/{employee_id}/editar")
def update_employee(
    employee_id: int,
    name: str = Form(...),
    role: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    salary: float = Form(0),
    hire_date: str = Form(""),
    active: str | None = Form(None),
):
    db = get_session()
    try:
        employee = db.get(Employee, employee_id)
        if employee:
            employee.name = name.strip()
            employee.role = role.strip()
            employee.phone = phone or None
            employee.email = email or None
            employee.salary = salary
            employee.hire_date = parse_date(hire_date)
            employee.active = bool(active)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/funcionarios", status_code=303)


@app.post("/funcionarios/{employee_id}/toggle")
def toggle_employee(employee_id: int):
    db = get_session()
    try:
        employee = db.get(Employee, employee_id)
        if employee:
            employee.active = not employee.active
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/funcionarios", status_code=303)


@app.post("/funcionarios/{employee_id}/excluir")
def delete_employee(employee_id: int):
    db = get_session()
    try:
        employee = db.get(Employee, employee_id)
        if employee:
            db.delete(employee)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/funcionarios", status_code=303)


# ---------------------------------------------------------------------------
# Gastos
# ---------------------------------------------------------------------------

@app.get("/gastos")
def list_expenses(request: Request, month: int | None = None, year: int | None = None):
    db = get_session()
    try:
        today = date.today()
        month = month or today.month
        year = year or today.year

        expenses = (
            db.query(Expense)
            .filter(extract("month", Expense.date) == month, extract("year", Expense.date) == year)
            .order_by(Expense.date.desc())
            .all()
        )
        total = sum(e.amount for e in expenses)
        employees = db.query(Employee).filter(Employee.active.is_(True)).order_by(Employee.name).all()

        return templates.TemplateResponse(
            "gastos.html",
            {
                "request": request,
                "page": "gastos",
                "expenses": expenses,
                "total": total,
                "employees": employees,
                "categories": CATEGORIES,
                "months": MONTHS,
                "years": [year - 1, year, year + 1],
                "selected_month": month,
                "selected_year": year,
                "today": today,
            },
        )
    finally:
        db.close()


@app.post("/gastos/novo")
def create_expense(
    description: str = Form(...),
    category: str = Form("Geral"),
    amount: float = Form(0),
    date_: str = Form(..., alias="date"),
    employee_id: str = Form(""),
):
    db = get_session()
    try:
        expense = Expense(
            description=description.strip(),
            category=category,
            amount=amount,
            date=parse_date(date_),
            employee_id=int(employee_id) if employee_id else None,
        )
        db.add(expense)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/gastos", status_code=303)


@app.post("/gastos/{expense_id}/excluir")
def delete_expense(expense_id: int):
    db = get_session()
    try:
        expense = db.get(Expense, expense_id)
        if expense:
            db.delete(expense)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/gastos", status_code=303)


# ---------------------------------------------------------------------------
# Registros (faltas, anotações, adiantamentos)
# ---------------------------------------------------------------------------

@app.get("/registros")
def registros(request: Request, tab: str = "faltas"):
    db = get_session()
    try:
        employees = db.query(Employee).order_by(Employee.name).all()
        absences = db.query(Absence).order_by(Absence.date.desc()).all()
        notes = db.query(Note).order_by(Note.pinned.desc(), Note.created_at.desc()).all()
        advances = db.query(Advance).order_by(Advance.date.desc()).all()

        total_absence_days = sum(a.days for a in absences)
        open_advance_total = sum(a.amount for a in advances if a.status == "open")

        return templates.TemplateResponse(
            "registros.html",
            {
                "request": request,
                "page": "registros",
                "tab": tab,
                "employees": employees,
                "absences": absences,
                "notes": notes,
                "advances": advances,
                "total_absence_days": total_absence_days,
                "open_advance_total": open_advance_total,
                "today": date.today(),
            },
        )
    finally:
        db.close()


@app.post("/registros/faltas/novo")
def create_absence(
    employee_id: int = Form(...),
    date_: str = Form(..., alias="date"),
    days: int = Form(1),
    reason: str = Form(...),
    justified: str | None = Form(None),
):
    db = get_session()
    try:
        absence = Absence(
            employee_id=employee_id,
            date=parse_date(date_),
            days=days,
            reason=reason.strip(),
            justified=bool(justified),
        )
        db.add(absence)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=faltas", status_code=303)


@app.post("/registros/faltas/{absence_id}/excluir")
def delete_absence(absence_id: int):
    db = get_session()
    try:
        absence = db.get(Absence, absence_id)
        if absence:
            db.delete(absence)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=faltas", status_code=303)


@app.post("/registros/anotacoes/novo")
def create_note(title: str = Form(...), content: str = Form(...), pinned: str | None = Form(None)):
    db = get_session()
    try:
        note = Note(title=title.strip(), content=content.strip(), pinned=bool(pinned))
        db.add(note)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=anotacoes", status_code=303)


@app.post("/registros/anotacoes/{note_id}/editar")
def update_note(note_id: int, title: str = Form(...), content: str = Form(...), pinned: str | None = Form(None)):
    db = get_session()
    try:
        note = db.get(Note, note_id)
        if note:
            note.title = title.strip()
            note.content = content.strip()
            note.pinned = bool(pinned)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=anotacoes", status_code=303)


@app.post("/registros/anotacoes/{note_id}/excluir")
def delete_note(note_id: int):
    db = get_session()
    try:
        note = db.get(Note, note_id)
        if note:
            db.delete(note)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=anotacoes", status_code=303)


@app.post("/registros/adiantamentos/novo")
def create_advance(
    employee_id: int = Form(...),
    amount: float = Form(0),
    date_: str = Form(..., alias="date"),
    description: str = Form(""),
    status: str = Form("open"),
):
    db = get_session()
    try:
        advance = Advance(
            employee_id=employee_id,
            amount=amount,
            date=parse_date(date_),
            description=description or None,
            status=status,
        )
        db.add(advance)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=adiantamentos", status_code=303)


@app.post("/registros/adiantamentos/{advance_id}/toggle")
def toggle_advance(advance_id: int):
    db = get_session()
    try:
        advance = db.get(Advance, advance_id)
        if advance:
            advance.status = "settled" if advance.status == "open" else "open"
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=adiantamentos", status_code=303)


@app.post("/registros/adiantamentos/{advance_id}/excluir")
def delete_advance(advance_id: int):
    db = get_session()
    try:
        advance = db.get(Advance, advance_id)
        if advance:
            db.delete(advance)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/registros?tab=adiantamentos", status_code=303)
