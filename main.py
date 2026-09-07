"""Controle de Serviços — aplicação FastAPI + Jinja2 + SQLite.

Sistema de controle de serviços com cadastro de funcionários,
controle de salários, gastos, faltas, adiantamentos e anotações.
"""

import logging
import base64
import hashlib
import hmac
import os
import re
import time
from datetime import date, datetime
from html import escape
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import Base, SessionLocal, engine
from models import Absence, Advance, Employee, Expense, Note


def load_local_env() -> None:
    """Carrega o .env local sem substituir variáveis já configuradas."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_local_env()

# Cria as tabelas no banco (se ainda não existirem)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Controle de Serviços")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)
AUTH_COOKIE = "controle_session"
SESSION_TTL = 60 * 60 * 8
AUTH_USERS = {"patrick": "Patrick", "fernando": "Fernando", "manuela": "Manuela"}
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")
AUTH_PASSWORD_DIGEST = ""


def password_digest(password: str) -> str:
    """Gera um hash lento e com salt para comparar a senha com segurança."""
    salt = hashlib.sha256(AUTH_SECRET.encode()).digest()[:16]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return base64.urlsafe_b64encode(digest).decode()


AUTH_PASSWORD_DIGEST = password_digest(AUTH_PASSWORD) if AUTH_SECRET else ""


def make_session(username: str) -> str:
    payload = f"{username}:{int(time.time())}"
    signature = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def get_session_user(request: Request) -> str | None:
    if not AUTH_SECRET or not AUTH_PASSWORD:
        return None
    try:
        decoded = base64.urlsafe_b64decode(request.cookies[AUTH_COOKIE]).decode()
        username, issued_at, signature = decoded.split(":", 2)
        payload = f"{username}:{issued_at}"
        expected = hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if username not in AUTH_USERS or not hmac.compare_digest(signature, expected):
            return None
        if time.time() - int(issued_at) > SESSION_TTL:
            return None
        return username
    except (KeyError, ValueError, TypeError, UnicodeDecodeError):
        return None


@app.get("/login")
def login_page(request: Request, error: str = ""):
    if get_session_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error},
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    username = username.strip().lower()
    password_matches = AUTH_PASSWORD_DIGEST and hmac.compare_digest(
        password_digest(password), AUTH_PASSWORD_DIGEST
    )
    if username not in AUTH_USERS or not password_matches:
        logger.warning("Tentativa de login recusada para usuário %s", username[:40])
        return RedirectResponse("/login?error=1", status_code=303)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        AUTH_COOKIE,
        make_session(username),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE)
    return response


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    public_paths = {"/login", "/static"}
    is_public = request.url.path == "/login" or request.url.path.startswith("/static/")
    if request.url.path not in public_paths and not is_public:
        if not get_session_user(request):
            from fastapi.responses import RedirectResponse as AuthRedirect
            return AuthRedirect("/login", status_code=303)
    return await call_next(request)


def error_response(status_code: int, title: str, message: str) -> HTMLResponse:
    """Retorna uma página curta e segura para erros exibidos ao usuário."""
    safe_title = escape(title)
    safe_message = escape(message)
    content = f"""
    <!doctype html>
        <html lang="pt-br">
            <head><meta charset="UTF-8"><title>{safe_title} · Controle de Serviços</title>
      <style>
        body {{ margin: 0; padding: 48px 20px; background: #101418; color: #edf2f1;
          font: 16px system-ui, sans-serif; }}
        main {{ max-width: 560px; margin: auto; padding: 32px; background: #171d23;
          border: 1px solid #2b3741; border-radius: 14px; }}
        h1 {{ margin-top: 0; }} p {{ color: #9ba9ad; line-height: 1.6; }}
        a {{ color: #a7e1d8; font-weight: 700; }}
      </style></head>
    <body><main><h1>{safe_title}</h1><p>{safe_message}</p>
        <a href="/">Voltar ao painel</a></main></body>
    </html>
    """
    return HTMLResponse(content=content, status_code=status_code)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    del request
    return error_response(exc.status_code, "Não foi possível concluir", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("Dados inválidos recebidos em %s: %s", request.url.path, exc.errors())
    return error_response(
        422,
        "Dados inválidos",
        "Confira os campos preenchidos e tente novamente.",
    )


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Erro de banco de dados em %s", request.url.path, exc_info=exc)
    return error_response(
        500,
        "Erro ao acessar os dados",
        "Não foi possível concluir a operação agora. Tente novamente em instantes.",
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception):
    logger.exception("Erro inesperado em %s", request.url.path, exc_info=exc)
    return error_response(
        500,
        "Algo deu errado",
        "Ocorreu um erro inesperado. Tente novamente ou volte ao painel.",
    )


def format_money(value) -> str:
    value = value or 0
    return "R$ " + f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_date(value) -> str:
    if not value:
        return "Não informado"
    return value.strftime("%d/%m/%Y")


def normalize_phone(value: str | None) -> str | None:
    """Normaliza e valida celular brasileiro com DDD (11 dígitos)."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not re.fullmatch(r"[1-9]{2}9\d{8}", digits):
        raise HTTPException(
            status_code=400,
            detail="Informe um celular válido com DDD e 11 dígitos.",
        )
    return digits


def format_phone(value) -> str:
    """Exibe um celular normalizado no formato conhecido pelo usuário."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    return value or "Não informado"


def phone_digits(value) -> str:
    return re.sub(r"\D", "", value or "")


templates.env.filters["money"] = format_money
templates.env.filters["data"] = format_date
templates.env.filters["telefone"] = format_phone
templates.env.filters["digitos"] = phone_digits

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
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="A data informada é inválida. Use o formato correto.",
        ) from exc


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
        username = get_session_user(request)
        user_name = AUTH_USERS.get(username or "", "Usuário")

        hour = datetime.now().hour
        if 5 <= hour < 12:
            saudacao = "Bom dia"
        elif 12 <= hour < 18:
            saudacao = "Boa tarde"
        else:
            saudacao = "Boa noite"

        employees = db.query(Employee).all()
        active_employees = [e for e in employees if e.active]

        settled_advances = (
            db.query(Advance)
            .filter(
                Advance.status == "settled",
                extract("month", Advance.date) == month,
                extract("year", Advance.date) == year,
            )
            .all()
        )
        settled_by_employee = {}
        for advance in settled_advances:
            settled_by_employee[advance.employee_id] = (
                settled_by_employee.get(advance.employee_id, 0) + advance.amount
            )

        monthly_payroll = sum(
            max(0, e.salary - settled_by_employee.get(e.id, 0))
            for e in active_employees
        )

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

        month_absences = (
            db.query(Absence)
            .filter(
                Absence.justified.is_(False),
                extract("month", Absence.date) == month,
                extract("year", Absence.date) == year,
            )
            .order_by(Absence.date.desc())
            .all()
        )
        absence_by_employee = {}
        for absence in month_absences:
            absence_by_employee.setdefault(absence.employee_id, []).append(absence)

        absence_employees = [
            {
                "employee": employee,
                "days": sum(a.days for a in absences),
            }
            for employee in employees
            if (absences := absence_by_employee.get(employee.id))
        ]
        open_absence_days = sum(item["days"] for item in absence_employees)
        open_advances_count = db.query(Advance).filter(Advance.status == "open").count()

        recent_expenses = db.query(Expense).order_by(Expense.date.desc()).limit(5).all()

        summary = {
            "month": month,
            "year": year,
            "total_employees": len(employees),
            "active_employees": len(active_employees),
            "monthly_payroll": monthly_payroll,
            "settled_advances": sum(a.amount for a in settled_advances),
            "monthly_expenses": monthly_expenses_total,
            "monthly_total": monthly_payroll + monthly_expenses_total,
            "monthly_advances": monthly_advances_total,
            "open_absences": open_absence_days,
            "open_advances": open_advances_count,
            "notes_count": db.query(Note).count(),
        }

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "page": "dashboard",
                "summary": summary,
                "recent_expenses": recent_expenses,
                "absence_employees": absence_employees,
                "months": MONTHS,
                "years": [year - 1, year, year + 1],
                "today": today,
                "saudacao": saudacao,
                "user_name": user_name,
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
            request=request,
            name="funcionarios.html",
            context={
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
            phone=normalize_phone(phone),
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
            employee.phone = normalize_phone(phone)
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
            request=request,
            name="gastos.html",
            context={
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
            request=request,
            name="registros.html",
            context={
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