"""Configuração do banco de dados.

Localmente usa SQLite (arquivo controle_servicos.db) sem precisar configurar
nada. Em produção (Render, Railway, etc.), defina a variável de ambiente
DATABASE_URL com a string de conexão do Postgres (ex.: do Neon) e o app passa
a usar Postgres automaticamente — necessário porque hospedagens gratuitas
apagam arquivos locais (como o .db do SQLite) a cada reinício do servidor.
Em URLs PostgreSQL, caracteres especiais na senha devem estar codificados
como URL (por exemplo, @ como %40 e # como %23).
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.exc import ArgumentError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import declarative_base, sessionmaker



def parse_database_url(value: str | None) -> URL:
    """Valida a URL sem expor credenciais e normaliza o alias legado postgres."""
    if value is None:
        return make_url("sqlite:///./controle_servicos.db")

    database_url = value.strip()
    if len(database_url) >= 2 and database_url[0] == database_url[-1] and database_url[0] in {"'", '"'}:
        database_url = database_url[1:-1].strip()
    if not database_url:
        raise RuntimeError("A variável DATABASE_URL está vazia.")

    try:
        parsed_url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError):
        raise RuntimeError(
            "DATABASE_URL inválida. Confira o formato da URL e codifique caracteres especiais da senha (ex.: @ como %40)."
        ) from None

    if parsed_url.drivername == "postgres":
        parsed_url = parsed_url.set(drivername="postgresql")
    supported_driver = parsed_url.drivername.startswith("sqlite") or (
        parsed_url.drivername == "postgresql"
        or parsed_url.drivername.startswith("postgresql+")
    )
    if not supported_driver:
        raise RuntimeError(
            "Esquema de DATABASE_URL não suportado. Use sqlite:// ou postgresql:// para o PostgreSQL do Aiven."
        )
    if parsed_url.drivername == "postgresql" or parsed_url.drivername.startswith("postgresql+"):
        if not parsed_url.host:
            raise RuntimeError(
                "DATABASE_URL inválida: informe o host do PostgreSQL do Aiven e codifique caracteres especiais da senha."
            )
    return parsed_url

DATABASE_URL = parse_database_url(os.environ.get("DATABASE_URL"))

connect_args = {"check_same_thread": False} if DATABASE_URL.drivername.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependência do FastAPI: abre uma sessão e garante que ela é fechada."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()