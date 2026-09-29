"""Actualiza data.json con los resultados de Meta Ads de Guatebee desde Windsor.ai.

Se ejecuta a diario con GitHub Actions. Necesita el secreto WINDSOR_API_KEY.
Reconstruye todos los meses desde START_MONTH hasta el mes de ayer (hora de Guatemala),
así las miniaturas de los anuncios se refrescan antes de caducar.
"""
import calendar
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

ACCOUNT_ID = "798050121603811"
START_MONTH = (2026, 8)
TZ = dt.timezone(dt.timedelta(hours=-6))  # America/Guatemala, sin horario de verano
BASE = "https://connectors.windsor.ai/facebook"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]
CONV = "actions_onsite_conversion_messaging_conversation_started_7d"
DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")


def fetch(fields, date_from, date_to):
    params = {
        "api_key": os.environ["WINDSOR_API_KEY"],
        "fields": ",".join(fields),
        "date_from": date_from,
        "date_to": date_to,
        "select_accounts": ACCOUNT_ID,
    }
    url = BASE + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as r:
        body = json.load(r)
    rows = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise RuntimeError(f"Respuesta inesperada de Windsor: {str(body)[:300]}")
    return rows


def n(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def money(v):
    return f"${v:,.2f}"


def pct(v):
    return f"{v*100:.1f}%"


def build_month(y, m, until):
    start = dt.date(y, m, 1)
    last = dt.date(y, m, calendar.monthrange(y, m)[1])
    end = min(last, until)
    df, dt_ = start.isoformat(), end.isoformat()
    partial = end < last

    tot_rows = fetch(["spend", "impressions", "reach", "clicks", "link_clicks", "ctr", "cpc", "cpm",
                      "frequency", "actions_lead", CONV, "actions_post_engagement"], df, dt_)
    t = tot_rows[0] if tot_rows else {}
    totals = {
        "spend": round(n(t.get("spend")), 2), "impressions": int(n(t.get("impressions"))),
        "reach": int(n(t.get("reach"))), "clicks": int(n(t.get("clicks"))),
        "link_clicks": int(n(t.get("link_clicks"))), "ctr": n(t.get("ctr")), "cpc": n(t.get("cpc")),
        "cpm": n(t.get("cpm")), "frequency": n(t.get("frequency")), "leads": int(n(t.get("actions_lead"))),
        "conversations": int(n(t.get(CONV))), "engagement": int(n(t.get("actions_post_engagement"))),
    }

    camps = []
    for r in fetch(["campaign", "campaign_objective", "spend", "impressions", "reach", "clicks", "ctr",
                    CONV, "actions_post_engagement"], df, dt_):
        if n(r.get("spend")) < 0.05:
            continue
        camps.append({
            "name": r.get("campaign"), "objective": r.get("campaign_objective"),
            "spend": round(n(r.get("spend")), 2), "impressions": int(n(r.get("impressions"))),
            "reach": int(n(r.get("reach"))), "clicks": int(n(r.get("clicks"))), "ctr": n(r.get("ctr")),
            "conversations": int(n(r.get(CONV))) if r.get(CONV) is not None else None,
            "engagement": int(n(r.get("actions_post_engagement"))),
        })

    daily = []
    for r in fetch(["date", "spend", "impressions", "clicks", CONV], df, dt_):
        daily.append({"date": r.get("date"), "spend": round(n(r.get("spend")), 2),
                      "impressions": int(n(r.get("impressions"))), "clicks": int(n(r.get("clicks"))),
                      "conversations": int(n(r.get(CONV)))})
    daily.sort(key=lambda d: d["date"])

    ads = []
    for r in fetch(["ad_name", "campaign", "object_type", "instagram_permalink_url", "thumbnail_url",
                    "spend", "impressions", "clicks", "ctr", CONV], df, dt_):
        if n(r.get("spend")) < 0.05:
            continue
        ads.append({
            "name": r.get("ad_name"), "campaign": r.get("campaign"),
            "spend": round(n(r.get("spend")), 2), "impressions": int(n(r.get("impressions"))),
            "clicks": int(n(r.get("clicks"))), "ctr": n(r.get("ctr")),
            "conversations": int(n(r.get(CONV))) if r.get(CONV) is not None else None,
            "format": "video" if (r.get("object_type") or "").upper() == "VIDEO" else "imagen",
            "permalink": r.get("instagram_permalink_url"), "thumbnail": r.get("thumbnail_url"),
        })
    ads.sort(key=lambda a: -a["spend"])
    if len(ads) > 10:
        rest = ads[9:]
        imp = sum(a["impressions"] for a in rest)
        clk = sum(a["clicks"] for a in rest)
        camps_rest = {a["campaign"] for a in rest}
        ads = ads[:9] + [{
            "name": f"Otros {len(rest)} anuncios", "campaign": camps_rest.pop() if len(camps_rest) == 1 else "Varias campañas",
            "spend": round(sum(a["spend"] for a in rest), 2), "impressions": imp, "clicks": clk,
            "ctr": clk / imp if imp else 0, "conversations": sum(a["conversations"] or 0 for a in rest),
            "format": None, "permalink": None, "thumbnail": None,
        }]

    return {
        "label": f"{MESES[m-1]} {y}", "partial": partial, "through": dt_ if partial else None,
        "totals": totals, "campaigns": camps, "ads": ads, "daily": daily,
    }


def cpc(t):
    return t["spend"] / t["conversations"] if t.get("conversations") else None


def insights(cur, prev):
    t = cur["totals"]
    c = cpc(t)
    wins, watch, nxt = [], [], []
    en_curso = f" (en curso, datos al {int(cur['through'][8:])})" if cur["partial"] else ""
    summary = f"{cur['label']}{en_curso}: {t['conversations']:,} conversaciones iniciadas con {money(t['spend'])} de inversión"
    if c is not None:
        summary += f", a {money(c)} por conversación"
    pc = cpc(prev["totals"]) if prev else None
    if c is not None and pc:
        ch = (c - pc) / pc
        summary += f" ({'mejor' if ch < 0 else 'peor'} que {prev['label'].split()[0].lower()}: {money(pc)})."
        if ch <= -0.05:
            wins.append(f"El costo por conversación bajó {abs(ch)*100:.0f}%: de {money(pc)} a {money(c)}.")
        elif ch >= 0.10:
            watch.append(f"El costo por conversación subió {ch*100:.0f}%: de {money(pc)} a {money(c)}.")
    else:
        summary += "."

    ranked = [x for x in cur["campaigns"] if (x.get("conversations") or 0) >= 5]
    ranked.sort(key=lambda x: x["spend"] / x["conversations"])
    if ranked:
        b = ranked[0]
        wins.append(f"“{b['name']}” es la campaña más eficiente: {money(b['spend']/b['conversations'])} por conversación ({b['conversations']} conversaciones).")
    good_ads = [a for a in cur["ads"] if a.get("permalink") and (a.get("conversations") or 0) >= 5]
    good_ads.sort(key=lambda a: a["spend"] / a["conversations"])
    if good_ads:
        a = good_ads[0]
        wins.append(f"El mejor anuncio es “{a['name']}”: {a['conversations']} conversaciones a {money(a['spend']/a['conversations'])} cada una.")

    worst = None
    if c:
        for x in sorted(cur["campaigns"], key=lambda x: -x["spend"]):
            conv = x.get("conversations") or 0
            cost = x["spend"] / conv if conv else None
            if x["spend"] >= 5 and (cost is None or cost >= 1.5 * c):
                worst = x
                watch.append(
                    f"“{x['name']}” gastó {money(x['spend'])} y generó {conv} conversaciones"
                    + (f" ({money(cost)} cada una, {cost/c:.1f} veces el promedio)." if cost else ".")
                )
                break
    if prev and prev["totals"]["ctr"] and t["ctr"]:
        d = (t["ctr"] - prev["totals"]["ctr"]) / prev["totals"]["ctr"]
        if d <= -0.10:
            watch.append(f"El CTR bajó de {pct(prev['totals']['ctr'])} a {pct(t['ctr'])}.")
        elif d >= 0.10:
            wins.append(f"El CTR subió de {pct(prev['totals']['ctr'])} a {pct(t['ctr'])}.")
    if t["frequency"] >= 2.5:
        watch.append(f"La frecuencia está en {t['frequency']:.1f}: la audiencia ve los mismos anuncios varias veces.")

    if worst and ranked and ranked[0]["name"] != worst["name"]:
        nxt.append(f"Mover presupuesto de “{worst['name']}” a “{ranked[0]['name']}”, o pausar sus anuncios de bajo rendimiento.")
    elif ranked:
        nxt.append(f"Escalar el presupuesto de “{ranked[0]['name']}” mientras mantenga el costo por conversación.")
    if t["frequency"] >= 2.5 or any("CTR bajó" in w for w in watch):
        nxt.append("Renovar creativos para bajar la frecuencia y recuperar el CTR.")
    if good_ads:
        nxt.append(f"Hacer nuevas piezas con el estilo de “{good_ads[0]['name']}”.")

    return {"summary": summary, "wins": wins[:3], "watch": watch[:3], "next": nxt[:3]}


def main():
    now = dt.datetime.now(TZ).date()
    until = now - dt.timedelta(days=1)
    try:
        data = json.load(open(DATA_PATH, encoding="utf-8"))
    except FileNotFoundError:
        data = {"account": {"name": "Guatebee", "currency": "USD", "platform": "Meta Ads"},
                "source": "Windsor.ai", "months": {}}

    y, m = START_MONTH
    keys = []
    while (y, m) <= (until.year, until.month):
        keys.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1

    months = {}
    prev = None
    for y, m in keys:
        k = f"{y}-{m:02d}"
        try:
            entry = build_month(y, m, until)
        except Exception as e:  # conserva el mes anterior si Windsor falla
            print(f"Error en {k}: {e}", file=sys.stderr)
            if k in data["months"]:
                months[k] = data["months"][k]
                prev = months[k]
                continue
            raise
        entry["insights"] = insights(entry, prev)
        months[k] = entry
        prev = entry

    data["months"] = months
    data["updated"] = now.isoformat()
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("Meses actualizados:", ", ".join(months))


if __name__ == "__main__":
    main()
