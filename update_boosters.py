"""
Booster Board updater - runs on GitHub Actions (no PC or server needed).
Asks Discord (with your bot) who is boosting your server, removes bad names,
and writes boosters.txt. The VRChat world downloads that file.
"""
import os, json, time, re, datetime, urllib.request, urllib.error, urllib.parse

TOKEN = os.environ["DISCORD_TOKEN"].strip()
GUILD = os.environ["GUILD_ID"].strip()
HIDE_ROLE = os.environ.get("HIDE_ROLE", "hide-from-board").strip().lower()
LINK_SALT = os.environ.get("LINK_SALT", "").strip()
LINK_CHANNEL = os.environ.get("LINK_CHANNEL", "booster-link").strip().lower().lstrip("#")
HERE = os.path.dirname(os.path.abspath(__file__))


class Denied(Exception): pass


def api(path, method="GET", fatal=True):
    for attempt in range(6):
        req = urllib.request.Request("https://discord.com/api/v10" + path, method=method, data=b"" if method == "PUT" else None, headers={
            "Authorization": "Bot " + TOKEN,
            "User-Agent": "DiscordBot (https://github.com, 1.0) booster-board"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(json.loads(e.read().decode()).get("retry_after", 2))
                time.sleep(wait + 0.5); continue
            if e.code in (401, 403, 404) and not fatal:
                raise Denied(str(e.code))
            if e.code in (401, 403):
                raise SystemExit("Discord said no (%d). Check the DISCORD_TOKEN secret, that the bot is in your server, "
                                 "and that 'Server Members Intent' is ON in the Developer Portal." % e.code)
            raise
    raise SystemExit("Discord kept rate limiting, try again later.")


def lines(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p): return []
    out = []
    for l in open(p, encoding="utf-8"):
        l = l.strip().lower()
        if l and not l.startswith("#"): out.append(l)
    return out


# ---------------------------------------------------------------- filter
LEET = {"0": "o", "1": "i", "2": "z", "3": "e", "4": "a", "5": "s", "6": "g", "7": "t", "8": "b", "9": "g", "$": "s", "@": "a", "!": "i", "|": "i", "+": "t"}

def squash(s):  # "fuuuck" -> "fuck"
    return re.sub(r"(.)\1+", r"\1", s)

def leet(raw, one):
    return "".join(LEET.get(c, c) if c != "1" else one for c in raw)

def variants(name):
    """All the ways a username could hide a word: leetspeak, separators, repeats."""
    out = set()
    for one in ("i", "l"):              # 1 can be i or l
        letters = re.sub(r"[^a-z]", "", leet(name, one))
        out |= {letters, squash(letters)}
    digits_kept = re.sub(r"[^a-z0-9]", "", name)
    out |= {digits_kept, squash(digits_kept)}
    return {v for v in out if v}

def tokens(name):
    """The separate words of a name: "ass_man" -> ass, man"""
    out = set()
    for one in ("i", "l"):
        for t in re.split(r"[._]+", leet(name, one)):
            t = re.sub(r"[^a-z]", "", t)
            if t: out |= {t, squash(t)}
    return out

STRONG = lines("badwords.txt")          # hidden if found ANYWHERE in the name
WEAK = lines("badwords_short.txt")      # short words: hidden only as a separate word ("ass_man", not "assassin")
SAFE = sorted(lines("safewords.txt"), key=len, reverse=True)   # normal words that contain a bad word ("hancock", "therapist")
ALLOW = set(lines("allow.txt"))         # names you checked yourself and want to show
HIDE = set(lines("hidden.txt"))         # names you never want to show

def bad(name):
    name = name.lower()
    if name in ALLOW: return False
    if name in HIDE: return True
    for v in variants(name):
        squashed = v == squash(v)
        for sw in SAFE:                 # blank out harmless words first, then look for bad ones
            v = v.replace(squash(sw) if squashed else sw, "#")
        rv = v[::-1]
        for w in STRONG:
            w2 = squash(w) if squashed else w     # "fuuuck" is compared as "fuck"
            if len(w2) < 3: w2 = w
            if w2 in v: return True
            if len(w2) >= 5 and w2 in rv: return True   # written backwards
    whole = variants(name)
    for t in tokens(name) | whole:
        for w in WEAK:
            if t == w or t == squash(w): return True
    return False


# ---------------------------------------------------------------- main
roles = api("/guilds/%s/roles" % GUILD)
hide_ids = {r["id"] for r in roles if r["name"].strip().lower() == HIDE_ROLE}

members, after = [], "0"
while True:
    page = api("/guilds/%s/members?limit=1000&after=%s" % (GUILD, after))
    if not page: break
    members += page
    after = page[-1]["user"]["id"]
    if len(page) < 1000: break

boosters = [m for m in members if m.get("premium_since") and not m["user"].get("bot") and not (hide_ids & set(m.get("roles", [])))]
boosters.sort(key=lambda m: m["premium_since"])           # longest boosters first

shown, blocked = [], 0
for m in boosters:
    u = m["user"]["username"].lower()
    if not re.fullmatch(r"[a-z0-9_.]{2,32}", u): blocked += 1; continue
    if bad(u):
        blocked += 1
        print("hidden:", u[:2] + "*" * (len(u) - 2))       # masked so the public log stays clean
        continue
    shown.append(u)

today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
with open(os.path.join(HERE, "boosters.txt"), "w", encoding="utf-8", newline="\n") as f:
    f.write("# updated %s\n" % today)
    for u in shown: f.write(u + "\n")
print("boosters: %d shown, %d hidden (members checked: %d)" % (len(shown), blocked, len(members)))


# ---------------------------------------------------------------- booster titles (VRChat link)
# A booster gets a code in the world (made from their VRChat name), then posts
# "CODE TheirVRChatName" in #booster-link. If the code fits the name and they
# are boosting, their name goes into boosters_vrc.txt (as a hash, not the name)
# and the world gives them a gold title.
ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def fnv(text):
    h = 2166136261
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        c = data[i] | (data[i + 1] << 8)
        if 65 <= c <= 90: c += 32                      # A-Z -> a-z, same as the world
        h ^= c
        h = (h * 16777619) & 0xFFFFFFFF
    return h

def link_code(name):
    h = fnv(LINK_SALT + "|" + name.strip())
    s = ""
    for _ in range(7):
        s += ALPHA[h & 31]; h >>= 5
    return s[:3] + "-" + s[3:]

def list_id(name):
    return "%08x" % fnv("L|" + name.strip())

linked = {}                                             # discord user id -> vrchat name
if not LINK_SALT:
    print("titles: LINK_SALT secret not set, skipping")
else:
    try:
        chans = api("/guilds/%s/channels" % GUILD)
        chan = next((c for c in chans if c.get("type") == 0 and c["name"].lower() == LINK_CHANNEL), None)
        if chan is None:
            print("titles: no #%s channel found, skipping" % LINK_CHANNEL)
        else:
            msgs, before = [], ""
            for _ in range(10):                         # the last 1000 messages
                page = api("/channels/%s/messages?limit=100%s" % (chan["id"], before), fatal=False)
                if not page: break
                msgs += page
                before = "&before=" + page[-1]["id"]
                if len(page) < 100: break
            msgs.reverse()                              # oldest first, so a newer post replaces an older one
            booster_ids = {m["user"]["id"] for m in boosters}
            hidden_names = set(lines("hidden.txt"))
            boosters_by_id = {m["user"]["id"]: m for m in boosters}
            taken = {}                                  # vrchat name -> discord id (first one keeps it)
            pat = re.compile(r"^\s*([A-Za-z0-9]{3})-?([A-Za-z0-9]{4})\s+[\"'`<]*(.+?)[\"'`>]*\s*$", re.S)
            empty = 0
            for m in msgs:
                text = (m.get("content") or "").strip()
                if not text: empty += 1; continue
                hit = pat.match(text)
                if not hit: continue
                code = (hit.group(1) + "-" + hit.group(2)).upper()
                name = hit.group(3).strip().splitlines()[0].strip()
                uid = m["author"]["id"]
                ok = (uid in booster_ids and code == link_code(name)
                      and m["author"]["username"].lower() not in hidden_names
                      and taken.get(name.lower(), uid) == uid)
                if ok:
                    old = linked.get(uid)
                    if old: taken.pop(old.lower(), None)
                    linked[uid] = name; taken[name.lower()] = uid
                mine = {r["emoji"]["name"] for r in m.get("reactions", []) if r.get("me")}
                emoji = "\u2705" if ok else "\u274c"
                if emoji not in mine:
                    try: api("/channels/%s/messages/%s/reactions/%s/@me" % (chan["id"], m["id"], urllib.parse.quote(emoji)), method="PUT", fatal=False)
                    except Denied: pass
                    time.sleep(0.3)
            if msgs and empty == len(msgs):
                print("titles: can't read messages - turn ON 'Message Content Intent' for the bot")
    except Denied as e:
        print("titles: the bot can't see #%s (%s) - give it View Channel + Read Message History there" % (LINK_CHANNEL, e))

    with open(os.path.join(HERE, "boosters_vrc.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# updated %s\n" % today)
        for n in sorted(list_id(v) for v in linked.values()): f.write(n + "\n")
    print("titles: %d boosters linked" % len(linked))
