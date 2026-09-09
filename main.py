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
from models import Absence, Advance, Employee, Expense, MonthlySummary, Note, Payment


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


def migrate_payment_table() -> None:
    """Atualiza a tabela antiga de pagamentos para o formato mensal independente."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        columns = connection.exec_driver_sql("PRAGMA table_info(payments)").fetchall()
        if not columns:
            return
        column_names = {column[1] for column in columns}
        expense_column = next(column for column in columns if column[1] == "expense_id")
        employee_column = next(column for column in columns if column[1] == "employee_id")
        if expense_column[3] == 0 and employee_column[3] == 0 and "method" not in column_names:
            return
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql(
            """
            CREATE TABLE payments_new (
                id INTEGER NOT NULL PRIMARY KEY,
                expense_id INTEGER,
                employee_id INTEGER,
                amount FLOAT NOT NULL DEFAULT 0,
                date DATE NOT NULL,
                description VARCHAR,
                FOREIGN KEY(expense_id) REFERENCES expenses (id) ON DELETE CASCADE,
                FOREIGN KEY(employee_id) REFERENCES employees (id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO payments_new (id, expense_id, employee_id, amount, date, description)
            SELECT id, expense_id, employee_id, amount, date, description FROM payments
            """
        )
        connection.exec_driver_sql("DROP TABLE payments")
        connection.exec_driver_sql("ALTER TABLE payments_new RENAME TO payments")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


migrate_payment_table()

app = FastAPI(title="Controle de Serviços")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)
AUTH_COOKIE = "controle_session"
SESSION_TTL = 60 * 60 * 8
AUTH_USERS = {"patrick": "Patrick", "fernando": "Fernando", "manuela": "Manuela"}


def read_auth_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


AUTH_PASSWORD = read_auth_env("AUTH_PASSWORD")
AUTH_SECRET = read_auth_env("AUTH_SECRET")
AUTH_PASSWORD_DIGEST = ""


def password_digest(password: str) -> str:
    """Gera um hash lento e com salt para comparar a senha com segurança."""
    salt = hashlib.sha256(AUTH_SECRET.encode()).digest()[:16]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return base64.urlsafe_b64encode(digest).decode()


AUTH_PASSWORD_DIGEST = password_digest(AUTH_PASSWORD) if AUTH_SECRET else ""

if not AUTH_PASSWORD or not AUTH_SECRET:
    logger.warning("AUTH_PASSWORD e AUTH_SECRET precisam estar configuradas no ambiente.")
else:
    logger.info("Autenticação configurada: senha com %d caracteres e segredo presente.", len(AUTH_PASSWORD))


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
    login_error = ""
    if error == "config":
        login_error = "O login ainda não foi configurado no servidor. Verifique AUTH_PASSWORD e AUTH_SECRET no Render."
    elif error:
        login_error = "Usuário ou senha inválidos."
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": login_error},
    )


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    username = username.strip().lower()
    if not AUTH_PASSWORD or not AUTH_SECRET:
        logger.error("Login indisponível: AUTH_PASSWORD ou AUTH_SECRET não configurada.")
        return RedirectResponse("/login?error=config", status_code=303)
    password_matches = AUTH_PASSWORD_DIGEST and hmac.compare_digest(
        password_digest(password), AUTH_PASSWORD_DIGEST
    )
    if username not in AUTH_USERS or not password_matches:
        logger.warning(
            "Tentativa de login recusada: usuário válido=%s, senha válida=%s",
            username in AUTH_USERS,
            bool(password_matches),
        )
        return RedirectResponse("/login?error=1", status_code=303)

    response = RedirectResponse("/", status_code=303)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    is_https = forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    response.set_cookie(
        AUTH_COOKIE,
        make_session(username),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE, path="/")
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


def save_monthly_summary(db: Session, month: int, year: int, total_cost: float) -> MonthlySummary:
    """Atualiza o fechamento do mês para manter o total disponível no histórico."""
    summary = (
        db.query(MonthlySummary)
        .filter(MonthlySummary.month == month, MonthlySummary.year == year)
        .first()
    )
    if summary is None:
        summary = MonthlySummary(month=month, year=year)
        db.add(summary)
    summary.total_cost = total_cost
    summary.updated_at = datetime.utcnow()
    db.commit()
    return summary


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

        month_payments = (
            db.query(Payment)
            .filter(extract("month", Payment.date) == month, extract("year", Payment.date) == year)
            .all()
        )
        paid_by_employee = {}
        for payment in month_payments:
            paid_by_employee[payment.employee_id] = (
                paid_by_employee.get(payment.employee_id, 0) + payment.amount
            )

        monthly_payroll = sum(
            max(
                0,
                e.salary
                - settled_by_employee.get(e.id, 0)
                - paid_by_employee.get(e.id, 0),
            )
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
        monthly_payments_total = sum(payment.amount for payment in month_payments)
        monthly_total = monthly_expenses_total + monthly_payments_total
        save_monthly_summary(db, month, year, monthly_total)
        monthly_summaries = (
            db.query(MonthlySummary)
            .order_by(MonthlySummary.year.desc(), MonthlySummary.month.desc())
            .all()
        )

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
        recent_payments = db.query(Payment).order_by(Payment.date.desc()).limit(5).all()
        recent_movements = [
            {
                "kind": "expense",
                "description": expense.description,
                "detail": f"{expense.category} · {expense.date.strftime('%d/%m/%Y')}",
                "amount": expense.amount,
                "person": expense.employee.name if expense.employee else "Operação geral",
                "date": expense.date,
            }
            for expense in recent_expenses
        ] + [
            {
                "kind": "payment",
                "description": f"Pagamento · {payment.employee.name if payment.employee else 'Funcionário não informado'}",
                "detail": f"Folha · {payment.date.strftime('%d/%m/%Y')}",
                "amount": payment.amount,
                "person": payment.description or "Pagamento realizado",
                "date": payment.date,
            }
            for payment in recent_payments
        ]
        recent_movements.sort(key=lambda movement: movement["date"], reverse=True)
        recent_movements = recent_movements[:5]

        summary = {
            "month": month,
            "year": year,
            "total_employees": len(employees),
            "active_employees": len(active_employees),
            "monthly_payroll": monthly_payroll,
            "monthly_payments": monthly_payments_total,
            "settled_advances": sum(a.amount for a in settled_advances),
            "monthly_expenses": monthly_expenses_total,
            "monthly_total": monthly_total,
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
                "recent_movements": recent_movements,
                "absence_employees": absence_employees,
                "months": MONTHS,
                "years": [year - 1, year, year + 1],
                "today": today,
                "saudacao": saudacao,
                "user_name": user_name,
                "monthly_summaries": monthly_summaries,
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
    return RedirectResponse(f"/gastos?month={parse_date(date_).month}&year={parse_date(date_).year}", status_code=303)


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


@app.get("/pagamentos")
def list_payments(
    request: Request,
    month: int | None = None,
    year: int | None = None,
):
    db = get_session()
    try:
        today = date.today()
        month = month or today.month
        year = year or today.year
        employees = db.query(Employee).filter(Employee.active.is_(True)).order_by(Employee.name).all()
        payments = (
            db.query(Payment)
            .filter(extract("month", Payment.date) == month, extract("year", Payment.date) == year)
            .order_by(Payment.date.desc())
            .all()
        )
        paid_by_employee = {}
        for payment in payments:
            paid_by_employee[payment.employee_id] = paid_by_employee.get(payment.employee_id, 0) + payment.amount
        unpaid_employees = [
            employee
            for employee in employees
            if paid_by_employee.get(employee.id, 0) < employee.salary
        ]
        remaining_by_employee = {
            employee.id: employee.salary - paid_by_employee.get(employee.id, 0)
            for employee in unpaid_employees
        }
        return templates.TemplateResponse(
            request=request,
            name="pagamentos.html",
            context={
                "page": "pagamentos",
                "payments": payments,
                "unpaid_employees": unpaid_employees,
                "remaining_by_employee": remaining_by_employee,
                "payment_total": sum(payment.amount for payment in payments),
                "months": MONTHS,
                "years": [year - 1, year, year + 1],
                "selected_month": month,
                "selected_year": year,
                "today": today,
            },
        )
    finally:
        db.close()


@app.get("/fechamentos")
def list_monthly_summaries(
    request: Request,
    month: int | None = None,
    year: int | None = None,
):
    db = get_session()
    try:
        summaries = (
            db.query(MonthlySummary)
            .order_by(MonthlySummary.year.desc(), MonthlySummary.month.desc())
            .all()
        )
        selected_summary = None
        if month and year:
            selected_summary = (
                db.query(MonthlySummary)
                .filter(MonthlySummary.month == month, MonthlySummary.year == year)
                .first()
            )
        return templates.TemplateResponse(
            request=request,
            name="fechamentos.html",
            context={
                "page": "fechamentos",
                "monthly_summaries": summaries,
                "months": MONTHS,
                "today": date.today(),
                "selected_summary": selected_summary,
            },
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Registros (faltas, anotações, adiantamentos, pagamentos)
# ---------------------------------------------------------------------------

@app.get("/registros")
def registros(
    request: Request,
    tab: str = "faltas",
    month: int | None = None,
    year: int | None = None,
):
    db = get_session()
    try:
        today = date.today()
        month = month or today.month
        year = year or today.year
        employees = db.query(Employee).order_by(Employee.name).all()
        active_employees = [employee for employee in employees if employee.active]
        absences = db.query(Absence).order_by(Absence.date.desc()).all()
        notes = db.query(Note).order_by(Note.pinned.desc(), Note.created_at.desc()).all()
        advances = db.query(Advance).order_by(Advance.date.desc()).all()
        payments = (
            db.query(Payment)
            .filter(extract("month", Payment.date) == month, extract("year", Payment.date) == year)
            .order_by(Payment.date.desc())
            .all()
        )

        total_absence_days = sum(a.days for a in absences)
        open_advance_total = sum(a.amount for a in advances if a.status == "open")
        payment_total = sum(payment.amount for payment in payments)
        paid_employee_ids = {payment.employee_id for payment in payments}
        unpaid_employees = [
            employee for employee in active_employees if employee.id not in paid_employee_ids
        ]

        return templates.TemplateResponse(
            request=request,
            name="registros.html",
            context={
                "page": "registros",
                "tab": tab,
                "employees": employees,
                "unpaid_employees": unpaid_employees,
                "absences": absences,
                "notes": notes,
                "advances": advances,
                "total_absence_days": total_absence_days,
                "open_advance_total": open_advance_total,
                "payments": payments,
                "payment_total": payment_total,
                "months": MONTHS,
                "years": [year - 1, year, year + 1],
                "selected_month": month,
                "selected_year": year,
                "today": today,
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


@app.post("/registros/faltas/{absence_id}/toggle")
def toggle_absence(absence_id: int):
    db = get_session()
    try:
        absence = db.get(Absence, absence_id)
        if absence:
            absence.justified = not absence.justified
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


@app.post("/pagamentos/novo")
def create_payment(
    employee_id: int = Form(...),
    amount: float = Form(0),
    date_: str = Form(..., alias="date"),
    description: str = Form(""),
):
    payment_date = parse_date(date_)
    db = get_session()
    try:
        employee = db.get(Employee, employee_id)
        if not employee:
            raise HTTPException(status_code=404, detail="Funcionário não encontrado.")
        month_payments = (
            db.query(Payment)
            .filter(
                Payment.employee_id == employee_id,
                extract("month", Payment.date) == payment_date.month,
                extract("year", Payment.date) == payment_date.year,
            )
            .all()
        )
        paid_amount = sum(payment.amount for payment in month_payments)
        remaining_salary = max(0, employee.salary - paid_amount)
        if remaining_salary == 0:
            raise HTTPException(
                status_code=400,
                detail="Este funcionário já recebeu o salário completo neste mês.",
            )
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Informe um valor de pagamento maior que zero.")
        payment_amount = min(amount, remaining_salary)
        advance_amount = max(0, amount - remaining_salary)
        db.add(
            Payment(
                employee_id=employee_id,
                amount=payment_amount,
                date=payment_date,
                description=description.strip() or None,
            )
        )
        if advance_amount > 0:
            db.add(
                Advance(
                    employee_id=employee_id,
                    amount=advance_amount,
                    date=payment_date,
                    description="Excedente do pagamento mensal",
                    status="open",
                )
            )
        db.commit()
    finally:
        db.close()
    return RedirectResponse(f"/pagamentos?month={payment_date.month}&year={payment_date.year}", status_code=303)


@app.post("/pagamentos/{payment_id}/excluir")
def delete_payment(payment_id: int):
    db = get_session()
    try:
        payment = db.get(Payment, payment_id)
        if payment:
            db.delete(payment)
            db.commit()
    finally:
        db.close()
    return RedirectResponse("/pagamentos", status_code=303)