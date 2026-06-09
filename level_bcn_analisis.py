#!/usr/bin/env python3
"""
Análisis de combinaciones LEVEL: Buenos Aires (EZE) <-> Barcelona (BCN)
======================================================================
Recorre de hoy hasta marzo 2027 y arma viajes IDA + VUELTA con una
separación máxima configurable (por defecto 16 días). Muestra el precio
de la ida, el de la vuelta y el TOTAL (suma de ambos), en dos rankings:

  RANKING A — mirando desde la IDA:
     20 fechas de salida distintas, cada una con su mejor regreso dentro
     de los 16 días siguientes.
  RANKING B — mirando desde la VUELTA:
     20 fechas de regreso distintas, cada una con su mejor salida dentro
     de los 16 días anteriores.

Precios:
  - precio_ida   = calendario SIN outboundDate, por día de salida.
  - precio_vuelta= calendario CON outboundDate=YYYYMMDD, por día de regreso.
  - total        = precio_ida + precio_vuelta (lo sumamos nosotros).

NOTA: conviene abrir un link y verificar que el total de la página
coincida con esta suma. Si la web mostrara otro número, avisá y se ajusta
en una línea (la suma está marcada con  # <-- TOTAL).

Uso:
  pip install requests
  python3 level_bcn_analisis.py
"""

import json
import os
import time
from datetime import date, datetime, timedelta

import requests

# --------------------------- Configuración --------------------------------
ORIGEN, DESTINO = "EZE", "BCN"
HASTA = (2027, 3)        # (anio, mes) inclusive -> marzo 2027
MONEDA = "USD"

MAX_DIAS = 16            # separación máxima entre ida y vuelta
MIN_DIAS = 1            # separación mínima (subilo si no querés viajes cortos)

N_CANDIDATOS = 40        # cuántas idas más baratas evaluar (más = más completo y lento)
TOP = 20                 # tamaño de cada ranking
PAUSA = 1.5              # segundos entre llamadas
ESTADO = "estado_level.json"  # guarda los mínimos de la última revisión

API = "https://www.flylevel.com/nwe/api/pricing/calendar/"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://www.flylevel.com/",
}


# ----------------------------- Utilidades ---------------------------------
def meses_rango(hasta):
    hoy = date.today()
    out, m, a = [], hoy.month, hoy.year
    fin_a, fin_m = hasta
    while (a, m) <= (fin_a, fin_m):
        out.append((m, a))
        m += 1
        if m > 12:
            m, a = 1, a + 1
    return out


def consultar(origen, destino, mes, anio, outbound=None):
    params = {
        "triptype": "RT", "origin": origen, "destination": destino,
        "month": mes, "year": anio, "version": 1, "currencyCode": MONEDA,
    }
    if outbound:
        params["outboundDate"] = outbound
    try:
        r = requests.get(API, params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json().get("dayPrices", [])
    except Exception as e:
        print(f"  ! error {origen}->{destino} {mes}/{anio}: {e}")
        return []


def en_rango(fstr):
    hoy = date.today()
    fin = date(HASTA[0], HASTA[1], 28) + timedelta(days=10)
    f = datetime.strptime(fstr, "%Y-%m-%d").date()
    return hoy <= f <= fin


# --------------------------- Recolección ----------------------------------
def recolectar_idas():
    """{fecha: precio_ida} de salidas EZE->BCN en todo el rango."""
    idas = {}
    for mes, anio in meses_rango(HASTA):
        for d in consultar(ORIGEN, DESTINO, mes, anio):
            if d.get("price") is None or not en_rango(d["date"]):
                continue
            idas[d["date"]] = d["price"]
        time.sleep(PAUSA)
    return idas


def combos_para_ida(ida_str, precio_ida):
    """
    Combinaciones (ida fija) con regreso en la ventana.
    total = precio_ida + precio_vuelta.
    """
    xd = datetime.strptime(ida_str, "%Y-%m-%d").date()
    dias = consultar(ORIGEN, DESTINO, xd.month, xd.year,
                     outbound=ida_str.replace("-", ""))
    combos = []
    for d in dias:
        if d.get("price") is None:
            continue
        rd = datetime.strptime(d["date"], "%Y-%m-%d").date()
        gap = (rd - xd).days
        if MIN_DIAS <= gap <= MAX_DIAS:
            precio_vuelta = d["price"]
            total = precio_ida + precio_vuelta          # <-- TOTAL
            combos.append({
                "ida": ida_str, "precio_ida": precio_ida,
                "vuelta": d["date"], "precio_vuelta": precio_vuelta,
                "dias": gap, "total": total,
            })
    return combos


def mejores_por(combos, campo):
    """Una combinación (la de menor total) por cada valor distinto de 'campo'."""
    best = {}
    for c in combos:
        k = c[campo]
        if k not in best or c["total"] < best[k]["total"]:
            best[k] = c
    return sorted(best.values(),
                  key=lambda c: (c["total"], c["ida"], c["vuelta"]))[:TOP]


def cargar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_estado(d):
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def variacion(actual, anterior):
    """Texto comparando el valor actual con el de la revisión anterior."""
    if anterior is None:
        return "primera medición (sin referencia previa)"
    dif = actual - anterior
    pct = (dif / anterior * 100) if anterior else 0
    if dif > 0:
        return f"SUBIÓ +{dif} {MONEDA} (+{pct:.1f}%)  [antes {anterior}]"
    if dif < 0:
        return f"BAJÓ {dif} {MONEDA} ({pct:.1f}%)  [antes {anterior}]"
    return f"sin cambios  [antes {anterior}]"


def enviar_telegram(texto, parse_mode=None):
    """Envía un mensaje a Telegram si están configuradas las variables."""
    token = os.environ.get("TG_TOKEN")
    chat = os.environ.get("TG_CHAT_ID")
    if not token or not chat:
        print("  (Telegram no configurado: faltan TG_TOKEN / TG_CHAT_ID)")
        return
    try:
        data = {"chat_id": chat, "text": texto}
        if parse_mode:
            data["parse_mode"] = parse_mode
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            timeout=15,
        )
        print("  Alerta enviada a Telegram.")
    except Exception as e:
        print("  ! Telegram falló:", e)


def link(ida, vuelta):
    return (f"https://www.flylevel.com/Flight/Select?triptype=RT"
            f"&o1={ORIGEN}&d1={DESTINO}&dd1={ida}&dd2={vuelta}"
            f"&ADT=1&CHD=0&INL=0&r=true&mm=false"
            f"&forcedCurrency={MONEDA}&forcedCulture=es-ES&newecom=true")


def imprimir_ranking(titulo, ranking):
    print("\n" + "=" * 64)
    print(titulo)
    print("=" * 64)
    for i, c in enumerate(ranking, 1):
        print(f"{i:2d}. Ida {c['ida']} ({c['precio_ida']} {MONEDA})  ->  "
              f"Vuelta {c['vuelta']} ({c['precio_vuelta']} {MONEDA})  "
              f"[{c['dias']} días]   TOTAL {c['total']} {MONEDA}")
        print(f"    {link(c['ida'], c['vuelta'])}")


# ------------------------------- Main -------------------------------------
def main():
    print(f"Analizando {ORIGEN} <-> {DESTINO} de hoy a {HASTA[1]:02d}/{HASTA[0]} "
          f"(máx {MAX_DIAS} días entre ida y vuelta)...\n")

    print(">> Barriendo idas...")
    idas = recolectar_idas()
    if not idas:
        print("Sin datos de ida.")
        return

    candidatos = sorted(idas.items(), key=lambda kv: kv[1])[:N_CANDIDATOS]
    print(f"   {len(idas)} fechas de ida; evaluando las {len(candidatos)} más baratas.\n")

    print(">> Armando combinaciones con sus regresos...")
    combos = []
    for ida, precio_ida in candidatos:
        combos.extend(combos_para_ida(ida, precio_ida))
        time.sleep(PAUSA)

    if not combos:
        print("No se encontraron combinaciones dentro de la ventana de días.")
        return

    # ---- Comparación con la revisión anterior ----
    actual = {
        "min_ida": min(idas.values()),
        "min_vuelta": min(c["precio_vuelta"] for c in combos),
        "min_total": min(c["total"] for c in combos),
    }
    prev = cargar_estado()
    print("\n" + "=" * 64)
    print("COMPARACIÓN CON LA REVISIÓN ANTERIOR")
    print("=" * 64)
    print(f"  Tramo IDA más barato:    {actual['min_ida']} {MONEDA}   "
          f"{variacion(actual['min_ida'], prev.get('min_ida'))}")
    print(f"  Tramo VUELTA más barato: {actual['min_vuelta']} {MONEDA}   "
          f"{variacion(actual['min_vuelta'], prev.get('min_vuelta'))}")
    print(f"  TOTAL más barato:        {actual['min_total']} {MONEDA}   "
          f"{variacion(actual['min_total'], prev.get('min_total'))}")

    hubo_cambio = any(
        prev.get(k) is not None and prev.get(k) != actual[k] for k in actual
    )
    actual["actualizado"] = datetime.now().isoformat(timespec="seconds")
    guardar_estado(actual)

    imprimir_ranking(f"RANKING A — {TOP} mejores combinaciones (vista por IDA)",
                     mejores_por(combos, "ida"))
    imprimir_ranking(f"RANKING B — {TOP} mejores combinaciones (vista por VUELTA)",
                     mejores_por(combos, "vuelta"))

    m = min(combos, key=lambda c: c["total"])
    print("\n" + "*" * 64)
    print(f"MÁS BARATA: ida {m['ida']} ({m['precio_ida']}) + "
          f"vuelta {m['vuelta']} ({m['precio_vuelta']}) "
          f"= {m['total']} {MONEDA}  [{m['dias']} días]")
    print("*" * 64)

    # ---- Alerta a Telegram (1ra corrida o cuando algo cambió) ----
    if not prev or hubo_cambio:
        encabezado = "PRIMERA MEDICIÓN" if not prev else "CAMBIO DE PRECIO"
        variacion_total = variacion(actual['min_total'], prev.get('min_total'))

        # Top 10 combinaciones únicas más baratas (sin repetir par ida/vuelta)
        top10 = sorted(
            {(c["ida"], c["vuelta"]): c for c in combos}.values(),
            key=lambda c: (c["total"], c["ida"])
        )[:10]

        lineas = []
        ordinal = ["1°","2°","3°","4°","5°","6°","7°","8°","9°","10°"]
        for i, c in enumerate(top10):
            url = link(c["ida"], c["vuelta"])
            # Link con texto legible (Telegram usa formato HTML)
            lineas.append(
                f'{ordinal[i]} {c["ida"]} → {c["vuelta"]} ({c["dias"]} días) '
                f'= ({c["precio_ida"]}+{c["precio_vuelta"]}) {c["total"]} {MONEDA}\n'
                f'<a href="{url}">Reservar</a>'
            )

        msg = (
            f"✈️ LEVEL {ORIGEN}-{DESTINO}  —  {encabezado}\n"
            f"Total más barato: {actual['min_total']} {MONEDA} ({variacion_total})\n\n"
            f"Mejores combinaciones:\n\n" +
            "\n\n".join(lineas)
        )
        enviar_telegram(msg, parse_mode="HTML")
    else:
        print("\nSin cambios respecto a la última revisión: no se envía alerta.")


if __name__ == "__main__":
    main()
