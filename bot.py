"""
BetPoisson Bot v2 — Flask sincrono + Telegram via requests diretti
"""

import os
import json
import logging
import requests
import threading
import time
import calendar
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]
SECRET_KEY = os.environ.get("BETPOISSON_SECRET", "betpoisson2025")
TG_API     = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)
DATA_FILE = Path("data/bets.json")
DATA_FILE.parent.mkdir(exist_ok=True)

# ─── Telegram helpers (sincroni via requests) ────────────────────────────────
def tg_send(text):
    try:
        requests.post(f"{TG_API}/sendMessage", json={
            "chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"
        }, timeout=15)
    except Exception as e:
        log.error(f"tg_send error: {e}")

def tg_send_document(pdf_bytes, filename, caption):
    try:
        requests.post(f"{TG_API}/sendDocument", 
            data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"document": (filename, pdf_bytes, "application/pdf")},
            timeout=30
        )
    except Exception as e:
        log.error(f"tg_send_document error: {e}")

# ─── Storage ─────────────────────────────────────────────────────────────────
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"bets": [], "bankroll": 0, "initialBankroll": 0}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─── Messaggi Telegram ────────────────────────────────────────────────────────
def fmt_bet(bet, data):
    m = bet.get("match", {})
    edge = bet.get("edge")
    edge_line = f"\n{'📈' if edge > 0 else '⚠️' if edge > -5 else '📉'} *Edge:* {'+' if edge > 0 else ''}{edge}%" if edge is not None else ""
    notes_line = f"\n📝 _{bet.get('notes')}_" if bet.get("notes") else ""
    pwin = round(bet.get("stake", 0) * bet.get("bookOdds", 1), 2)
    profit = round(pwin - bet.get("stake", 0), 2)
    return (
        f"🎯 *Nuova Schedina*\n\n"
        f"{m.get('countryFlag','⚽')} *{m.get('home','?')} vs {m.get('away','?')}*\n"
        f"📅 {m.get('date','')} · {m.get('time','')} · {m.get('league','')}\n\n"
        f"📌 *Esito:* `{bet.get('selection','?')}`\n"
        f"📊 *Quota:* {bet.get('bookOdds', 0)}\n"
        f"💶 *Puntata:* €{bet.get('stake', 0):.2f}\n"
        f"🏆 *Vincita pot.:* €{pwin:.2f} (+€{profit:.2f})"
        f"{edge_line}{notes_line}\n"
        f"💰 Cassa: *€{data.get('bankroll', 0):.2f}*"
    )

def fmt_result(bet, data):
    m = bet.get("match", {})
    result = bet.get("result", "")
    stake = bet.get("stake", 0)
    odds  = bet.get("bookOdds", 1)
    if result == "won":
        profit = round(stake * odds - stake, 2)
        line = f"Vinta! +€{profit:.2f}"; emoji = "✅"
    elif result == "lost":
        line = f"Persa — -€{stake:.2f}"; emoji = "❌"
    elif result == "void":
        line = f"Rimborsata — €{stake:.2f}"; emoji = "↩️"
    else:
        return ""
    return (
        f"{emoji} *Risultato Schedina*\n\n"
        f"{m.get('countryFlag','⚽')} *{m.get('home','?')} vs {m.get('away','?')}*\n"
        f"📌 `{bet.get('selection','?')}` — *{line}*\n\n"
        f"💰 Cassa: *€{data.get('bankroll', 0):.2f}*"
    )

# ─── PDF mensile ─────────────────────────────────────────────────────────────
def generate_pdf(year, month, data):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    import io

    month_names = ["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
                   "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
    month_str = f"{year}-{month:02d}"
    bets = [b for b in data.get("bets", [])
            if b.get("match", {}).get("date", "").startswith(month_str)
            or b.get("createdAt", "").startswith(month_str)]

    won   = [b for b in bets if b.get("result") == "won"]
    lost  = [b for b in bets if b.get("result") == "lost"]
    pend  = [b for b in bets if b.get("result") == "pending"]
    staked = sum(b.get("stake", 0) for b in bets)
    pwin_tot = sum(b.get("stake",0)*b.get("bookOdds",1) for b in won)
    profit = pwin_tot - staked
    roi    = (profit / staked * 100) if staked > 0 else 0
    wr     = (len(won) / len(won+lost) * 100) if (won or lost) else 0

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    accent = colors.HexColor("#C8553D")
    dark   = colors.HexColor("#1A1A1A")
    muted  = colors.HexColor("#9E9891")
    green  = colors.HexColor("#2D8A56")
    red_c  = colors.HexColor("#C8553D")

    elems = []
    elems.append(Paragraph("BetPoisson", ParagraphStyle("T", parent=styles["Title"], textColor=dark, fontSize=24, fontName="Helvetica-Bold")))
    elems.append(Paragraph(f"Report Mensile — {month_names[month]} {year}", ParagraphStyle("S", parent=styles["Normal"], textColor=muted, fontSize=11, spaceAfter=12)))
    elems.append(HRFlowable(width="100%", thickness=1, color=accent, spaceAfter=14))

    kpi = [
        ["Schedine", "Vinte", "Perse", "In Attesa"],
        [str(len(bets)), str(len(won)), str(len(lost)), str(len(pend))],
        ["Puntato", "Profitto", "ROI", "Win Rate"],
        [f"€{staked:.2f}", f"{'+'if profit>=0 else ''}€{profit:.2f}", f"{'+'if roi>=0 else ''}{roi:.1f}%", f"{wr:.0f}%"],
    ]
    kt = Table(kpi, colWidths=[4.2*cm]*4)
    kt.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#F5F2ED")),
        ("BACKGROUND",(0,2),(-1,2),colors.HexColor("#F5F2ED")),
        ("FONTNAME",(0,1),(-1,1),"Helvetica-Bold"), ("FONTNAME",(0,3),(-1,3),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),9), ("FONTSIZE",(0,1),(-1,1),15), ("FONTSIZE",(0,3),(-1,3),13),
        ("TEXTCOLOR",(1,1),(1,1),green), ("TEXTCOLOR",(2,1),(2,1),red_c),
        ("TEXTCOLOR",(1,3),(1,3),green if profit>=0 else red_c),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("GRID",(0,0),(-1,-1),0.5,colors.HexColor("#E8E4DD")),
        ("TOPPADDING",(0,0),(-1,-1),7), ("BOTTOMPADDING",(0,0),(-1,-1),7),
    ]))
    elems.append(kt)
    elems.append(Spacer(1, 14))

    if bets:
        elems.append(Paragraph("Dettaglio Giocate", ParagraphStyle("H2", parent=styles["Normal"], textColor=dark, fontSize=13, fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=6)))
        cell_s = ParagraphStyle("C", parent=styles["Normal"], textColor=dark, fontSize=8)
        rows = [["Data","Partita","Esito","Quota","Puntata","Risultato"]]
        for b in sorted(bets, key=lambda x: x.get("match",{}).get("date",""), reverse=True):
            mm = b.get("match",{})
            res = b.get("result","pending")
            rm  = {"won":"✓ Vinta","lost":"✗ Persa","void":"↩","pending":"⏳"}
            s_  = b.get("stake",0); o_ = b.get("bookOdds",1)
            pf  = round(s_*o_-s_,2) if res=="won" else (-s_ if res=="lost" else 0)
            pfs = f"+€{pf:.2f}" if pf>=0 else f"€{pf:.2f}"
            rows.append([
                mm.get("date","")[-5:],
                Paragraph(f"{mm.get('home','?')} v {mm.get('away','?')}", cell_s),
                Paragraph(b.get("selection","?"), cell_s),
                f"{o_:.2f}", f"€{s_:.2f}",
                f"{rm.get(res,'?')} {pfs}" if res!="pending" else "⏳",
            ])
        dt = Table(rows, colWidths=[1.5*cm,5.5*cm,3.5*cm,1.8*cm,1.8*cm,2.9*cm], repeatRows=1)
        dt.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#1A1A1A")),
            ("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),8),
            ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#FAF8F5")]),
            ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#E8E4DD")),
            ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        elems.append(dt)

    elems.append(Spacer(1,20))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=muted))
    elems.append(Spacer(1,5))
    elems.append(Paragraph(f"Generato il {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')} · BetPoisson v2",
        ParagraphStyle("F", parent=styles["Normal"], textColor=muted, fontSize=8, alignment=1)))

    doc.build(elems)
    buf.seek(0)
    return buf.read()

# ─── Invia report mensile ─────────────────────────────────────────────────────
def send_monthly_report(year, month):
    month_names = ["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
                   "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
    data = load_data()
    log.info(f"Generazione report {month}/{year}...")

    month_str = f"{year}-{month:02d}"
    bets   = [b for b in data.get("bets",[]) if b.get("match",{}).get("date","").startswith(month_str) or b.get("createdAt","").startswith(month_str)]
    won    = [b for b in bets if b.get("result")=="won"]
    lost   = [b for b in bets if b.get("result")=="lost"]
    staked = sum(b.get("stake",0) for b in bets)
    pwin   = sum(b.get("stake",0)*b.get("bookOdds",1) for b in won)
    profit = pwin - staked
    roi    = (profit/staked*100) if staked>0 else 0

    caption = (
        f"📊 *Report {month_names[month]} {year}*\n\n"
        f"📋 {len(bets)} schedine · {len(won)}✅ {len(lost)}❌\n"
        f"💶 Puntato: €{staked:.2f}\n"
        f"{'📈' if profit>=0 else '📉'} P/L: *{'+'if profit>=0 else ''}€{profit:.2f}* (ROI {'+' if roi>=0 else ''}{roi:.1f}%)\n"
        f"💰 Cassa: *€{data.get('bankroll',0):.2f}*"
    )

    try:
        pdf = generate_pdf(year, month, data)
        tg_send_document(pdf, f"BetPoisson_{month_names[month]}_{year}.pdf", caption)
        log.info(f"Report PDF {month}/{year} inviato.")
    except Exception as e:
        import traceback
        log.error(f"PDF fallito: {e}\n{traceback.format_exc()}")
        # Fallback: manda solo testo
        tg_send(caption + f"\n\n⚠️ _PDF non disponibile: {str(e)[:80]}_")
        log.info(f"Report testuale {month}/{year} inviato.")

# ─── Endpoints Flask ─────────────────────────────────────────────────────────
def check_secret():
    return request.headers.get("X-Secret","") == SECRET_KEY

@app.route("/health")
def health():
    return jsonify({"status":"ok","time":datetime.now(timezone.utc).isoformat()})

@app.route("/api/bet", methods=["POST"])
def receive_bet():
    if not check_secret(): return jsonify({"error":"unauthorized"}),401
    payload = request.json or {}
    data = load_data()
    action = payload.get("action","new")
    bet    = payload.get("bet",{})

    if action == "new":
        existing = next((b for b in data["bets"] if str(b.get("id"))==str(bet.get("id"))),None)
        if existing: existing.update(bet)
        else: data["bets"].insert(0, bet)
        if "bankroll" in payload: data["bankroll"] = payload["bankroll"]
        save_data(data)
        threading.Thread(target=tg_send, args=(fmt_bet(bet, data),), daemon=True).start()
        return jsonify({"ok":True})

    elif action == "result":
        existing = next((b for b in data["bets"] if str(b.get("id"))==str(bet.get("id"))),None)
        if existing: existing.update(bet)
        if "bankroll" in payload: data["bankroll"] = payload["bankroll"]
        save_data(data)
        msg = fmt_result(bet, data)
        if msg: threading.Thread(target=tg_send, args=(msg,), daemon=True).start()
        return jsonify({"ok":True})

    return jsonify({"error":"unknown action"}),400

@app.route("/api/sync", methods=["POST"])
def sync():
    if not check_secret(): return jsonify({"error":"unauthorized"}),401
    save_data(request.json or {})
    return jsonify({"ok":True})

@app.route("/api/report", methods=["POST"])
def manual_report():
    if not check_secret(): return jsonify({"error":"unauthorized"}),401
    payload = request.json or {}
    now = datetime.now(timezone.utc)
    year  = payload.get("year",  now.year)
    month = payload.get("month", now.month)
    threading.Thread(target=send_monthly_report, args=(year,month), daemon=True).start()
    return jsonify({"ok":True, "message":f"Report {month}/{year} in elaborazione..."})

# ─── Scheduler report automatico ─────────────────────────────────────────────
def scheduler():
    sent_this_month = set()
    while True:
        now = datetime.now(timezone.utc)
        last_day = calendar.monthrange(now.year, now.month)[1]
        key = f"{now.year}-{now.month}"
        if now.day == last_day and now.hour == 20 and key not in sent_this_month:
            sent_this_month.add(key)
            send_monthly_report(now.year, now.month)
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=scheduler, daemon=True).start()
    log.info("BetPoisson Bot v2 avviato ✅")
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
