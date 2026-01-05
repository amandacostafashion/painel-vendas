from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, jsonify
import pandas as pd
from datetime import date, datetime, timedelta
import calendar

APP_TITLE = "Acompanhamento de Vendas - Amanda Costa Fashion"

# CSV publicado do Google Sheets (VENDAS)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vToiXxDVpr8cg8rSGdketwsb8rRnYPasZvogJbDunQCtpYvItF0ug9nQZNi6jhxSCZ2kOZqDXgcFDuM/pub?gid=0&single=true&output=csv"

# CSV publicado do Google Sheets (FERIADOS) - gid informado por você
HOLIDAYS_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vToiXxDVpr8cg8rSGdketwsb8rRnYPasZvogJbDunQCtpYvItF0ug9nQZNi6jhxSCZ2kOZqDXgcFDuM/pub?gid=2066099077&single=true&output=csv"

# Nomes das colunas no seu CSV de vendas
COL_DATA = "Emissao"
COL_VALOR = "Total Nota"
COL_CLIENTE = "Cliente"

app = Flask(__name__)

MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# =========================
# METAS (exemplo por ano/mês)
# =========================
METAS_POR_ANO = {
    2026: {
        1: 65000.00,
        2: 62000.00,
        3: 61000.00,
        4: 78000.00,
        5: 127000.00,
        6: 124000.00,
        7: 115000.00,
        8: 63000.00,
        9: 60000.00,
        10: 84000.00,
        11: 66000.00,
        12: 119000.00,
    }
}
METAS_DEFAULT = {m: 0.0 for m in range(1, 13)}


def to_brl(v: float) -> str:
    """Formata número para BRL."""
    if v is None:
        v = 0.0
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def read_sheet() -> pd.DataFrame:
    """Lê o CSV do Sheets (vendas), normaliza colunas e converte tipos."""
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
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df[COL_VALOR] = pd.to_numeric(val, errors="coerce").fillna(0.0)

    # Normaliza cliente
    df[COL_CLIENTE] = df[COL_CLIENTE].astype(str).str.strip()

    # Mantém só linhas com data válida
    df = df[df[COL_DATA].notna()].copy()
    return df


def read_holidays() -> set[date]:
    """
    Lê o CSV de feriados e devolve um set(date).
    Tenta detectar automaticamente a coluna de data (ex: 'data', 'dia', 'dt', etc).
    """
    try:
        df = pd.read_csv(HOLIDAYS_CSV_URL, dtype=str).fillna("")
    except Exception:
        return set()

    df.columns = [str(c).strip() for c in df.columns]
    if df.empty or len(df.columns) == 0:
        return set()

    # tenta achar uma coluna de data por nome
    cols_lower = {c.lower(): c for c in df.columns}
    candidates = []
    for key in cols_lower.keys():
        if "data" in key or "dia" in key or key in ("dt", "date"):
            candidates.append(cols_lower[key])

    col_date = candidates[0] if candidates else df.columns[0]

    s = df[col_date].astype(str).str.strip()
    # converte datas (dd/mm/aaaa ou aaaa-mm-dd)
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    dt = dt.dropna()

    return set(dt.dt.date.tolist())


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    """Soma/subtrai meses preservando o ano, com dia protegido."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)
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


def get_meta_mes(ano: int, mes: int) -> float:
    metas_ano = METAS_POR_ANO.get(ano, METAS_DEFAULT)
    return float(metas_ano.get(mes, 0.0))


def iter_dates(ini: date, fim: date):
    d = ini
    while d <= fim:
        yield d
        d += timedelta(days=1)


def is_sunday(d: date) -> bool:
    # Monday=0 ... Sunday=6
    return d.weekday() == 6


def calc_commercial_days(ano: int, mes: int, feriados: set[date]) -> dict:
    """
    Retorna contagens do mês:
      dias_mes, domingos_mes, feriados_mes (não-domingo), dias_uteis_mes
    onde dias_uteis_mes = dias_mes - domingos_mes - feriados_mes (que não são domingo).
    """
    dias_mes = calendar.monthrange(ano, mes)[1]
    ini = date(ano, mes, 1)
    fim = date(ano, mes, dias_mes)

    domingos = 0
    feriados_no_mes_nao_domingo = 0

    for d in iter_dates(ini, fim):
        if is_sunday(d):
            domingos += 1

    # conta feriados do mês que NÃO caem em domingo (pra não subtrair duas vezes)
    for h in feriados:
        if h.year == ano and h.month == mes:
            if not is_sunday(h):
                feriados_no_mes_nao_domingo += 1

    dias_uteis = dias_mes - domingos - feriados_no_mes_nao_domingo
    if dias_uteis < 0:
        dias_uteis = 0

    return {
        "dias_mes": dias_mes,
        "domingos_mes": domingos,
        "feriados_mes": feriados_no_mes_nao_domingo,
        "dias_uteis_mes": dias_uteis,
    }


def calc_passed_commercial_days(ano: int, mes: int, ref_dt: date, feriados: set[date]) -> int:
    """
    Conta quantos dias úteis comerciais já passaram no mês até a data de referência (inclusive),
    excluindo domingos e feriados.
    """
    ini = date(ano, mes, 1)
    fim = ref_dt

    if fim < ini:
        return 0

    total = 0
    for d in iter_dates(ini, fim):
        if is_sunday(d):
            continue
        if d in feriados:
            continue
        total += 1
    return total


def sum_vendas_periodo(df: pd.DataFrame, ini: date, fim: date) -> float:
    return float(df.loc[(df["data"] >= ini) & (df["data"] <= fim), COL_VALOR].sum())


@app.get("/api/metas/resumo")
def api_metas_resumo():
    """
    Resumo de metas do ano (Jan até mês da referência):
    - Projeção por dias úteis comerciais (exclui domingos e feriados)
    - Mostra também a conta de dias: 31 - 4 - 1 = 26
    """
    tz = ZoneInfo("America/Sao_Paulo")

    ref_str = request.args.get("ref", "")
    if ref_str:
        ref_dt = datetime.strptime(ref_str, "%Y-%m-%d").date()
    else:
        ref_dt = datetime.now(tz).date()

    ano = request.args.get("ano", type=int) or ref_dt.year
    mes_ref = ref_dt.month

    df = read_sheet()
    df["data"] = df[COL_DATA].dt.date

    feriados = read_holidays()

    itens = []

    for m in range(1, mes_ref + 1):
        meta_mes = get_meta_mes(ano, m)

        # período de realizado:
        ini_mes = date(ano, m, 1)
        last_day = calendar.monthrange(ano, m)[1]
        fim_mes = date(ano, m, last_day)

        # se for o mês da referência, soma até ref_dt; caso contrário soma mês fechado
        fim_realizado = ref_dt if (ano == ref_dt.year and m == ref_dt.month) else fim_mes

        realizado_bruto = sum_vendas_periodo(df, ini_mes, fim_realizado)

        # (neste app não há devoluções separadas)
        devolucoes = 0.0
        realizado_liq = max(realizado_bruto - devolucoes, 0.0)

        # contagens do mês (dias úteis comerciais)
        cnt = calc_commercial_days(ano, m, feriados)
        dias_mes = cnt["dias_mes"]
        domingos_mes = cnt["domingos_mes"]
        feriados_mes = cnt["feriados_mes"]
        dias_uteis_mes = cnt["dias_uteis_mes"]

        # dias úteis passados até a referência (inclusive, mas domingo/feriado não contam)
        if ano == ref_dt.year and m == ref_dt.month:
            dias_passados_uteis = calc_passed_commercial_days(ano, m, ref_dt, feriados)
        else:
            # mês fechado: considera todos os dias úteis do mês
            dias_passados_uteis = dias_uteis_mes

        # projeção por dias úteis (evita divisão por zero)
        if dias_passados_uteis > 0:
            media_dia_util = realizado_liq / dias_passados_uteis
            projecao = media_dia_util * dias_uteis_mes
        else:
            media_dia_util = 0.0
            projecao = 0.0

        falta = max(meta_mes - realizado_liq, 0.0)
        pct_atingido = (realizado_liq / meta_mes * 100.0) if meta_mes > 0 else 0.0
        vai_bater = (projecao >= meta_mes) if meta_mes > 0 else False

        resumo_dias_str = (
            f"{dias_mes} Dias - {domingos_mes} Domingos - {feriados_mes} Feriado(s) = "
            f"{dias_uteis_mes} Dias úteis comercial"
        )

        itens.append({
            "mes_num": m,
            "mes": MESES[m - 1],
            "meta": float(meta_mes),
            "realizado_liq": float(realizado_liq),
            "falta": float(falta),
            "pct": float(pct_atingido),
            "projecao": float(projecao),
            "vai_bater": bool(vai_bater),

            # novos campos para exibir no front
            "dias_mes": int(dias_mes),
            "domingos_mes": int(domingos_mes),
            "feriados_mes": int(feriados_mes),
            "dias_uteis_mes": int(dias_uteis_mes),
            "dias_passados_uteis": int(dias_passados_uteis),
            "resumo_dias_str": resumo_dias_str,
        })

    return jsonify({
        "ano": ano,
        "ref": ref_dt.isoformat(),
        "itens": itens
    })


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
