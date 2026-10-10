"""PropDesk CLOUD runner -- the always-on jobs, so alerts keep coming when the laptop is off (2026-10-09).
Runs on GitHub Actions every 10 min, leagues tiered 10/30/60 min by how fast their lines move (see .github/workflows/propdesk.yml). Needs only `requests`. Posts to Discord (DISCORD_WEBHOOK
secret) and/or ntfy (NTFY_TOPIC secret).

  1. PROMOS      -- PrizePicks marks them (is_promo / discount_name / flash_sale_line_score). New ones -> alert.
  2. LINE MOVES  -- PP standard lines vs the last run, EVERY player in the league (league-based, not leg-based) -> one digest
                    per league per run: raised / lowered / pulled, biggest moves first (8 each, plus the counts).
  3. SHARP VALUE -- a PP standard line where Pinnacle's de-vigged price AT THE SAME NUMBER gives one side >= 60% (PP 2-pick
                    break-even 57.7%) -> alert, once per line.
State lives in state/*.json and is committed back by the workflow (small: line numbers only).
"""
import os, json, re, time, datetime, requests

CT = datetime.timezone(datetime.timedelta(hours=-5))
NOW = datetime.datetime.now(datetime.timezone.utc)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124", "Accept": "application/json"}
PH = dict(UA, **{"X-API-Key": "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"})          # Pinnacle's public guest key (same as their web app)
PP_LEAGUES = {8: "NHL", 7: "NBA", 3: "WNBA", 9: "NFL", 15: "CFB", 82: "SOCCER", 265: "CS2", 159: "VAL", 121: "LoL", 174: "Dota2",
              135: "KBO", 298: "NPB", 2: "MLB", 131: "GOLF"}
PIN_LEAGUES = {"NHL": 1456, "NFL": 889, "WNBA": 578, "NBA": 487, "CFB": 880}
STAT = {"shotsongoal": "shots on goal", "points": "points", "assists": "assists", "goals": "goals", "goaliesaves": "saves",
        "passyards": "passing yards", "rushyards": "rushing yards", "receivingyards": "receiving yards", "receptions": "receptions",
        "rebounds": "rebounds", "ptsrebsasts": "pts & rebs & asts", "3ptmade": "threes made"}
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
S = "state"
os.makedirs(S, exist_ok=True)
def load(name, default):
    try:
        return json.load(open(os.path.join(S, name), encoding="utf-8"))
    except Exception:
        return default
def save(name, obj):
    json.dump(obj, open(os.path.join(S, name), "w", encoding="utf-8"), ensure_ascii=False, indent=0)

sent = []
def alert(kind, text):
    icon = {"PROMO": "🟢", "MOVE": "📈", "PULLED": "⛔", "VALUE": "💰"}.get(kind, "•")
    stamp = datetime.datetime.now(CT).strftime("%I:%M %p CT")
    msg = "%s **%s** -- %s  (%s)" % (icon, kind, text, stamp)
    sent.append(msg)
    hook, topic = os.environ.get("DISCORD_WEBHOOK"), os.environ.get("NTFY_TOPIC")
    if hook:
        try:
            requests.post(hook, json={"content": msg[:1900]}, timeout=20); time.sleep(0.6)
        except Exception as e:
            print("discord:", e)
    if topic:
        try:
            requests.post("https://ntfy.sh/%s" % topic, data=("%s %s" % (text, stamp)).encode(), headers={"Title": "%s %s" % (icon, kind)}, timeout=20)
        except Exception as e:
            print("ntfy:", e)

def get(url, h=UA):
    for i in range(3):
        try:
            r = requests.get(url, headers=h, timeout=40)
            if r.status_code == 200:
                return r.json()
            print(url[:80], "HTTP", r.status_code)
        except Exception as e:
            print(url[:80], e)
        time.sleep(15 * (i + 1))                                        # 429 backoff
    return None

# ---------------------------------------------------------------- PrizePicks board
# 10/9 user: every 10 min, not hourly. QUICK runs (every 10 min) = the busiest boards; a FULL run (minute 0-9 of each half hour)
# = every league + the Pinnacle value check. Keeps PrizePicks' rate limit (429s) away while promos/moves land within ~10 min.
# 10/9 user: check leagues by HOW FAST THEIR LINES CHANGE (measured on lines.db: line changes per 100 posted lines,
# CFB 145, NFL 122, WNBA 29, VAL 27, LoL 25, NBA 22 | Dota 16, soccer 12.5, NHL 12, CS2 9, MLB 6 | golf 1, KBO 0, NPB 0),
# League-based only (user 10/9): every line in a league, for everyone -- no per-leg promotion.
TIER10 = {15, 9, 3, 159, 121, 7}             # CFB NFL WNBA VAL LoL NBA
TIER30 = {174, 82, 8, 265, 2}                # Dota soccer NHL CS2 MLB
TIER60 = {135, 298, 131}                     # KBO NPB golf
RUN30 = NOW.minute % 30 < 10 or os.environ.get("FULL") == "1"
RUN60 = NOW.minute < 10 or os.environ.get("FULL") == "1"
FULL = RUN30                                  # Pinnacle value check rides the 30-minute runs
QUICK = TIER10 | (TIER30 if RUN30 else set()) | (TIER60 if RUN60 else set())
print("this run checks:", sorted(PP_LEAGUES[l] for l in QUICK if l in PP_LEAGUES))
for lid, lg in PP_LEAGUES.items():
    if lid not in QUICK:
        continue
    d = get("https://partner-api.prizepicks.com/projections?per_page=1000&league_id=%d" % lid)
    time.sleep(4)
    if not d:
        continue
    inc = {(x["type"], x["id"]): x.get("attributes", {}) for x in d.get("included", [])}
    for x in d.get("data", []):
        a = x.get("attributes", {})
        try:
            if datetime.datetime.fromisoformat(a["start_time"]).astimezone(datetime.timezone.utc) < NOW:
                continue
        except Exception:
            continue
        pl = inc.get(("new_player", ((x.get("relationships") or {}).get("new_player") or {}).get("data", {}).get("id")), {})
        if a.get("is_promo") or a.get("discount_name"):
            promos.append(dict(id=x["id"], lg=lg, player=pl.get("name"), stat=a.get("stat_type"), line=a.get("line_score"),
                               promo=a.get("flash_sale_line_score"), kind=a.get("discount_name"), start=a.get("start_time")))
        if a.get("odds_type") == "standard" and "+" not in (pl.get("name") or ""):
            lines[x["id"]] = [float(a["line_score"]), lg, pl.get("name"), a.get("stat_type"), pl.get("team"), a.get("start_time")]
print("PP: %d live standard lines, %d promos" % (len(lines), len(promos)))
if not lines:
    print("PrizePicks returned nothing (blocked from this server?) -- skipping moves/value, keeping old state")

# 1) PROMOS
seen = set(load("promos_seen.json", []))
for p in promos:
    if p["id"] in seen:
        continue
    seen.add(p["id"])
    st = datetime.datetime.fromisoformat(p["start"]).astimezone(CT).strftime("%a %I:%M %p")
    alert("PROMO", "%s %s %s: %s -> %s (%s) %s CT" % (p["lg"], p["player"], p["stat"], p["line"], p["promo"], p["kind"], st))
save("promos_seen.json", sorted(seen)[-3000:])

# 2) LINE MOVES -- everyone's lines, league-based. One DIGEST message per league per run (NFL/CFB move ~460-480 lines a day:
#    one ping per move would be hundreds of buzzes). Raised / lowered / pulled, biggest moves first.
if lines:
    last = load("last_lines.json", {})
    fetched = {v[1] for v in lines.values()}
    dig = {}
    for pid, old in last.items():
        if old[1] not in fetched:
            continue
        new = lines.get(pid)
        if new and new[0] == old[0]:
            continue
        try:
            if datetime.datetime.fromisoformat(old[5]).astimezone(datetime.timezone.utc) < NOW:
                continue                                         # game started -> not a pull
        except Exception:
            continue
        d = dig.setdefault(old[1], {"up": [], "down": [], "pulled": []})
        if not new:
            d["pulled"].append("%s %s %s" % (old[2], old[3], old[0]))
        else:
            d["up" if new[0] > old[0] else "down"].append((abs(new[0] - old[0]), "%s %s %s->%s" % (old[2], old[3], old[0], new[0])))
    for lg, d in dig.items():
        n = len(d["up"]) + len(d["down"])
        parts = ["%d moved, %d pulled" % (n, len(d["pulled"]))]
        if d["up"]:
            parts.append("UP: " + "; ".join(x for _, x in sorted(d["up"], reverse=True)[:8]))
        if d["down"]:
            parts.append("DOWN: " + "; ".join(x for _, x in sorted(d["down"], reverse=True)[:8]))
        if d["pulled"]:
            parts.append("PULLED: " + "; ".join(d["pulled"][:8]))
        alert("MOVE", "%s -- %s" % (lg, " | ".join(parts)))
    merged = {k: v for k, v in last.items() if v[1] not in fetched}
    merged.update(lines)
    save("last_lines.json", merged)

# 3) SHARP VALUE at PP's exact number (Pinnacle props) -- full runs only
if lines and FULL:
    done = set(load("value_seen.json", []))
    by = {}
    for pid, (ln, lg, pl, stt, tm, st_) in lines.items():
        k = STAT.get(norm(stt))
        if k and lg in PIN_LEAGUES:
            by[(lg, norm(pl), k, ln)] = (pid, pl, stt, ln, st_)
    for lg, plid in PIN_LEAGUES.items():
        M = get("https://guest.api.arcadia.pinnacle.com/0.1/leagues/%d/matchups" % plid, PH) or []
        K = get("https://guest.api.arcadia.pinnacle.com/0.1/leagues/%d/markets/straight" % plid, PH) or []
        sp = {m["id"]: m for m in M if m.get("type") == "special" and (m.get("special") or {}).get("category") == "Player Props"}
        for k in K:
            m = sp.get(k.get("matchupId"))
            if not m or k.get("type") != "total":
                continue
            names = {p["id"]: p["name"] for p in m["participants"]}
            pr = {names.get(p.get("participantId")): (p.get("points"), p["price"]) for p in k["prices"]}
            if "Over" not in pr or "Under" not in pr:
                continue
            mm = re.match(r"(.+?) Total (.+)$", m["special"]["description"])
            if not mm:
                continue
            imp = lambda o: (-o) / ((-o) + 100) if o < 0 else 100 / (o + 100)
            o, u = imp(pr["Over"][1]), imp(pr["Under"][1]); po = o / (o + u)
            hit = by.get((lg, norm(mm.group(1)), mm.group(2).lower(), float(pr["Over"][0])))
            if not hit:
                continue
            pid, pl, stt, ln, st_ = hit
            side, p = ("MORE", po) if po >= 0.5 else ("LESS", 1 - po)
            if p >= 0.60 and pid not in done:
                done.add(pid)
                alert("VALUE", "%s %s %s %s %s -- Pinnacle %.0f%% at PP's number (b/e 57.7%%)" % (lg, pl, stt, side, ln, 100 * p))
        time.sleep(1)
    save("value_seen.json", sorted(done)[-5000:])

save("last_run.json", dict(utc=NOW.isoformat(), lines=len(lines), promos=len(promos), alerts=sent))
print("alerts sent: %d" % len(sent))
for s in sent:
    print("  ", s)
