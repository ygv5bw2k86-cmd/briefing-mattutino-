import os
import re
import sys
import time
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import requests
import markdown

ORE_INDIETRO = 36
MAX_PER_FEED = 10
MODELLI = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"]
BROWSER = {"User-Agent": "Mozilla/5.0 (compatible; BriefingMattutino/1.0)"}
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

PROMPT = open("prompt.txt", encoding="utf-8").read()

PAGINA = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Briefing">
<title>Briefing {{DATA}}</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--tx:#1a1a1a;--ac:#0b57d0;--bd:#ddd}
@media(prefers-color-scheme:dark){:root{--bg:#121212;--tx:#e8e8e8;--ac:#8ab4f8;--bd:#333}}
body{margin:0;padding:16px;background:var(--bg);color:var(--tx);font:17px/1.5 -apple-system,system-ui,sans-serif;max-width:700px;margin:auto}
h1{font-size:1.3em;margin:.2em 0}
h2{font-size:1.1em;margin-top:1.8em;padding-top:.8em;border-top:1px solid var(--bd)}
a{color:var(--ac)} ul{padding-left:1.1em} li{margin-bottom:.6em}
.sub,.nota{font-size:.8em;opacity:.7}
</style></head><body>
<h1>Briefing del {{DATA}}</h1>
<div class="sub">Aggiornato alle {{ORA}}</div>
{{CORPO}}
<p class="nota">Generato automaticamente da IA a partire da feed RSS. Contenuto informativo e didattico, non è consulenza finanziaria. Verifica sempre le fonti.</p>
</body></html>"""


def pulisci(testo, max_car=300):
    testo = re.sub(r"<[^>]+>", " ", testo or "")
    testo = html.unescape(re.sub(r"\s+", " ", testo)).strip()
    return testo[:max_car]


def leggi_feed():
    limite = datetime.now(timezone.utc) - timedelta(hours=ORE_INDIETRO)
    sezioni = {}
    sezione = None
    with open("feeds.txt", encoding="utf-8") as f:
        righe = [r.strip() for r in f if r.strip()]
    for riga in righe:
        if riga.startswith("["):
            sezione = riga.strip("[]")
            sezioni[sezione] = []
            continue
        try:
            risposta = requests.get(riga, headers=BROWSER, timeout=25)
            risposta.raise_for_status()
            feed = feedparser.parse(risposta.content)
        except Exception as errore:
            print(f"[SALTATO] {riga} -> {errore}")
            continue
        fonte = pulisci(feed.feed.get("title", ""), 60) or riga
        trovati = 0
        for voce in feed.entries:
            data = voce.get("published_parsed") or voce.get("updated_parsed")
            if data and datetime(*data[:6], tzinfo=timezone.utc) < limite:
                continue
            titolo = pulisci(voce.get("title", ""), 200)
            sommario = pulisci(voce.get("summary", ""), 450)
            sezioni[sezione].append(f"- [{fonte}] {titolo} | {sommario} | {voce.get('link', '')}")
            trovati += 1
            if trovati >= MAX_PER_FEED:
                break
        print(f"[OK] {fonte}: {trovati} notizie")
    return sezioni


def chiedi_gemini(prompt, chiave):
    for modello in MODELLI:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modello}:generateContent"
        for tentativo in range(3):
            try:
                r = requests.post(
                    url,
                    headers={"x-goog-api-key": chiave, "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0.3, "maxOutputTokens": 24000}},
                    timeout=240,
                )
            except Exception as errore:
                print(f"[{modello}] errore di rete: {errore}")
                time.sleep(20)
                continue
            if r.status_code == 200:
                parti = r.json()["candidates"][0]["content"]["parts"]
                testo = "".join(p.get("text", "") for p in parti).strip()
                if testo:
                    print(f"Riassunto creato con {modello}")
                    return testo
            print(f"[{modello}] risposta {r.status_code}: {r.text[:200]}")
            if r.status_code in (429, 500, 503):
                time.sleep(30 * (tentativo + 1))
                continue
            break
    sys.exit("Nessun modello Gemini ha risposto.")


def main():
    chiave = os.environ.get("GEMINI_API_KEY")
    if not chiave:
        sys.exit("Manca la chiave GEMINI_API_KEY.")
    sezioni = leggi_feed()
    if sum(len(v) for v in sezioni.values()) == 0:
        sys.exit("Nessuna notizia trovata.")
    adesso = datetime.now(ZoneInfo("Europe/Rome"))
    data = f"{GIORNI[adesso.weekday()]} {adesso.strftime('%d/%m/%Y')}"
    notizie = "\n\n".join(f"### AREA: {nome}\n" + "\n".join(voci) for nome, voci in sezioni.items())
    prompt = PROMPT.replace("{DATA}", data).replace("{ORE}", str(ORE_INDIETRO)).replace("{NOTIZIE}", notizie)
    testo = chiedi_gemini(prompt, chiave)
    corpo = markdown.markdown(testo, extensions=["extra"])
    pagina = (PAGINA.replace("{{DATA}}", data)
                    .replace("{{ORA}}", adesso.strftime("%H:%M"))
                    .replace("{{CORPO}}", corpo))
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(pagina)
    print("Pagina salvata in docs/index.html")


main()
