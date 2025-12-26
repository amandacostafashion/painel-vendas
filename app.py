from zoneinfo import ZoneInfo
from flask import Flask, render_template, request
import pandas as pd
from datetime import date, datetime, timedelta

APP_TITLE = "Acompanhamento de Vendas - Amanda Costa Fashion"

# CSV publicado do Google Sheets
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vToiXxDVpr8cg8rSGdketwsb8rRnYPasZvogJbDunQCtpYvItF0ug9nQZNi6jhxSCZ2kOZqDXgcFDuM/pub?gid=0&single=true&output=csv"

# Nomes das colunas no seu CSV
COL_DATA = "Emissao"
COL_VALOR = "Total Nota"
COL_CLIENTE = "Cliente"  # conforme você informou

app = Flask(__name__)


def to_brl(v: float) -> str:
    """Formata número para BRL."""
    if v is None:
        v = 0.0
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def read_sheet() -> pd.DataFrame:
    """Lê o CSV do Sheets, normaliza colunas e converte tipos."""
    df = pd.read_csv(SHEET_CSV_URL, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]

    if COL_DATA not in df.columns:
        raise ValueError(
            f"Coluna de data '{COL_DATA}' não encontrada. Colunas disponíveis: {list(df.columns)}"
        )
    if COL_VALOR not in df.columns:
        raise ValueError(
            f"Coluna de valor '{COL_VALOR}' não encontrada. Colunas disponíveis: {list(df.columns)}"
        )
    if COL_CLIENTE not in df.columns:
        raise ValueError(
            f"Coluna de cliente '{COL_CLIENTE}' não encontrada. Colunas disponíveis: {list(df.columns)}"
        )

    # Converte data (dd/mm/aaaa ou aaaa-mm-dd)
    df[COL_DATA] = pd.to_datetime(df[COL_DATA], errors="coerce", dayfirst=True)

    # Converte valor (aceita "1.234,56" e "1234.56")
    val = df[COL_VALOR].astype(str).str.strip()
    val = (
        val.replace({"R$": "", " ": ""}, regex=True)
        .str.replace(".", "", regex=False)   # remove milhar pt-br
        .str.replace(",", ".", regex=False)  # vírgula decimal -> ponto
    )
    df[COL_VALOR] = pd.to_numeric(val, errors="coerce").fillna(0.0)

    # Normaliza cliente
    df[COL_CLIENTE] = df[COL_CLIENTE].astype(str).str.strip()

    # Mantém só linhas com data válida
    df = df[df[COL_DATA].notna()].copy()
    return df


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    """Soma/subtrai meses preservando o ano, com dia protegido."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)  # seguro
    return date(y, m, day)


def safe_last_year(d: date) -> date:
    """Mesmo dia do ano anterior; fallback para 28/02 se for 29/02."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def pct(a: float, b: float):
    """Variação percentual: atual a vs base b."""
    if b == 0:
        return None if a == 0 else 100.0
    return (a / b - 1) * 100.0


@app.get("/")
def dashboard():
    # ===== FUSO HORÁRIO (Brasil) =====
    tz = ZoneInfo("America/Sao_Paulo")

    # Data de referência (padrão: hoje no fuso do Brasil)
    ref_str = request.args.get("ref")  # yyyy-mm-dd
    if ref_str:
        ref = datetime.strptime(ref_str, "%Y-%m-%d").date()
    else:
        ref = datetime.now(tz).date()

    try:
        df = read_sheet()
    except Exception as e:
        return render_template(
            "dashboard.html",
            title=APP_TITLE,
            error=str(e),
            ref=ref.strftime("%Y-%m-%d"),
        )

    # Coluna auxiliar por dia (date)
    df["data"] = df[COL_DATA].dt.date

    # ===== DIA: hoje / ontem / mesmo dia ano anterior / dia seguinte ano anterior =====
    hoje = ref
    ontem = ref - timedelta(days=1)

    ano_ant = safe_last_year(ref)
    ano_ant_dia_seguinte = ano_ant + timedelta(days=1)

    v_hoje = float(df.loc[df["data"] == hoje, COL_VALOR].sum())
    v_ontem = float(df.loc[df["data"] == ontem, COL_VALOR].sum())
    v_ano_ant = float(df.loc[df["data"] == ano_ant, COL_VALOR].sum())
    v_ano_ant_dia_seguinte = float(df.loc[df["data"] == ano_ant_dia_seguinte, COL_VALOR].sum())

    pct_dia = pct(v_hoje, v_ontem)
    pct_ano = pct(v_hoje, v_ano_ant)
    pct_ano_dia_seguinte = pct(v_hoje, v_ano_ant_dia_seguinte)

    # ===== MÊS proporcional por dias (MoM) =====
    ini_mes = month_start(ref)
    ini_mes_ant = month_start(add_months(ref, -1))

    fim_atual = ref
    fim_mes_ant = ini_mes_ant + timedelta(days=max(ref.day - 1, 0))

    v_mes_atual = float(
        df.loc[(df["data"] >= ini_mes) & (df["data"] <= fim_atual), COL_VALOR].sum()
    )
    v_mes_ant_proporcional = float(
        df.loc[(df["data"] >= ini_mes_ant) & (df["data"] <= fim_mes_ant), COL_VALOR].sum()
    )

    pct_mes = pct(v_mes_atual, v_mes_ant_proporcional)

    # ===== MÊS ano anterior proporcional por dias (YoY) =====
    ini_mes_ano_ant = date(ref.year - 1, ref.month, 1)
    fim_mes_ano_ant = ini_mes_ano_ant + timedelta(days=max(ref.day - 1, 0))

    v_mes_ano_ant = float(
        df.loc[(df["data"] >= ini_mes_ano_ant) & (df["data"] <= fim_mes_ano_ant), COL_VALOR].sum()
    )
    pct_mes_ano_ant = pct(v_mes_atual, v_mes_ano_ant)

    # ===== Recorte do mês atual (até a data de referência) =====
    df_mes = df[(df["data"] >= ini_mes) & (df["data"] <= fim_atual)].copy()

    # ===== Top 5 dias do mês atual =====
    top_dias = (
        df_mes.groupby("data")[COL_VALOR]
        .sum()
        .sort_values(ascending=False)
        .head(5)
        .reset_index()
    )
    top_dias["valor_fmt"] = top_dias[COL_VALOR].apply(to_brl)
    top_dias["data_fmt"] = top_dias["data"].apply(lambda x: x.strftime("%d/%m/%Y"))

    # ===== Top 5 clientes do mês (exceto Consumidor Final) =====
    df_cli = df_mes.copy()

    df_cli = df_cli[df_cli[COL_CLIENTE].astype(str).str.strip() != ""]
    mask_cf = df_cli[COL_CLIENTE].astype(str).str.upper().str.contains("CONSUMIDOR FINAL", na=False)
    df_cli = df_cli[~mask_cf].copy()

    top_clientes = (
        df_cli.groupby(COL_CLIENTE)[COL_VALOR]
        .sum()
        .sort_values(ascending=False)
        .head(5)
        .reset_index()
    )

    top_clientes = top_clientes.rename(columns={COL_CLIENTE: "Cliente"})
    top_clientes["valor_fmt"] = top_clientes[COL_VALOR].apply(to_brl)

    return render_template(
        "dashboard.html",
        title=APP_TITLE,
        error=None,
        ref=ref.strftime("%Y-%m-%d"),

        hoje_str=hoje.strftime("%d/%m/%Y"),
        ontem_str=ontem.strftime("%d/%m/%Y"),

        ano_ant_str=ano_ant.strftime("%d/%m/%Y"),
        ano_ant_dia_seguinte_str=ano_ant_dia_seguinte.strftime("%d/%m/%Y"),

        ini_mes_str=ini_mes.strftime("%d/%m/%Y"),

        v_hoje=to_brl(v_hoje),
        v_ontem=to_brl(v_ontem),

        v_ano_ant=to_brl(v_ano_ant),
        v_ano_ant_dia_seguinte=to_brl(v_ano_ant_dia_seguinte),

        v_mes_atual=to_brl(v_mes_atual),
        v_mes_ant=to_brl(v_mes_ant_proporcional),

        # NOVOS CAMPOS (mesmo mês ano anterior)
        v_mes_ano_ant=to_brl(v_mes_ano_ant),
        pct_mes_ano_ant=pct_mes_ano_ant,
        mes_ano_ant_str=ini_mes_ano_ant.strftime("%m/%Y"),

        pct_dia=pct_dia,
        pct_mes=pct_mes,
        pct_ano=pct_ano,
        pct_ano_dia_seguinte=pct_ano_dia_seguinte,

        top_dias=top_dias.to_dict(orient="records"),
        top_clientes=top_clientes.to_dict(orient="records"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5556, debug=True)
