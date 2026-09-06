"""Configuração do banco de dados.

Localmente usa SQLite (arquivo controle_servicos.db) sem precisar configurar
nada. Em produção (Render, Railway, etc.), defina a variável de ambiente
DATABASE_URL com a string de conexão do Postgres (ex.: do Neon) e o app passa
a usar Postgres automaticamente — necessário porque hospedagens gratuitas
apagam arquivos locais (como o .db do SQLite) a cada reinício do servidor.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./controle_servicos.db")

# O Neon (e alguns outros provedores) fornecem a URL como "postgres://",
# mas o SQLAlchemy exige o prefixo "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependência do FastAPI: abre uma sessão e garante que ela é fechada."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
