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

# Converte URLs do CockroachDB/Postgres para o dialeto do CockroachDB no SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "cockroachdb://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "cockroachdb://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

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