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
IG_ACCOUNT = "17841405733522791"
FB_PAGE = "1711394962462292"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]
CONV = "actions_onsite_conversion_messaging_conversation_started_7d"
DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")


def fetch(fields, date_from=None, date_to=None, connector="facebook", account=None, preset=None):
    params = {
        "api_key": os.environ["WINDSOR_API_KEY"],
        "fields": ",".join(fields),
        "select_accounts": account or ACCOUNT_ID,
    }
    if preset:
        params["date_preset"] = preset
    else:
        params["date_from"], params["date_to"] = date_from, date_to
    url = f"https://connectors.windsor.ai/{connector}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=120) as r:
        body = json.load(r)
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(str(body.get("error"))[:300])
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


# ---------------------------------------------------------------- Orgánico (Instagram)

def ig(fields, **kw):
    return fetch(fields, connector="instagram", account=IG_ACCOUNT, **kw)


def build_ig_month(y, m, until, new_followers):
    start = dt.date(y, m, 1)
    last = dt.date(y, m, calendar.monthrange(y, m)[1])
    end = min(last, until)
    df, dt_ = start.isoformat(), end.isoformat()
    partial = end < last

    daily = []
    for r in ig(["date", "reach", "views", "total_interactions"], date_from=df, date_to=dt_):
        d = r.get("date")
        daily.append({"date": d, "reach": int(n(r.get("reach"))), "views": int(n(r.get("views"))),
                      "interactions": int(n(r.get("total_interactions"))),
                      "new_followers": new_followers.get(d)})
    daily.sort(key=lambda x: x["date"])

    t = (ig(["accounts_engaged", "total_interactions", "likes", "comments", "saves", "shares", "views",
             "profile_links_taps"], date_from=df, date_to=dt_) or [{}])[0]
    nf_days = [x["new_followers"] for x in daily if x["new_followers"] is not None]
    reach_days = [x["reach"] for x in daily]

    posts = []
    for r in ig(["media_id", "timestamp", "media_type", "media_product_type", "media_caption", "media_permalink",
                 "media_url", "media_thumbnail_url", "media_reach", "media_views", "media_engagement",
                 "media_saved", "media_shares", "media_total_like_count", "media_total_comments_count"],
                date_from=df, date_to=dt_):
        ts = (r.get("timestamp") or "")[:10]
        if not ts.startswith(f"{y}-{m:02d}") or (r.get("media_product_type") or "").upper() in ("AD", "STORY"):
            continue
        cap = (r.get("media_caption") or "").strip().split("\n")[0]
        reach = int(n(r.get("media_reach")))
        eng = int(n(r.get("media_engagement")))
        mtype = (r.get("media_type") or "").upper()
        posts.append({
            "date": ts, "type": {"VIDEO": "reel", "REEL": "reel", "CAROUSEL_ALBUM": "carrusel"}.get(mtype, "imagen"),
            "caption": short(cap, 110), "permalink": r.get("media_permalink"),
            "image": r.get("media_thumbnail_url") or (r.get("media_url") if mtype != "VIDEO" else None),
            "reach": reach, "views": int(n(r.get("media_views"))), "engagement": eng,
            "likes": int(n(r.get("media_total_like_count"))), "comments": int(n(r.get("media_total_comments_count"))),
            "saves": int(n(r.get("media_saved"))), "shares": int(n(r.get("media_shares"))),
            "eng_rate": eng / reach if reach else 0,
        })
    posts.sort(key=lambda p: -p["reach"])
    pr = sum(p["reach"] for p in posts)
    totals = {
        "reach_avg": round(sum(reach_days) / len(reach_days)) if reach_days else 0,
        "reach_peak": max(reach_days) if reach_days else 0,
        "views": int(n(t.get("views"))), "interactions": int(n(t.get("total_interactions"))),
        "likes": int(n(t.get("likes"))), "comments": int(n(t.get("comments"))),
        "saves": int(n(t.get("saves"))), "shares": int(n(t.get("shares"))),
        "profile_taps": int(n(t.get("profile_links_taps"))),
        "new_followers": sum(nf_days) if nf_days else None, "new_followers_days": len(nf_days),
        "posts": len(posts), "post_eng_rate": (sum(p["engagement"] for p in posts) / pr) if pr else None,
    }
    return {"label": f"{MESES[m-1]} {y}", "partial": partial, "through": dt_ if partial else None,
            "days": len(daily), "totals": totals, "daily": daily, "posts": posts}


def short(txt, k):
    txt = (txt or "").rstrip("…")
    if len(txt) <= k:
        return txt
    return txt[:k].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def ig_insights(cur, prev):
    t = cur["totals"]
    wins, watch, nxt = [], [], []
    en_curso = f" (en curso, datos al {int(cur['through'][8:])})" if cur["partial"] else ""
    summary = (f"{cur['label']}{en_curso}: {t['posts']} publicaciones, alcance promedio de {t['reach_avg']:,} cuentas "
               f"al día y {t['interactions']:,} interacciones")
    if t["new_followers"] is not None and t["new_followers_days"] >= cur["days"] - 2:
        summary += f"; {t['new_followers']:,} seguidores nuevos"
    summary += "."
    if prev:
        p = prev["totals"]
        if p["reach_avg"]:
            ch = (t["reach_avg"] - p["reach_avg"]) / p["reach_avg"]
            if ch >= 0.10:
                wins.append(f"El alcance promedio diario subió {ch*100:.0f}% (de {p['reach_avg']:,} a {t['reach_avg']:,} cuentas).")
            elif ch <= -0.10:
                watch.append(f"El alcance promedio diario bajó {abs(ch)*100:.0f}% (de {p['reach_avg']:,} a {t['reach_avg']:,} cuentas).")
        pi = p["interactions"] / max(prev["days"], 1)
        ci = t["interactions"] / max(cur["days"], 1)
        if pi and (ci - pi) / pi >= 0.15:
            wins.append(f"Las interacciones por día subieron de {pi:.1f} a {ci:.1f}.")
        elif pi and (ci - pi) / pi <= -0.15:
            watch.append(f"Las interacciones por día bajaron de {pi:.1f} a {ci:.1f}.")
    peak = max(cur["daily"], key=lambda x: x["reach"]) if cur["daily"] else None
    if peak and t["reach_avg"] and peak["reach"] >= 2.5 * t["reach_avg"]:
        watch.append(f"El pico de alcance fue el {int(peak['date'][8:])} ({peak['reach']:,} cuentas). Si coincide con más pauta, parte de ese alcance viene de los anuncios.")
    if cur["posts"]:
        best = max(cur["posts"], key=lambda x: x["engagement"])
        wins.append(f"La publicación con más interacción: “{short(best['caption'], 60)}” ({best['engagement']} interacciones, {best['reach']:,} de alcance).")
        sv = sorted(cur["posts"], key=lambda x: -(x["saves"] + x["shares"]))[0]
        if sv["saves"] + sv["shares"] >= 5:
            nxt.append(f"Hacer más contenido como “{short(sv['caption'], 50)}”: fue la más guardada y compartida ({sv['saves']} guardados, {sv['shares']} compartidos).")
    if t["post_eng_rate"] is not None and t["post_eng_rate"] < 0.02:
        watch.append(f"La tasa de interacción por publicación es de {t['post_eng_rate']*100:.1f}%: la gente ve el contenido pero interactúa poco.")
        nxt.append("Agregar preguntas, encuestas o llamados a guardar/compartir en los textos para subir la interacción.")
    if t["posts"] and cur["days"] and t["posts"] / cur["days"] * 7 < 3:
        nxt.append(f"Subir la frecuencia de publicación: van {t['posts']} publicaciones en {cur['days']} días.")
    if not any("reel" == p_["type"] for p_ in cur["posts"]):
        nxt.append("Probar reels: este mes todas las publicaciones fueron imágenes, y los reels suelen alcanzar a más gente nueva.")
    return {"summary": summary, "wins": wins[:3], "watch": watch[:3], "next": nxt[:3]}


# ---------------------------------------------------------------- Orgánico (Facebook)

def fbo(fields, **kw):
    return fetch(fields, connector="facebook_organic", account=FB_PAGE, **kw)


def build_fb_month(y, m, until):
    start = dt.date(y, m, 1)
    last = dt.date(y, m, calendar.monthrange(y, m)[1])
    end = min(last, until)
    df, dt_ = start.isoformat(), end.isoformat()
    partial = end < last
    daily = []
    for r in fbo(["date", "page_follows", "page_daily_follows_unique", "page_daily_unfollows_unique",
                  "page_impressions_unique", "page_post_engagements", "page_views_total"], date_from=df, date_to=dt_):
        daily.append({"date": r.get("date"), "followers": int(n(r.get("page_follows"))),
                      "new_followers": int(n(r.get("page_daily_follows_unique"))),
                      "unfollows": int(n(r.get("page_daily_unfollows_unique"))),
                      "reach": int(n(r.get("page_impressions_unique"))),
                      "engagements": int(n(r.get("page_post_engagements"))),
                      "page_views": int(n(r.get("page_views_total")))})
    daily.sort(key=lambda x: x["date"])
    posts = []
    for r in fbo(["post_id", "post_created_time", "post_message_oneline", "permalink_url", "full_picture",
                  "post_impressions_unique", "post_engagements", "post_reactions_total", "post_comments_total",
                  "post_clicks"], date_from=df, date_to=dt_):
        ts = (r.get("post_created_time") or "")[:10]
        if not ts.startswith(f"{y}-{m:02d}"):
            continue
        reach = int(n(r.get("post_impressions_unique")))
        eng = int(n(r.get("post_engagements")))
        posts.append({"date": ts, "type": "post", "caption": short(r.get("post_message_oneline"), 110),
                      "permalink": r.get("permalink_url"), "image": r.get("full_picture"), "reach": reach,
                      "engagement": eng, "reactions": int(n(r.get("post_reactions_total"))),
                      "comments": int(n(r.get("post_comments_total"))), "clicks": int(n(r.get("post_clicks"))),
                      "eng_rate": eng / reach if reach else 0})
    posts.sort(key=lambda p: -p["reach"])
    reach_days = [x["reach"] for x in daily]
    pr = sum(p["reach"] for p in posts)
    fol = [x["followers"] for x in daily if x["followers"]]
    totals = {
        "followers": fol[-1] if fol else None, "followers_start": fol[0] if fol else None,
        "new_followers": sum(x["new_followers"] for x in daily), "unfollows": sum(x["unfollows"] for x in daily),
        "reach_avg": round(sum(reach_days) / len(reach_days)) if reach_days else 0,
        "reach_peak": max(reach_days) if reach_days else 0,
        "engagements": sum(x["engagements"] for x in daily), "page_views": sum(x["page_views"] for x in daily),
        "posts": len(posts), "post_clicks": sum(p["clicks"] for p in posts),
        "post_reach_avg": round(pr / len(posts)) if posts else 0,
        "post_eng_rate": (sum(p["engagement"] for p in posts) / pr) if pr else None,
    }
    return {"label": f"{MESES[m-1]} {y}", "partial": partial, "through": dt_ if partial else None,
            "days": len(daily), "totals": totals, "daily": daily, "posts": posts}


def fb_insights(cur, prev):
    t = cur["totals"]
    wins, watch, nxt = [], [], []
    en_curso = f" (en curso, datos al {int(cur['through'][8:])})" if cur["partial"] else ""
    net = t["new_followers"] - t["unfollows"]
    summary = (f"{cur['label']}{en_curso}: la página ganó {net:+,} seguidores netos ({t['new_followers']} nuevos, "
               f"{t['unfollows']} dejaron de seguir), con {t['posts']} publicaciones y un alcance promedio de "
               f"{t['reach_avg']:,} personas al día.")
    if prev:
        p = prev["totals"]
        rn = t["new_followers"] / max(cur["days"], 1)
        rp = p["new_followers"] / max(prev["days"], 1)
        if rp and (rn - rp) / rp >= 0.15:
            wins.append(f"Los seguidores nuevos por día subieron de {rp:.1f} a {rn:.1f}.")
        elif rp and (rn - rp) / rp <= -0.15:
            watch.append(f"Los seguidores nuevos por día bajaron de {rp:.1f} a {rn:.1f}.")
        if p["post_reach_avg"]:
            ch = (t["post_reach_avg"] - p["post_reach_avg"]) / p["post_reach_avg"]
            if ch >= 0.10:
                wins.append(f"Cada publicación alcanzó en promedio {t['post_reach_avg']:,} personas ({ch*100:.0f}% más que el mes anterior).")
            elif ch <= -0.10:
                watch.append(f"El alcance promedio por publicación bajó {abs(ch)*100:.0f}%: de {p['post_reach_avg']:,} a {t['post_reach_avg']:,} personas.")
    if cur["posts"]:
        best = max(cur["posts"], key=lambda x: (x["engagement"] + x["clicks"], x["reach"]))
        wins.append(f"La publicación que más movió: “{short(best['caption'], 60)}” ({best['reach']:,} de alcance, {best['clicks']} clics).")
    if t["post_eng_rate"] is not None and t["post_eng_rate"] < 0.01:
        watch.append(f"Las publicaciones tienen poca interacción ({t['post_eng_rate']*100:.1f}% del alcance). La interacción de la página viene sobre todo de los anuncios.")
        nxt.append("Probar publicaciones con pregunta directa, encuestas o videos cortos para generar comentarios.")
    peak = max(cur["daily"], key=lambda x: x["reach"]) if cur["daily"] else None
    if peak and t["reach_avg"] and peak["reach"] >= 2 * t["reach_avg"]:
        watch.append(f"El pico de alcance fue el {int(peak['date'][8:])} ({peak['reach']:,} personas); el alcance de la página incluye lo que traen los anuncios.")
    recs = [x for x in cur["posts"] if "recuerdo" in (x["caption"] or "").lower() or "invitados" in (x["caption"] or "").lower()]
    if recs and max(recs, key=lambda x: x["clicks"])["clicks"] >= 10:
        r = max(recs, key=lambda x: x["clicks"])
        nxt.append(f"Repetir contenido de recuerdos para eventos: “{short(r['caption'], 45)}” generó {r['clicks']} clics.")
    if t["posts"] and cur["days"] and t["posts"] / cur["days"] * 7 < 3:
        nxt.append(f"Publicar más seguido: van {t['posts']} publicaciones en {cur['days']} días.")
    return {"summary": summary, "wins": wins[:3], "watch": watch[:3], "next": nxt[:3]}


def update_organic(data, keys, now, until):
    org = data.get("organic") or {}
    igd = org.get("instagram") or {}
    history = igd.get("follower_history", {})
    new_f = igd.get("new_followers", {})
    try:
        info = ig(["followers_count", "media_count", "username"], preset="last_7d")
        if info:
            igd["username"] = info[0].get("username")
            igd["followers"] = int(n(info[0].get("followers_count")))
            igd["media_count"] = int(n(info[0].get("media_count")))
            history[now.isoformat()] = igd["followers"]
        for r in ig(["date", "follower_count_1d"], preset="last_30d"):
            if r.get("date") and r.get("date") <= until.isoformat():
                new_f[r["date"]] = int(n(r.get("follower_count_1d")))
    except Exception as e:
        print(f"Instagram (perfil/seguidores): {e}", file=sys.stderr)
    igd["follower_history"], igd["new_followers"], igd["as_of"] = history, new_f, now.isoformat()

    months, prev = {}, None
    old = org.get("months", {})
    for y, m in keys:
        k = f"{y}-{m:02d}"
        try:
            e = build_ig_month(y, m, until, new_f)
            e["insights"] = ig_insights(e, prev)
        except Exception as ex:
            print(f"Instagram {k}: {ex}", file=sys.stderr)
            if k not in old:
                continue
            e = old[k]
        months[k] = e
        prev = e
    fb_months, prev = {}, None
    old_fb = org.get("fb_months", {})
    fbd = org.get("facebook") or {}
    for y, m in keys:
        k = f"{y}-{m:02d}"
        try:
            e = build_fb_month(y, m, until)
            e["insights"] = fb_insights(e, prev)
        except Exception as ex:
            print(f"Facebook {k}: {ex}", file=sys.stderr)
            if k not in old_fb:
                continue
            e = old_fb[k]
        fb_months[k] = e
        prev = e
        if e["totals"].get("followers"):
            fbd["followers"], fbd["as_of"] = e["totals"]["followers"], e["daily"][-1]["date"] if e["daily"] else None
    data["organic"] = {"instagram": igd, "months": months, "facebook": fbd, "fb_months": fb_months}


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
    update_organic(data, keys, now, until)
    data["updated"] = now.isoformat()
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("Meses actualizados:", ", ".join(months))


if __name__ == "__main__":
    main()
