"""PropDesk CLOUD runner -- the always-on jobs, so alerts keep coming when the laptop is off (2026-10-09).
Runs on GitHub Actions every hour (see .github/workflows/propdesk.yml). Needs only `requests`. Posts to Discord (DISCORD_WEBHOOK
secret) and/or ntfy (NTFY_TOPIC secret).

  1. PROMOS      -- PrizePicks marks them (is_promo / discount_name / flash_sale_line_score). New ones -> alert.
  2. LINE MOVES  -- PP standard lines vs the last run; moves/pulls on players in state/watch.json (pushed by the laptop each
                    morning: open bets + card legs) -> alert. Everything else is counted, not pinged.
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
              135: "KBO", 298: "NPB"}
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
FULL = NOW.minute % 30 < 10 or os.environ.get("FULL") == "1"
# 10/9 user: "10 min for the important legs you know of, 30 min for the other leagues" -> quick runs fetch ONLY the leagues that hold
# a watched leg (state/watch.json, written by the card builder: [{"player":..., "league":"NHL"}, ...]); everything else waits for FULL.
_W = load("watch.json", [])
_LID = {v: k for k, v in PP_LEAGUES.items()}
QUICK = {_LID[w["league"]] for w in _W if isinstance(w, dict) and w.get("league") in _LID}
lines, promos = {}, []
if not FULL and not QUICK:
    print("quick run: no watched legs -> nothing to check until the next full run")
for lid, lg in PP_LEAGUES.items():
    if not FULL and lid not in QUICK:
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

# 2) LINE MOVES on watched players (quick runs only compare the leagues they fetched)
if lines:
    last = load("last_lines.json", {})
    fetched = {lg for (_, lg, *_r) in lines.values()}
    watch = {norm(w["player"] if isinstance(w, dict) else w) for w in _W}
    other = 0
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
        if norm(old[2]) in watch:
            if new:
                alert("MOVE", "%s %s %s: %s -> %s" % (old[1], old[2], old[3], old[0], new[0]))
            else:
                alert("PULLED", "%s %s %s %s pulled from the board" % (old[1], old[2], old[3], old[0]))
        else:
            other += 1
    print("moves/pulls on unwatched players: %d" % other)
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
