"""
BetPoisson Bot — Telegram + Railway
Notifiche schedine e report PDF mensile
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, jsonify
from telegram import Bot
from telegram.constants import ParseMode
import schedule
import threading
import time

# ─── Setup ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID    = os.environ["TELEGRAM_CHAT_ID"]
SECRET_KEY = os.environ.get("BETPOISSON_SECRET", "betpoisson2025")

bot = Bot(token=BOT_TOKEN)
app = Flask(__name__)

DATA_FILE = Path("data/bets.json")
DATA_FILE.parent.mkdir(exist_ok=True)

# ─── Storage helpers ─────────────────────────────────────────────────────────
def load_data():
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"bets": [], "bankroll": 0, "initialBankroll": 0}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─── Formattazione messaggi Telegram ─────────────────────────────────────────
def fmt_bet_message(bet: dict, data: dict) -> str:
    m = bet.get("match", {})
    flag   = m.get("countryFlag", "⚽")
    home   = m.get("home", "?")
    away   = m.get("away", "?")
    league = m.get("league", "")
    time_  = m.get("time", "")
    date_  = m.get("date", "")

    market   = bet.get("selection", "?")
    book_odd = bet.get("bookOdds", 0)
    stake    = bet.get("stake", 0)
    edge     = bet.get("edge")
    notes    = bet.get("notes", "")
    pwin     = round(stake * book_odd, 2)
    profit   = round(pwin - stake, 2)

    edge_line = ""
    if edge is not None:
        emoji = "📈" if edge > 0 else "⚠️" if edge > -5 else "📉"
        edge_line = f"\n{emoji} *Edge:* {'+' if edge > 0 else ''}{edge}%"

    notes_line = f"\n📝 _{notes}_" if notes else ""

    bankroll_line = f"\n💰 Cassa attuale: *€{data.get('bankroll', 0):.2f}*"

    return (
        f"🎯 *Nuova Schedina Salvata*\n\n"
        f"{flag} *{home} vs {away}*\n"
        f"📅 {date_} · {time_} · {league}\n\n"
        f"📌 *Esito:* `{market}`\n"
        f"📊 *Quota:* {book_odd}\n"
        f"💶 *Puntata:* €{stake:.2f}\n"
        f"🏆 *Vincita pot.:* €{pwin:.2f} (+€{profit:.2f})"
        f"{edge_line}"
        f"{notes_line}"
        f"{bankroll_line}"
    )

def fmt_result_message(bet: dict, data: dict) -> str:
    m = bet.get("match", {})
    flag = m.get("countryFlag", "⚽")
    home = m.get("home", "?")
    away = m.get("away", "?")
    result = bet.get("result", "pending")
    stake  = bet.get("stake", 0)
    book_odd = bet.get("bookOdds", 1)
    market = bet.get("selection", "?")

    if result == "won":
        payout = round(stake * book_odd, 2)
        profit = round(payout - stake, 2)
        emoji  = "✅"
        res_line = f"Vinta! +€{profit:.2f}"
    elif result == "lost":
        emoji  = "❌"
        res_line = f"Persa — -€{stake:.2f}"
    elif result == "void":
        emoji  = "↩️"
        res_line = f"Rimborsata — €{stake:.2f} restituiti"
    else:
        return ""

    bankroll = data.get("bankroll", 0)
    return (
        f"{emoji} *Risultato Schedina*\n\n"
        f"{flag} *{home} vs {away}*\n"
        f"📌 `{market}` — *{res_line}*\n\n"
        f"💰 Cassa aggiornata: *€{bankroll:.2f}*"
    )

# ─── Genera PDF report mensile ────────────────────────────────────────────────
def generate_monthly_pdf(year: int, month: int, data: dict) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.graphics.shapes import Drawing, Rect, String
        from reportlab.graphics import renderPDF
    except ImportError as e:
        raise RuntimeError(f"reportlab non disponibile: {e}")
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    # Filtra schedine del mese
    month_str = f"{year}-{month:02d}"
    bets = [b for b in data.get("bets", [])
            if b.get("match", {}).get("date", "").startswith(month_str)
            or b.get("createdAt", "").startswith(month_str)]

    styles = getSampleStyleSheet()
    accent = colors.HexColor("#C8553D")
    dark   = colors.HexColor("#1A1A1A")
    muted  = colors.HexColor("#9E9891")
    green  = colors.HexColor("#2D8A56")
    red_c  = colors.HexColor("#C8553D")

    title_style = ParagraphStyle("Title", parent=styles["Title"],
                                  textColor=dark, fontSize=24, spaceAfter=4,
                                  fontName="Helvetica-Bold")
    sub_style   = ParagraphStyle("Sub", parent=styles["Normal"],
                                  textColor=muted, fontSize=11, spaceAfter=16)
    h2_style    = ParagraphStyle("H2", parent=styles["Normal"],
                                  textColor=dark, fontSize=13, fontName="Helvetica-Bold",
                                  spaceBefore=16, spaceAfter=8)
    cell_style  = ParagraphStyle("Cell", parent=styles["Normal"],
                                  textColor=dark, fontSize=9)

    month_names = ["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
                   "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
    month_label = f"{month_names[month]} {year}"

    # Calcola statistiche
    won   = [b for b in bets if b.get("result") == "won"]
    lost  = [b for b in bets if b.get("result") == "lost"]
    pend  = [b for b in bets if b.get("result") == "pending"]
    void  = [b for b in bets if b.get("result") == "void"]
    settled = won + lost

    total_staked = sum(b.get("stake", 0) for b in bets)
    total_won    = sum(b.get("stake", 0) * b.get("bookOdds", 1) for b in won)
    total_void   = sum(b.get("stake", 0) for b in void)
    profit       = total_won - total_staked + total_void
    roi          = (profit / total_staked * 100) if total_staked > 0 else 0
    win_rate     = (len(won) / len(settled) * 100) if settled else 0

    elements = []

    # Header
    elements.append(Paragraph("BetPoisson", title_style))
    elements.append(Paragraph(f"Report Mensile — {month_label}", sub_style))
    elements.append(HRFlowable(width="100%", thickness=1, color=accent, spaceAfter=16))

    # KPI boxes
    elements.append(Paragraph("Riepilogo", h2_style))
    kpi_data = [
        ["Schedine Totali", "Vinte", "Perse", "In Attesa"],
        [str(len(bets)), str(len(won)), str(len(lost)), str(len(pend))],
        ["Puntato", "Profitto/Perdita", "ROI", "Win Rate"],
        [f"€{total_staked:.2f}",
         f"{'+'if profit>=0 else ''}€{profit:.2f}",
         f"{'+'if roi>=0 else ''}{roi:.1f}%",
         f"{win_rate:.0f}%"],
    ]
    kpi_table = Table(kpi_data, colWidths=[4.2*cm]*4)
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#F5F2ED")),
        ("BACKGROUND",  (0,2), (-1,2), colors.HexColor("#F5F2ED")),
        ("TEXTCOLOR",   (0,0), (-1,0), muted),
        ("TEXTCOLOR",   (0,2), (-1,2), muted),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica"),
        ("FONTNAME",    (0,2), (-1,2), "Helvetica"),
        ("FONTSIZE",    (0,0), (-1,-1), 9),
        ("FONTNAME",    (0,1), (-1,1), "Helvetica-Bold"),
        ("FONTNAME",    (0,3), (-1,3), "Helvetica-Bold"),
        ("FONTSIZE",    (0,1), (-1,1), 16),
        ("FONTSIZE",    (0,3), (-1,3), 14),
        ("TEXTCOLOR",   (1,1), (1,1), green),   # vinte verde
        ("TEXTCOLOR",   (2,1), (2,1), red_c),   # perse rosso
        ("TEXTCOLOR",   (1,3), (1,3), green if profit >= 0 else red_c),
        ("TEXTCOLOR",   (2,3), (2,3), green if roi >= 0 else red_c),
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.white]),
        ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#E8E4DD")),
        ("ROUNDEDCORNERS", [4]),
        ("TOPPADDING",  (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0), (-1,-1), 8),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 16))

    # Dettaglio schedine
    if bets:
        elements.append(Paragraph("Dettaglio Giocate", h2_style))
        headers = ["Data", "Partita", "Esito", "Quota", "Puntata", "Risultato"]
        rows = [headers]
        for b in sorted(bets, key=lambda x: x.get("match",{}).get("date",""), reverse=True):
            m   = b.get("match", {})
            res = b.get("result", "pending")
            res_map = {"won":"✓ Vinta","lost":"✗ Persa","void":"↩ Rimb.","pending":"⏳"}
            stake_v  = b.get("stake", 0)
            odds_v   = b.get("bookOdds", 0)
            profit_v = round(stake_v * odds_v - stake_v, 2) if res == "won" else (-stake_v if res == "lost" else 0)
            profit_s = f"+€{profit_v:.2f}" if profit_v >= 0 else f"€{profit_v:.2f}"

            rows.append([
                m.get("date","")[-5:],  # GG-MM
                Paragraph(f"{m.get('home','?')} v {m.get('away','?')}", cell_style),
                Paragraph(b.get("selection","?"), cell_style),
                f"{odds_v:.2f}",
                f"€{stake_v:.2f}",
                f"{res_map.get(res,'?')} {profit_s}" if res != "pending" else "⏳",
            ])

        col_w = [1.5*cm, 5.5*cm, 3.5*cm, 1.8*cm, 1.8*cm, 2.9*cm]
        det_table = Table(rows, colWidths=col_w, repeatRows=1)
        det_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#1A1A1A")),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 8),
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#FAF8F5")]),
            ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#E8E4DD")),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]))
        elements.append(det_table)

    # Footer
    elements.append(Spacer(1, 24))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=muted))
    elements.append(Spacer(1, 6))
    gen_date = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    elements.append(Paragraph(
        f"Generato il {gen_date} · BetPoisson v2",
        ParagraphStyle("Footer", parent=styles["Normal"], textColor=muted, fontSize=8, alignment=1)
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.read()

# ─── Endpoint: nuova schedina dall'app ───────────────────────────────────────
@app.route("/api/bet", methods=["POST"])
def receive_bet():
    auth = request.headers.get("X-Secret", "")
    if auth != SECRET_KEY:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.json
    if not payload:
        return jsonify({"error": "empty payload"}), 400

    data = load_data()

    action = payload.get("action", "new")  # "new" | "result"
    bet    = payload.get("bet", {})

    if action == "new":
        # Aggiunge o aggiorna la schedina
        existing = next((b for b in data["bets"] if str(b.get("id")) == str(bet.get("id"))), None)
        if existing:
            existing.update(bet)
        else:
            data["bets"].insert(0, bet)

        # Aggiorna cassa se fornita
        if "bankroll" in payload:
            data["bankroll"] = payload["bankroll"]

        save_data(data)

        # Invia notifica Telegram
        msg = fmt_bet_message(bet, data)
        asyncio.run(bot.send_message(
            chat_id=CHAT_ID,
            text=msg,
            parse_mode=ParseMode.MARKDOWN
        ))
        log.info(f"Notifica inviata per schedina {bet.get('id')}")
        return jsonify({"ok": True})

    elif action == "result":
        # Aggiorna risultato schedina
        bid = str(bet.get("id"))
        existing = next((b for b in data["bets"] if str(b.get("id")) == bid), None)
        if existing:
            existing.update(bet)
        if "bankroll" in payload:
            data["bankroll"] = payload["bankroll"]
        save_data(data)

        msg = fmt_result_message(bet, data)
        if msg:
            asyncio.run(bot.send_message(
                chat_id=CHAT_ID,
                text=msg,
                parse_mode=ParseMode.MARKDOWN
            ))
        return jsonify({"ok": True})

    return jsonify({"error": "unknown action"}), 400

# ─── Endpoint: sync completo dallo storico dell'app ──────────────────────────
@app.route("/api/sync", methods=["POST"])
def sync_history():
    auth = request.headers.get("X-Secret", "")
    if auth != SECRET_KEY:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.json
    if not payload:
        return jsonify({"error": "empty"}), 400

    save_data(payload)
    return jsonify({"ok": True, "bets": len(payload.get("bets", []))})

# ─── Endpoint: richiedi report manuale ───────────────────────────────────────
@app.route("/api/report", methods=["POST"])
def manual_report():
    auth = request.headers.get("X-Secret", "")
    if auth != SECRET_KEY:
        return jsonify({"error": "unauthorized"}), 401

    payload = request.json or {}
    now = datetime.now(timezone.utc)
    year  = payload.get("year",  now.year)
    month = payload.get("month", now.month)

    send_monthly_report(year, month)
    return jsonify({"ok": True})

# ─── Health check ─────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(timezone.utc).isoformat()})

# ─── Invio report mensile ─────────────────────────────────────────────────────
def send_monthly_report(year: int, month: int):
    log.info(f"Generazione report {month}/{year}...")
    data = load_data()

    month_names = ["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
                   "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]

    try:
        pdf_bytes = generate_monthly_pdf(year, month, data)
    except Exception as e:
        import traceback
        log.error(f"Errore generazione PDF: {e}\n{traceback.format_exc()}")
        # Invia report testuale come fallback
        try:
            month_str_fb = f"{year}-{month:02d}"
            bets_fb = [b for b in data.get("bets", [])
                       if b.get("match", {}).get("date", "").startswith(month_str_fb)]
            won_fb  = sum(1 for b in bets_fb if b.get("result") == "won")
            lost_fb = sum(1 for b in bets_fb if b.get("result") == "lost")
            staked_fb = sum(b.get("stake", 0) for b in bets_fb)
            pwin_fb = sum(b.get("stake",0)*b.get("bookOdds",1) for b in bets_fb if b.get("result")=="won")
            profit_fb = pwin_fb - staked_fb
            roi_fb = (profit_fb / staked_fb * 100) if staked_fb > 0 else 0
            asyncio.run(bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"📊 *Report {month_names[month]} {year}* (testo — PDF non disponibile)\n\n"
                    f"📋 {len(bets_fb)} schedine · {won_fb}✅ {lost_fb}❌\n"
                    f"💶 Puntato: €{staked_fb:.2f}\n"
                    f"{'📈' if profit_fb >= 0 else '📉'} P/L: *{'+'if profit_fb>=0 else ''}€{profit_fb:.2f}* (ROI {'+' if roi_fb>=0 else ''}{roi_fb:.1f}%)\n"
                    f"💰 Cassa: *€{data.get('bankroll', 0):.2f}*\n\n"
                    f"⚠️ _Errore PDF: {str(e)[:100]}_"
                ),
                parse_mode=ParseMode.MARKDOWN
            ))
        except Exception as e2:
            log.error(f"Anche il fallback testuale ha fallito: {e2}")
        return

    # Calcola stats per il messaggio
    month_str = f"{year}-{month:02d}"
    bets = [b for b in data.get("bets", [])
            if b.get("match", {}).get("date", "").startswith(month_str)
            or b.get("createdAt", "").startswith(month_str)]

    won  = sum(1 for b in bets if b.get("result") == "won")
    lost = sum(1 for b in bets if b.get("result") == "lost")
    staked = sum(b.get("stake", 0) for b in bets)
    total_won = sum(b.get("stake", 0) * b.get("bookOdds", 1) for b in bets if b.get("result") == "won")
    profit = total_won - staked
    roi = (profit / staked * 100) if staked > 0 else 0

    caption = (
        f"📊 *Report Mensile — {month_names[month]} {year}*\n\n"
        f"📋 {len(bets)} schedine · {won}✅ {lost}❌\n"
        f"💶 Puntato: €{staked:.2f}\n"
        f"{'📈' if profit >= 0 else '📉'} P/L: *{'+'if profit>=0 else ''}€{profit:.2f}* "
        f"(ROI {'+' if roi>=0 else ''}{roi:.1f}%)\n"
        f"💰 Cassa attuale: *€{data.get('bankroll', 0):.2f}*"
    )

    import io
    asyncio.run(bot.send_document(
        chat_id=CHAT_ID,
        document=io.BytesIO(pdf_bytes),
        filename=f"BetPoisson_{month_names[month]}_{year}.pdf",
        caption=caption,
        parse_mode=ParseMode.MARKDOWN
    ))
    log.info(f"Report {month}/{year} inviato.")

# ─── Scheduler report mensile ────────────────────────────────────────────────
def run_scheduler():
    def check_end_of_month():
        now = datetime.now(timezone.utc)
        # Controlla se è l'ultimo giorno del mese alle 20:00 UTC
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day == last_day and now.hour == 20 and now.minute < 2:
            send_monthly_report(now.year, now.month)

    schedule.every(2).minutes.do(check_end_of_month)
    while True:
        schedule.run_pending()
        time.sleep(60)

# ─── Avvio ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Avvia scheduler in thread separato
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    log.info("BetPoisson Bot avviato ✅")

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
