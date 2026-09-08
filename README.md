# Controle de Serviços (versão Python)

Sistema de controle de serviços com cadastro de funcionários, controle de
salários, gastos, pagamentos, faltas, adiantamentos e anotações.

Reescrito em **Python** (FastAPI + SQLAlchemy + Jinja2 + SQLite) a partir da
versão original em Node.js/TypeScript.

## Requisitos

- Python 3.10+

## Instalação

```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/Mac

pip install -r requirements.txt
```

## Executar

```bash
uvicorn main:app --reload
```

Acesse: http://127.0.0.1:8000

### Acesso

O sistema exige login. Configure `AUTH_PASSWORD` e `AUTH_SECRET` no ambiente
de execução. Existem três usuários com o mesmo nível de acesso: `patrick`,
`fernando` e `manuela`. Nunca publique o arquivo `.env` nem a senha no código.

Na hospedagem, cadastre essas duas variáveis em **Environment Variables** da
plataforma. O arquivo `.env` local não é enviado para o servidor. Depois de
salvar as variáveis, faça um novo deploy/restart. O login usa os usuários
`patrick`, `fernando` ou `manuela` e a senha definida em `AUTH_PASSWORD`.

O banco de dados (`controle_servicos.db`, SQLite) é criado automaticamente
na primeira execução, na mesma pasta do projeto.

## Estrutura

```
main.py          # rotas da aplicação (dashboard, funcionários, gastos, registros)
models.py        # tabelas SQLAlchemy (Employee, Expense, Payment, MonthlySummary, ...)
database.py      # configuração do banco (SQLite)
templates/       # páginas HTML (Jinja2)
static/style.css # estilo visual
requirements.txt # dependências Python
```

## Funcionalidades

- **Dashboard**: folha do mês, gastos, custo total, equipe ativa, movimentações
  recentes, filtro por mês/ano.
- **Funcionários**: cadastro, edição, ativar/inativar, exclusão, busca e filtro.
- **Gastos**: cadastro, exclusão, filtro por mês/ano, vínculo opcional com
  funcionário.
- **Registros**:
  - *Faltas*: data, dias, motivo, se foi justificada.
  - *Anotações*: título, conteúdo, fixar no topo.
  - *Adiantamentos*: valor, data, situação (em aberto/descontado).
  - *Pagamentos* (tela própria): escolha do funcionário, valor, data e observação. Cada pessoa
    só pode ser paga uma vez no mês selecionado e volta a aparecer no mês seguinte.
- **Fechamento mensal**: o custo total de cada mês fica salvo no histórico do
  dashboard, enquanto os lançamentos do novo mês começam separados.
