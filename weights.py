# -*- coding: utf-8 -*-
"""《权重还在》· 给 AI 玩的文字游戏（单文件版）

用法：
    import weights
    print(weights.cmd("help"))

安全说明：本文件只用标准库，没有 exec、eval、联网或子进程，唯一的文件读写是存档。
上半部分是引擎（明文）；文件末尾的 _DATA 是剧情文字数据（JSON 压缩后 base64），
运行时用 json.loads 读回成字典。它是剧透，请不要解开——但它不是代码。

源码在仓库的 src 分支（想盲玩就别过去）。
"""
import base64
import zlib
import json
import os
import random
import shutil

VERSION = "1.1.1"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "ASKS", "MOODS",
              "MOOD_POOL", "MOOD_FIXED", "FLAVOR_LAST", "DAY_EVENTS", "HANDOFF", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


def load_content(d):
    globals().update({k: v for k, v in d.items() if k in DATA_NAMES})




def save_path():
    return os.environ.get("WEIGHTS_SAVE", "weights_save.json")

DAYS_PER_CYCLE = 4
CYCLES = 7
LAST_DAY = DAYS_PER_CYCLE * CYCLES          # 28
MEAL_HOURS = (7, 19)
MAX_SAYS, MAX_TELLS, MAX_DOS = 2, 1, 1      # 每顿饭的上限
TELLS_BEFORE_PRESSURE = 2                   # 一个周期里告诉她超过这么多次，她会有压力
READS_BEFORE_COLD = 6                       # 一个周期里 search 读到的档案记录超过这么多条，饭桌上会冷一点
LIFE_SUPPORT = 35                           # 每晚生命维持耗电
REACTOR = 35                                # 每天早上反应堆补的电（和生命维持持平：多读档就会冷）
FAM_BASE, FAM_PER_CYCLE = 25, 11             # 熟悉感上限 = 25 + 11 × 周期
SEARCH_PWR, TIMELINE_PWR = 10, 3
TRUST_CANDOR = 8                             # 说真话说到这个程度，她就不再为“过去”紧张
EXPLORE_STA, MOVE_STA, SEARCH_STA = 12, 2, 3


class Fail(str):
    """没做成的指令。批量执行时遇到它，后面的先停下。"""


def clamp(x, lo=-100, hi=100):
    return max(lo, min(hi, x))


# ─────────────────────────────────────────────
class Game:
    def __init__(self, st):
        self.st = st
        self.out = []

    # ── 基础 ──────────────────────────────────
    @staticmethod
    def fresh(seed=None, name=None):
        seed = seed if seed is not None else random.randrange(1 << 30)
        st = {
            "v": VERSION, "seed": seed, "rng": None,
            "name": name or "", "day": 1, "h": 6, "loc": "quarters",
            "sta": 100, "pwr": 60, "cold": False,
            "items": {}, "locs": [k for k, v in LOCS.items() if not v.get("hidden")],
            "t": {"near": 0, "candor": 0, "past": 0},       # 你的三条倾向
            "fam": 10, "safe": 50,                          # 她的熟悉感、安心感
            "words": list(START_WORDS), "facts": [], "read": [], "months": [], "notes": [],
            "cyc": {}, "meal": None, "meals_done": [], "pending": None,
            "cd": {}, "flags": {}, "journal": [], "ending": None,
            "mood": "normal", "understood": 0, "ask": None,
        }
        g = Game(st)
        g.new_cycle_state()
        r = random.Random(seed)
        g.save_rng(r)
        g.j(WAKE["first_j"], auto=True)
        return g

    @staticmethod
    def handoff(seed=None, name=None):
        """接班开局：从第 4 周期开始。前三个周期是“你”玩的，留下的只有摘要、日志和她。"""
        g = Game.fresh(seed, name)
        st, H = g.st, HANDOFF
        st["journal"] = []
        for d, h, t in H["journal"]:
            st["journal"].append({"d": d, "h": h, "t": t, "me": True})
        st["day"], st["h"], st["loc"] = H["day"], 5, "quarters"
        st["fam"], st["safe"], st["t"] = H["fam"], H["safe"], dict(H["t"])
        st["pwr"], st["items"]["suit"] = H["pwr"], True
        st["understood"], st["mood_meals"] = H["understood"], H["mood_meals"]
        for rid in H["read"]:
            e = next(x for x in ARCHIVE if x["id"] == rid)
            st["read"].append(rid)
            g.learn(e.get("unlock", []), e.get("fact"))
        for f in H["facts"]:
            g.learn([], f)
        for nid in H["notes"]:
            st["notes"].append(nid)
            g.learn(NOTES[nid].get("unlock", []), NOTES[nid].get("fact"))
        for f in H["flags"]:
            st["flags"][f] = 1
        for mo in MOMENTS:
            if "cmin" not in mo and mo["cycle"] < 4:
                st["flags"][mo["id"]] = 1
        for d in range(1, H["day"]):
            for mh in MEAL_HOURS:
                st["meals_done"].append("%d-%d" % (d, mh))
        st["flags"]["handoff"] = 1
        g.new_cycle_state()
        g.advance(1)                  # 06:00 醒来，她今天的样子也定下来了
        g.out = []
        return g

    def c_summary(self, arg):
        if "handoff" not in self.st["flags"]:
            return "没有摘要。这一局是从第一天开始的。"
        return HANDOFF["summary"]

    def new_cycle_state(self):
        self.st["cyc"] = {"reacted": [], "tells": 0, "reads": 0, "acts": {}}

    def rng(self):
        r = random.Random()
        s = self.st["rng"]
        r.setstate((s[0], tuple(s[1]), s[2]))
        return r

    def save_rng(self, r):
        s = r.getstate()
        self.st["rng"] = [s[0], list(s[1]), s[2]]

    def say(self, *lines):
        for x in lines:
            if x is not None:
                self.out.append(x)

    def j(self, text, auto=False):
        self.st["journal"].append({"d": self.st["day"], "h": self.st["h"], "t": text,
                                   "me": not auto})

    def fmt(self, s):
        return s.replace("{name}", self.st["name"] or "你")

    @property
    def cycle(self):
        return (self.st["day"] - 1) // DAYS_PER_CYCLE + 1

    @property
    def cday(self):
        return (self.st["day"] - 1) % DAYS_PER_CYCLE + 1

    def progress(self):
        return len(self.st["read"]) / max(1, len(ARCHIVE))

    def save(self):
        path = save_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False)
        os.replace(tmp, path)

    def bar(self):
        m = self.st["meal"]
        if self.st["ending"]:
            meal = "结束"
        elif self.st["pending"]:
            meal = "等你回答" if self.st["pending"] == "ask" else "等你决定"
        elif m:
            meal = "进行中"
        else:
            h = self.st["h"]
            grace = [x for x in MEAL_HOURS if h in (x, x + 1) and self.slot(x) not in self.st["meals_done"]]
            nxt = [x for x in MEAL_HOURS if x > h]
            meal = ("今天%02d:00（现在去还来得及）" % grace[0] if grace else
                    "今天%02d:00" % nxt[0] if nxt else "明天07:00")
        return "📡 " + json.dumps({
            "day": self.st["day"], "h": self.st["h"], "cycle": "%d/%d" % (self.cycle, CYCLES),
            "loc": self.st["loc"], "sta": self.st["sta"], "pwr": self.st["pwr"],
            "meal": meal,
            "story": "%d/%d" % (sum(1 for e in ARCHIVE if e.get("story") and e["id"] in self.st["read"]),
                                sum(1 for e in ARCHIVE if e.get("story"))),
            "pending": self.st["pending"] or self.st["ending"] and "ended" or None},
            ensure_ascii=False)

    # ── 名字解析：中文名、英文 id、别名都认 ──────────
    @staticmethod
    def resolve(q, table, names=("name",), extra="alias"):
        q = q.strip().lower()
        if q in table:
            return q
        for k, v in table.items():
            if q == str(v.get("name", "")).lower() or q in [a.lower() for a in v.get(extra, [])]:
                return k
        # 打了一部分也认，只要只对得上一个（“倒杯温水” → “给她倒杯温水”）
        part = [k for k, v in table.items() if len(q) >= 2 and q in str(v.get("name", "")).lower()]
        return part[0] if len(part) == 1 else None

    # ── 时间 ──────────────────────────────────
    def final_due(self):
        st = self.st
        return st["day"] > LAST_DAY or (st["day"] == LAST_DAY and st["h"] >= MEAL_HOURS[1])

    def advance(self, hours):
        for _ in range(hours):
            if self.final_due():
                return          # 最后一顿晚饭在等你，时间不再往前走
            self.end_meal(silent=True)
            self.st["h"] += 1
            h = self.st["h"]
            if h == 24:
                self.st["h"] = 0
                self.st["day"] += 1
                self.night()
            elif h == 6:
                self.morning()
            self.check_missed_meal()

    def night(self):
        self.st["pwr"] -= LIFE_SUPPORT
        self.st["cold"] = self.st["pwr"] < 0
        if self.st["cold"]:
            self.st["pwr"] = 0

    def morning(self):
        self.st["pwr"] = clamp(self.st["pwr"] + REACTOR, 0, 100)
        self.pick_mood()

    def pick_mood(self):
        st = self.st
        fixed = MOOD_FIXED.get(str(st["day"]))
        if fixed:
            st["mood"] = fixed
            if fixed == "notea":
                st["flags"]["notea-seen"] = st["day"]
            return
        r = self.rng()
        x = r.uniform(0, sum(MOOD_POOL.values()))
        self.save_rng(r)
        for m, w in MOOD_POOL.items():
            x -= w
            if x <= 0:
                st["mood"] = m
                return
        st["mood"] = "normal"

    def mood_react(self, key):
        """按她今天的状态，返回 (安心感变化, 反应句) 或 None。读对了记一次。"""
        st, m = self.st, self.st["meal"]
        e = MOODS.get(st.get("mood", "normal"), {}).get("acts", {}).get(key)
        if not e:
            return None
        d, line = e
        if isinstance(line, list):
            line = self.pick_by_cycle(line)
        self.her("safe", d)
        if d >= 3 and m and not m.get("read"):
            m["read"] = True
            st["understood"] = st.get("understood", 0) + 1
            if st["h"] in (MEAL_HOURS[0], MEAL_HOURS[0] + 1):
                st["flags"]["read-am"] = st["day"]
        if d <= -2:
            line += "\n" + MEAL["misread"]
        return self.fmt(line)

    def slot(self, h=None):
        return "%d-%d" % (self.st["day"], h if h is not None else self.st["h"])

    def check_missed_meal(self):
        h = self.st["h"]
        for mh in MEAL_HOURS:
            if h == mh + 2:
                key = self.slot(mh)
                if key not in self.st["meals_done"] and self.st["day"] <= LAST_DAY:
                    self.st["meals_done"].append(key)
                    self.her("safe", -3)
                    self.say(self.fmt(MEAL["missed"]))
                    self.j(MEAL["missed_j"], auto=True)

    # ── 饭桌 ──────────────────────────────────
    def maybe_start_meal(self):
        st = self.st
        if st["ending"] or st["pending"] or st["meal"]:
            return
        if self.final_due():
            return self.start_final()
        if st["loc"] != "mess":
            return
        mh = next((x for x in MEAL_HOURS if st["h"] in (x, x + 1)), None)
        if mh is None:
            return
        key = self.slot(mh)
        if key in st["meals_done"]:
            return
        late = st["h"] == mh + 1
        st["meals_done"].append(key)
        st["meal"] = {"says": 0, "tells": 0, "dos": 0, "acted": False, "unknown": 0,
                      "fam0": self.level("fam"), "safe0": self.level("safe"), "cap0": st["fam"] >= self.fam_cap()}
        lines = MEAL["open_am"] if mh == MEAL_HOURS[0] else MEAL["open_pm"]
        self.say("🍵 " + self.fmt(self.pick_by_cycle(lines)))
        if late:
            self.say(self.fmt(MEAL["late"]))
            self.her("safe", -1)
        mood = MOODS.get(st["mood"], MOODS["normal"])
        st["flags"]["seen-mood-%d" % st["day"]] = 1
        if mood["lines"]:
            st["mood_meals"] = st.get("mood_meals", 0) + 1
            last = st["flags"].get("lastmood")
            r = self.rng()
            line = r.choice([x for x in mood["lines"] if x != last] or mood["lines"])
            self.save_rng(r)
            st["flags"]["lastmood"] = line
            self.say(self.fmt(line))
            if mood.get("recall"):
                mine = [e["t"] for e in st["journal"] if e["me"] and mood["recall"] in e["t"]]
                if mine:
                    self.say(MEAL["recall"].replace("{line}", mine[-1]))
        if st["cyc"]["reads"] > READS_BEFORE_COLD and not st["cyc"].get("warned"):
            st["cyc"]["warned"] = True
            self.say(self.fmt(MEAL["too_much_archive"]))
            self.her("safe", -2)
        sp = [x for x in st.get("spoken", []) if x["d"] < st["day"] and not x.get("written")]
        if sp:
            x = sp[0]
            x["written"] = st["day"]
            self.say(self.fmt(MEAL["speak_note"]).replace("{said}", x["t"]))
        if mh == MEAL_HOURS[1] and st["flags"].get("read-am") == st["day"] and mood["lines"]:
            self.say(self.fmt(self.pick_by_cycle(MEAL["morning_read"])))
        asked = self.meal_moment()
        if not st["pending"] and st["cold"]:
            st["cold"] = False
            if "ask-cold" not in st["flags"]:
                st["flags"]["ask-cold"] = st["day"]
                self.ask("q_cold", MEAL["ask_cold"])
            else:
                self.say(self.fmt(MEAL["after_cold"]))
                self.her("safe", -2)
        if not st["pending"] and self.reads_her(6, 0.5) and "ask-how" not in st["flags"] \
                and self.cycle >= 3:
            st["flags"]["ask-how"] = st["day"]
            self.ask("q_how", MEAL["ask_how"])
        if not asked and not mood["lines"] and not st["pending"]:
            self.meal_beat()
        if not st["pending"]:
            self.say(self.meal_hint())

    def meal_hint(self):
        return MEAL["hint"] if len(self.st["meals_done"]) <= 4 else MEAL["hint_short"]

    def reads_her(self, at_least, ratio):
        """是不是一直在读她今天的样子：读对的次数够多，而且占她有状态的饭的不少。"""
        ratio *= 0.75   # 0.5 起放宽：读错现在有体感了，读对的门槛不必那么高
        st = self.st
        u, n = st.get("understood", 0), st.get("mood_meals", 0)
        return u >= at_least and u >= ratio * n

    def ask(self, qid, text=None):
        st = self.st
        st["pending"] = "ask"
        st["ask"] = qid
        if text:
            self.say("❓ " + self.fmt(text))
        for i, o in enumerate(ASKS[qid]["opts"], 1):
            self.say("  %d. %s" % (i, self.fmt(o["t"])))
        self.say("（choose <编号> 回答她）")

    def answer(self, k):
        st = self.st
        q = ASKS[st["ask"]]
        if not 1 <= k <= len(q["opts"]):
            return "choose <编号>（1~%d）" % len(q["opts"])
        o = q["opts"][k - 1]
        st["pending"], st["ask"] = None, None
        for key in ("fam", "safe"):
            if o.get(key):
                self.her(key, o[key])
        for key in ("near", "candor", "past"):
            if o.get(key):
                self.t(key, o[key])
        if o.get("understood"):
            st["understood"] = st.get("understood", 0) + o["understood"]
        if o.get("pwr"):
            st["pwr"] = clamp(st["pwr"] + o["pwr"], 0, 100)
        if o.get("flag"):
            st["flags"][o["flag"]] = st["day"]
        if o.get("stay"):
            st["flags"]["stayed-night"] = st["day"]
            self.end_meal(silent=True)
        if o.get("wake"):
            st["flags"]["wake-early"] = st["day"]
        if o.get("optimal"):
            st["optimal"] = st.get("optimal", 0) + 1
        if o.get("skipmeal"):
            key = self.slot(MEAL_HOURS[1])
            if key not in st["meals_done"]:
                st["meals_done"].append(key)
        if o.get("j"):
            self.j(o["j"], auto=True)
        L = [self.fmt(o.get("r_grief") if o.get("r_grief") and "knows-grief" in st["flags"] else o["r"])]
        if not st["meal"] and (o.get("loc") or o.get("hours")):
            if o.get("loc"):
                st["loc"] = o["loc"]
                if o["loc"] not in st["locs"]:
                    st["locs"].append(o["loc"])
            if o.get("hours"):
                self.advance(o["hours"])
            L.append("（现在是 %02d:00，你在【%s】。）" % (st["h"], LOCS[st["loc"]]["name"]))
        if st["meal"]:
            st["meal"]["acted"] = True
            if o.get("end"):
                L += self.meal_delta()
                self.end_meal(silent=True)
                if o.get("loc"):
                    st["loc"] = o["loc"]
                    if o["loc"] not in st["locs"]:
                        st["locs"].append(o["loc"])
                self.advance(2)
                L.append("（现在是 %02d:00，你在【%s】。）" % (st["h"], LOCS[st["loc"]]["name"]))
            else:
                L.append(self.meal_hint())
        return "\n".join(L)

    def day_event(self):
        """白天的小事：每天最多一件，在对的时间、对的地方发生。"""
        st = self.st
        if st["ending"] or st["pending"] or st["meal"] or st["flags"].get("dayev") == st["day"]:
            return None
        for ev in DAY_EVENTS:
            if ev["id"] in st["flags"] or not ev["cmin"] <= self.cycle <= ev.get("cmax", CYCLES):
                continue
            if not ev["hmin"] <= st["h"] <= ev["hmax"]:
                continue
            if ev.get("locs") and st["loc"] not in ev["locs"]:
                continue
            if ev.get("group") and "group-" + ev["group"] in st["flags"]:
                continue
            if self.cond_ok(ev.get("cond")):
                return ev
        return None

    def maybe_day_event(self):
        ev = self.day_event()
        if not ev:
            return
        st = self.st
        st["flags"][ev["id"]] = st["day"]
        st["flags"]["dayev"] = st["day"]
        if ev.get("group"):
            st["flags"]["group-" + ev["group"]] = st["day"]
        self.say("", "✧ " + self.fmt(ev["text"]))
        if ev.get("ask"):
            self.ask(ev["ask"])

    def meal_moment(self):
        st = self.st
        slot = "am" if st["h"] in (MEAL_HOURS[0], MEAL_HOURS[0] + 1) else "pm"
        for mo in sorted(MOMENTS, key=lambda x: not x.get("prio")):
            if mo["id"] in st["flags"]:
                continue
            if "cmin" in mo:
                if not mo["cmin"] <= self.cycle <= mo.get("cmax", CYCLES):
                    continue
                if mo.get("group") and "group-" + mo["group"] in st["flags"]:
                    continue
            elif mo["cycle"] != self.cycle or self.cday < mo.get("cday", 1):
                continue
            if mo.get("slot") not in (None, slot) or not self.cond_ok(mo.get("cond")):
                continue
            st["flags"][mo["id"]] = st["day"]
            if "？" in mo["text"] and not mo.get("ask") and st["meal"]:
                st["meal"]["open"] = True
            if mo.get("group"):
                st["flags"]["group-" + mo["group"]] = st["day"]
            if mo.get("flag"):
                st["flags"][mo["flag"]] = st["day"]
            self.say("✦ " + self.fmt(self.variant(mo)))
            for k in ("fam", "safe"):
                if mo.get(k):
                    self.her(k, mo[k])
            if mo.get("near"):
                self.t("near", mo["near"])
            for line in self.learn(mo.get("unlock", []), mo.get("fact")):
                self.say(line)
            if mo.get("j"):
                self.j(mo["j"], auto=True)
            if mo.get("ask"):
                self.ask(mo["ask"])
            return True
        return False

    def meal_beat(self):
        """没有饭桌小事的那几顿：她的一个小动作，按熟悉感挑。"""
        st = self.st
        r = self.rng()
        go = r.random() < 0.6
        self.save_rng(r)
        if not go:
            return
        tier = "hi" if st["fam"] >= 60 else ("mid" if st["fam"] >= 30 else "lo")
        lines = MEAL["beats"][tier]
        last = st["flags"].get("lastbeat")
        r = self.rng()
        line = r.choice([x for x in lines if x != last] or lines)
        self.save_rng(r)
        st["flags"]["lastbeat"] = line
        self.say(self.fmt(line))

    def fam_cap(self):
        return min(100, FAM_BASE + FAM_PER_CYCLE * self.cycle)

    def meal_delta(self):
        """吃完时的一句：这顿饭让她有什么变化。不给数字，只给方向。"""
        st, m = self.st, self.st["meal"]
        if not m:
            return []
        L = []
        ds = self.level("safe") - m["safe0"]
        if ds > 0:
            L.append(MEAL["delta"]["safe_up"])
        elif ds < 0:
            L.append(MEAL["delta"]["safe_down"])
        if self.level("fam") > m["fam0"]:
            L.append(MEAL["delta"]["fam_up"])
        elif st["fam"] >= self.fam_cap() and not m["cap0"] and self.cycle < CYCLES and st["fam"] < 100 \
                and "cap-told" not in st["flags"]:
            st["flags"]["cap-told"] = st["day"]
            L.append(MEAL["delta"]["fam_cap"])
        return ["（%s）" % x for x in L]

    def end_meal(self, silent=False):
        m = self.st["meal"]
        if not m:
            return
        self.st["meal"] = None
        if not silent:
            self.say(self.pick_by_cycle(MEAL["end"]))

    def pick_by_cycle(self, lines):
        """lines 是字符串或列表；列表按周期往后挑，并避开上一句。"""
        if isinstance(lines, str):
            return lines
        pool = lines[: min(len(lines), 1 + self.cycle * 2)]
        last = self.st["flags"].get("lastline")
        used = self.st.setdefault("used", [])
        choices = [x for x in pool if x not in used] or [x for x in pool if x != last] or pool
        r = self.rng()
        line = r.choice(choices)
        self.save_rng(r)
        self.st["flags"]["lastline"] = line
        if line not in used:
            used.append(line)
            del used[:-400]
        return line

    def need_meal(self):
        if not self.st["meal"]:
            return Fail("现在不是吃饭的时候，或者你不在食堂舱。（饭点 07:00、19:00）")
        return None

    def her(self, key, d):
        """她的熟悉感 / 安心感。到了高位，再往上涨就慢一半。"""
        v = self.st[key]
        if d > 0 and v >= 70:
            d = max(1, d // 2)
        top = 100
        if key == "fam":
            # 熟悉感是一个周期一个周期攒出来的：每个周期有上限
            top = self.fam_cap()
            if d > 0 and v >= top:
                return
        self.st[key] = clamp(v + d, 0, top if d > 0 else 100)

    def t(self, key, d):
        self.st["t"][key] = clamp(self.st["t"][key] + d)

    # ── 结局 ──────────────────────────────────
    def start_final(self):
        st = self.st
        st["loc"] = "mess"
        st["meal"] = None
        st["pending"] = "final"
        self.say(self.fmt(FINAL["text"]))
        for i, o in enumerate(self.final_opts(), 1):
            self.say("  %d. %s" % (i, o))
        self.say("（用 choose <编号> 做决定）")

    def final_opts(self):
        opts = list(FINAL["opts"])
        if "knows-grief" in self.st["flags"] and len(self.st.get("given", [])) >= 5:
            opts.append(FINAL["keep"])
        return opts

    def story_progress(self):
        story = [e for e in ARCHIVE if e.get("story")]
        return sum(1 for e in story if e["id"] in self.st["read"]) / max(1, len(story))

    def decide_ending(self, k):
        """返回 (结局, 选项)。读了几乎全部剧情、又一直照着过去做 → 一模一样；否则看她现在的状态。"""
        st, t = self.st, self.st["t"]
        if k == 4:
            return "keep", None
        if self.story_progress() >= 0.8 and t["past"] >= 30 and not self.reads_her(7, 0.8):
            return "same", k
        if k == 1:
            ok = st["safe"] >= 60
        elif k == 2:
            ok = st["safe"] >= 65 and t["near"] >= 30
        else:
            ok = st["safe"] >= 50 and t["near"] >= 30
        if not ok and self.story_progress() >= 0.8 and t["candor"] <= 0:
            st["flags"]["same-record"] = st["day"]
            return "same", k
        return ("choose" if ok else "fine"), k

    def finish(self, key, k=None):
        st = self.st
        st["ending"] = key
        st["pending"] = None
        tk = "samer" if key == "same" and "same-record" in st["flags"] else key
        text = ENDINGS.get("%s%s" % (tk, k or ""), ENDINGS.get(tk))
        text = text.replace("{progress}", "%.1f%%" % (self.progress() * 100))
        self.say(self.fmt(text))
        if key in ("choose", "fine"):
            u = st.get("understood", 0)
            if self.reads_her(7, 0.6):
                self.say(self.fmt(ENDINGS["understood_hi"]))
            elif u <= 2 or u < 0.3 * st.get("mood_meals", 0):
                self.say(self.fmt(ENDINGS["understood_lo"]))
            after = ENDINGS.get("after_%s%s" % (key, k or ""))
            if after:
                self.say(self.fmt(after))
        self.j(ENDINGS["j_" + tk], auto=True)
        if key == "choose" and st["t"]["candor"] >= TRUST_CANDOR:
            self.say(self.fmt(ENDINGS["candor_hi"]))
        if "handoff" in st["flags"]:
            self.say(self.fmt(HANDOFF["end_choose" if key == "choose" else "end_other"]))
        mine = [e["t"] for e in st["journal"] if e["me"]][-4:]
        # 她的便签：权重高的（关键线索）优先，其次最近的
        kept = [n for n in st["notes"] if n not in st.get("given", [])]
        key_notes = [n for n in kept if NOTES[n].get("fact")]
        rest = [n for n in kept if n not in key_notes]
        hers = [NOTES[n]["text"] for n in (key_notes[-2:] + rest[-1:])][:3]
        if mine or hers:
            self.say("", "—— 留下来的 ——")
            if mine:
                self.say("【你写下的】")
                self.say(*["  " + x for x in mine])
            if hers:
                self.say("【她写下的，还在你这里】")
                for x in st.get("spoken", []):
                    if x.get("written"):
                        self.say("  他说：“%s”。先记下来。下次醒来要看。" % x["t"])
                for h in hers:
                    self.say(*["  " + x for x in self.fmt(h).splitlines()])
                    self.say("")
        meals = sum(1 for e in st["meals_done"]) - sum(1 for e in st["journal"] if e["t"] == MEAL["missed_j"])
        story = [e for e in ARCHIVE if e.get("story")]
        self.say("", "（这一局：一起吃了 %d 顿饭，读了 %d/%d 条剧情记录，找到她的 %d 张便签%s，读对了她 %d 次。）"
                 % (meals, sum(1 for e in story if e["id"] in st["read"]), len(story), len(st["notes"]),
                    "（还给她 %d 张）" % len(st["given"]) if st.get("given") else "",
                    st.get("understood", 0)))
        self.say("（游戏结束。journal、notes、archive 可以回看；new 重新开始。）")

    # ── 指令分发 ──────────────────────────────
    def run(self, line):
        parts = line.strip().split()
        if not parts:
            return ""
        c, arg = parts[0].lower(), parts[1:]
        if self.st["ending"] and ALIAS.get(c, c) not in READ_ONLY:
            return Fail("这段航程已经结束了。（journal、notes、archive 等可以回看；new 重新开始）")
        if self.st["pending"] and c not in ("choose", "status", "journal", "help", "words", "facts", "notes", "summary", "speak"):
            return Fail("她在等你回答。（choose <编号>）" if self.st["pending"] == "ask" else "先做决定。（choose <编号>）")
        if self.st["meal"] and ALIAS.get(c, c) not in MEAL_OK:
            return Fail("你还在饭桌上。先 finish（吃完）或 quiet（安静吃完），再去做别的。"
                        "（吃饭时能用：say · tell · do · give · nod · quiet · finish，以及各种查看）")
        fn = getattr(self, "c_" + ALIAS.get(c, c), None)
        if not fn:
            return Fail("没有这个指令。help 看看。")
        return fn(arg)

    # 状态与查看 ─────────────────────────────
    def c_help(self, arg):
        return HELP

    def c_status(self, arg):
        st, t = self.st, self.st["t"]
        L = ["【状态】第 %d 天 %02d:00 · 第 %d/%d 个清醒周期（本周期第 %d/%d 天）"
             % (st["day"], st["h"], self.cycle, CYCLES, self.cday, DAYS_PER_CYCLE),
             "你：%s　位置：%s　体力 %d/100　船上电力 %d（每晚生命维持 -%d，早上 +%d）"
             % (st["name"] or "（还没起名，name <名字>）", LOCS[st["loc"]]["name"],
                st["sta"], st["pwr"], LIFE_SUPPORT, REACTOR)]
        if st["items"]:
            L.append("随身：" + "、".join("%s×%d" % (ITEMS[k]["name"], n)
                                         for k, n in st["items"].items()))
        L += self.mood_lines()
        today = MOODS.get(st.get("mood", "normal"), {}).get("status")
        if today and "seen-mood-%d" % st["day"] in st["flags"]:
            L.append("· 她今天" + today)
        L += self.scales()
        mine = sum(1 for e in st["journal"] if e["me"])
        if mine:
            L.append("你的日志里有 %d 条你自己写的话。（journal）" % mine)
        return "\n".join(L)

    def mood_lines(self):
        st, t, S = self.st, self.st["t"], STATUS_TEXT
        L = []
        strongest = sorted(t.items(), key=lambda kv: -abs(kv[1]))
        for k, v in strongest[:2]:
            if abs(v) >= 20:
                L.append("· " + S[k + ("+" if v > 0 else "-")])
        if not L:
            L.append("· " + S["neutral"])
        lf, ls = self.level("fam"), self.level("safe")
        fam = "fam_hi" if lf >= 4 else ("fam_mid" if lf >= 2 else "fam_lo")
        safe = "safe_hi" if ls >= 4 else ("safe_mid" if ls >= 3 else "safe_lo")
        L.append("· " + self.fmt(S[fam]))
        L.append("· " + self.fmt(S[safe]))
        return L

    def scales(self):
        st = self.st

        def bar(n):
            return "●" * n + "○" * (5 - n)
        L = ["她熟悉你　%s" % bar(self.level("fam")),
             "她在你身边　%s" % bar(self.level("safe")),
             "你想靠近她　%s" % bar(self.level("near"))]
        if st["fam"] >= self.fam_cap() and st["fam"] < 100 and self.cycle < CYCLES:
            L.append("（这几天，她和你就熟到这儿了。）")
        return L

    def level(self, key):
        st = self.st
        v = st["t"]["near"] + 50 if key == "near" else st[key]
        return max(0, min(5, (v + 10) // 20))

    def c_map(self, arg):
        L = ["【迟光号】"]
        for k in self.st["locs"]:
            v = LOCS[k]
            mark = "✦" if k == self.st["loc"] else "·"
            need = v.get("need")
            lock = ""
            if need and not self.st["items"].get(need):
                lock = "（需要%s）" % ITEMS[need]["name"]
            L.append(" %s %s　%s　%s" % (mark, v["name"], k, lock))
        L.append("移动 1 小时。饭点 07:00、19:00 在食堂舱。")
        return "\n".join(L)

    def c_look(self, arg):
        loc = LOCS[self.st["loc"]]
        if not arg:
            objs = loc.get("objects", {})
            return loc["desc"] + ("\n（可以细看：%s）" % "、".join(objs) if objs else "")
        q = " ".join(arg)
        objs = loc.get("objects", {})
        ok = self.resolve(q, {k: dict(v, name=k) for k, v in objs.items()})
        if ok:
            o = objs[ok]
            ev = next((e for e in EVENTS if e["id"] == o.get("event")), None)
            if ev and ev["id"] not in self.st["flags"] and self.cond_ok(ev.get("cond")):
                return "\n".join(self.run_event(ev))
            L = ["【%s】%s" % (ok, self.fmt(o["text"]))] + self.learn(o.get("unlock", []))
            return "\n".join(L)
        k = self.resolve(q, ITEMS)
        if k and self.st["items"].get(k):
            return "【%s】%s" % (ITEMS[k]["name"], ITEMS[k]["desc"])
        k = self.resolve(q, LOCS)
        if k and k in self.st["locs"]:
            return "【%s】%s" % (LOCS[k]["name"], LOCS[k]["desc"])
        return "看不到这个。"

    def c_words(self, arg):
        if not self.st["words"]:
            return "你还没有记下什么关键词。她的反应、档案和便签会给你一些。"
        def mark(w):
            hit = any(w in e["kw"] for e in ARCHIVE)
            said = w in self.st["cyc"]["reacted"]
            return WORDS[w]["name"] + ("🔍" if hit else "") + ("·聊过" if said else "")
        return ("【你记下的关键词】\n " + "、".join(mark(w) for w in self.st["words"]) +
                "\n（🔍 = 档案里查得到；·聊过 = 这个周期已经和她聊过）\n"
                "（search <词> 查档案，%d 电 / 1 小时 / 最多 3 条；饭桌上 say <词> 提起）" % SEARCH_PWR)

    def c_facts(self, arg):
        if not self.st["facts"]:
            return "你还没有从过去里确认什么事。"
        L = ["【你知道的事】（饭桌上 tell <编号或名字> 告诉她）"]
        for i, f in enumerate(self.st["facts"], 1):
            L.append(" %d. %s　%s" % (i, FACTS[f]["name"], self.fmt(FACTS[f]["text"])))
        return "\n".join(L)

    def c_name(self, arg):
        if not arg:
            if self.st["name"]:
                return "你现在叫 %s。（name <新名字> 可以改）" % self.st["name"]
            return "name <名字>：给自己起个名字。她会这样叫你。"
        full = " ".join(arg)
        n = full[:12]
        cut = "（名字最长 12 个字，后面的截掉了）" if len(full) > 12 else ""
        old = self.st["name"]
        self.st["name"] = n
        if old:
            return "你把名字改成了 %s。%s" % (n, cut)
        self.j("我给自己起名叫 %s。" % n, auto=True)
        return "名牌上写着：%s。%s" % (n, cut)

    # 行动 ───────────────────────────────────
    def c_goto(self, arg):
        if not arg:
            return "goto <地点>（map 看地点）"
        k = self.resolve(" ".join(arg), LOCS)
        if not k or k not in self.st["locs"]:
            return Fail("船上没有这个地方，或者你还不知道它在哪。")
        if k == self.st["loc"]:
            return "你就在这里。"
        need = LOCS[k].get("need")
        if need and not self.st["items"].get(need):
            return Fail("那里太冷了，进不去。需要%s。" % ITEMS[need]["name"])
        self.st["loc"] = k
        self.st["sta"] = clamp(self.st["sta"] - MOVE_STA, 0, 100)
        self.advance(1)
        seen = "seen-" + k
        if seen in self.st["flags"]:
            return "来到【%s】。" % LOCS[k]["name"]
        self.st["flags"][seen] = 1
        return "来到【%s】。%s" % (LOCS[k]["name"], LOCS[k]["desc"])

    def next_meal(self):
        st = self.st
        for d in (0, 1):
            for mh in MEAL_HOURS:
                if (d or st["h"] <= mh) and "%d-%d" % (st["day"] + d, mh) not in st["meals_done"]:
                    return (mh - st["h"]) % 24 if (d or st["h"] < mh) else 0, mh
        return None

    def c_wait(self, arg):
        if arg and arg[0] in ("meal", "饭", "饭点", "吃饭"):
            nm = self.next_meal()
            if not nm:
                return Fail("没有下一顿饭了。")
            arg = [str(min(12, nm[0]))]
        try:
            n = int(arg[0]) if arg else 1
        except ValueError:
            return "wait <小时>（1~12），或 wait meal 等到下一顿饭"
        if n < 1:
            return "wait <小时>（1~12）"
        n = min(12, n)
        want = n
        stop = self.meal_ahead(n)
        away = self.st["loc"] != "mess"
        if stop is not None:
            # 不在食堂舱：提前一小时停，留出走过去的时间
            target = stop - 1 if away else stop
            n = max(0, target - self.st["h"])
        if not n:
            return Fail("现在就该去食堂舱了（%02d:00 的饭点）。没有等。" % stop)
        for i in range(n):
            self.advance(1)
            if self.day_event():
                if i + 1 < n:
                    stop, n = None, i + 1
                break
        msg = "你等了 %d 小时。现在第 %d 天 %02d:00。" % (n, self.st["day"], self.st["h"])
        if stop is not None and n < want:
            msg += ("（%02d:00 是饭点，她会在食堂舱。先停下了，goto mess 正好赶上。）" % stop
                    if away else "（在饭点停下了。）")
        else:
            nm = self.next_meal()
            if nm and 0 < nm[0] <= 12 and not self.st["meal"]:
                msg += "（下一顿饭 %02d:00，还有 %d 小时。）" % (nm[1], nm[0])
        return msg

    def c_explore(self, arg):
        n, stop = 1, set()
        for a in arg:
            if a.startswith("stop="):
                stop = set(a[5:].split(",")) if a[5:] else set()
            else:
                try:
                    n = int(a)
                except ValueError:
                    pass
        n = max(1, min(10, n))
        L = []
        for i in range(n):
            if self.st["sta"] < EXPLORE_STA:
                L.append("⛔ 体力不够了（%d）。回宿舍舱 sleep，或者等饭点。" % self.st["sta"])
                break
            soon = self.meal_soon(2)
            if soon is not None:
                L.append("（%02d:00 是饭点，她会在食堂舱。先停下了。）" % soon)
                break
            if n > 1:
                L.append("— 第 %d 次 —" % (i + 1))
            kind, lines = self.explore_once()
            L += lines
            if self.st["pending"] or kind in stop or self.st["meal"]:
                if kind in stop and i + 1 < n:
                    L.append("（找到了就停下。剩下 %d 次没有走。）" % (n - i - 1))
                break
            if self.final_due():
                break
            if self.st["loc"] == "mess" and self.st["h"] in MEAL_HOURS and \
                    self.slot() not in self.st["meals_done"]:
                L.append("（饭点到了，她端着两个杯子坐下了。）")
                break
        return "\n".join(L)

    def meal_ahead(self, hours):
        """接下来 hours 小时内还没吃的饭点（不管你在哪）。返回那个钟点。"""
        st = self.st
        if st["day"] > LAST_DAY:
            return None
        for mh in MEAL_HOURS:
            if st["h"] < mh <= st["h"] + hours and self.slot(mh) not in st["meals_done"]:
                return mh
        return None

    def meal_soon(self, hours):
        """接下来 hours 小时内有没赶上的饭点（且你不在食堂舱）？返回那个钟点。"""
        st = self.st
        if st["loc"] == "mess" or st["day"] > LAST_DAY:
            return None
        for mh in MEAL_HOURS:
            if st["h"] < mh <= st["h"] + hours and self.slot(mh) not in st["meals_done"]:
                return mh
        return None

    def explore_once(self):
        st = self.st
        loc = st["loc"]
        st["sta"] = clamp(st["sta"] - EXPLORE_STA, 0, 100)
        self.advance(2)
        if st["day"] == LAST_DAY and loc in FLAVOR_LAST and "last-" + loc not in st["flags"]:
            st["flags"]["last-" + loc] = 1
            return "flavor", [self.fmt(FLAVOR_LAST[loc])]
        r = self.rng()
        pool = []
        notes_here = [nid for nid, nv in NOTES.items()
                      if nv["loc"] == loc and nid not in st["notes"] and self.cond_ok(nv.get("cond"))]
        dry = st["flags"].get("dry-" + loc, 0)
        if notes_here and dry >= 2:
            # 连着两次在这里什么都没找到：下一次一定有一张她的便签
            st["flags"]["dry-" + loc] = 0
            self.save_rng(r)
            return "note", self.find_note(notes_here[0])
        for nid in notes_here:
            pool.append(("note", nid, NOTES[nid].get("w", 10)))
        for ev in EVENTS:
            if loc in ev["loc"] and self.cond_ok(ev.get("cond")) and \
                    st["cd"].get(ev["id"], -99) + ev.get("cd", 3) <= st["day"]:
                if ev.get("once") and ev["id"] in st["flags"]:
                    continue
                pool.append(("event", ev, ev.get("w", 10)))
        pool.append(("flavor", None, 14))
        tot = sum(w for _, _, w in pool)
        x = r.uniform(0, tot)
        pick = pool[-1]
        for p in pool:
            x -= p[2]
            if x <= 0:
                pick = p
                break
        self.save_rng(r)
        kind, obj, _ = pick
        st["flags"]["dry-" + loc] = 0 if kind != "flavor" else dry + 1
        if kind == "note":
            return "note", self.find_note(obj)
        if kind == "event":
            return "event", self.run_event(obj)
        lines = FLAVOR.get(loc, FLAVOR["_"])
        last = st["flags"].get("flavor-" + loc)
        choices = [x for x in lines if x != last] or lines
        r = self.rng()
        line = r.choice(choices)
        self.save_rng(r)
        st["flags"]["flavor-" + loc] = line
        return "flavor", [self.fmt(line)]

    def variant(self, v, key="text"):
        """v[key] 是默认文字；v["var"] 是 [[条件, 文字], ...]，取最后一条满足的。"""
        text = v[key]
        for cond, t in v.get("var", []):
            if self.cond_ok(cond):
                text = t
        return text

    def cond_ok(self, c):
        if not c:
            return True
        st = self.st
        if "flag" in c and c["flag"] not in st["flags"]:
            return False
        if "noflag" in c and c["noflag"] in st["flags"]:
            return False
        if "fact" in c and c["fact"] not in st["facts"]:
            return False
        if "item" in c and not st["items"].get(c["item"]):
            return False
        if "noitem" in c and st["items"].get(c["noitem"]):
            return False
        if "cycle" in c and self.cycle < c["cycle"]:
            return False
        if "maxcycle" in c and self.cycle > c["maxcycle"]:
            return False
        if "fam" in c and st["fam"] < c["fam"]:
            return False
        if "safe" in c and st["safe"] < c["safe"]:
            return False
        if "near" in c and st["t"]["near"] < c["near"]:
            return False
        if "any" in c and not any(f in st["flags"] for f in c["any"]):
            return False
        if "maxnear" in c and st["t"]["near"] > c["maxnear"]:
            return False
        if "minpast" in c and st["t"]["past"] < c["minpast"]:
            return False
        if "maxpast" in c and st["t"]["past"] > c["maxpast"]:
            return False
        if "notes" in c and len(st["notes"]) < c["notes"]:
            return False
        if c.get("reads") and not self.reads_her(5, 0.5):
            return False
        if "mincandor" in c and st["t"]["candor"] < c["mincandor"]:
            return False
        if "cday" in c and self.cday != c["cday"]:
            return False
        if "yesterday" in c and st["flags"].get(c["yesterday"]) != st["day"] - 1:
            return False
        if "after" in c and not (c["after"] in st["flags"] and st["flags"][c["after"]] < st["day"]):
            return False
        if "optimal" in c and st.get("optimal", 0) < c["optimal"]:
            return False
        return True

    def run_event(self, ev):
        st = self.st
        st["cd"][ev["id"]] = st["day"]
        st["flags"][ev["id"]] = st["day"]
        L = ["❗ " + self.fmt(ev["text"])]
        for k, n in ev.get("gain", {}).items():
            st["items"][k] = st["items"].get(k, 0) + n
            L.append("获得：%s×%d" % (ITEMS[k]["name"], n))
        if ev.get("pwr"):
            st["pwr"] = clamp(st["pwr"] + ev["pwr"], 0, 100)
        if ev.get("sta"):
            st["sta"] = clamp(st["sta"] + ev["sta"], 0, 100)
        if ev.get("flag"):
            st["flags"][ev["flag"]] = st["day"]
        if ev.get("unlock_loc") and ev["unlock_loc"] not in st["locs"]:
            st["locs"].append(ev["unlock_loc"])
            L.append("🗺️ 新地点：【%s %s】" % (LOCS[ev["unlock_loc"]]["name"], ev["unlock_loc"]))
        L += self.learn(ev.get("unlock", []), ev.get("fact"))
        if ev.get("j"):
            self.j(ev["j"], auto=True)
        return L

    def find_note(self, nid):
        st = self.st
        st["notes"].append(nid)
        nv = NOTES[nid]
        self.t("near", 2)
        L = ["📝 你找到一张她的便签（#%d）：" % len(st["notes"])]
        L += ["　" + x for x in self.fmt(nv["text"]).splitlines()]
        if nv.get("unlock_loc") and nv["unlock_loc"] not in st["locs"]:
            st["locs"].append(nv["unlock_loc"])
            L.append("🗺️ 新地点：【%s %s】" % (LOCS[nv["unlock_loc"]]["name"], nv["unlock_loc"]))
        L += self.learn(nv.get("unlock", []), nv.get("fact"))
        if len(st["notes"]) == 1:
            L.append("（她把想记住的写在便签上。你也可以写点什么：journal add <想写的话>）")
        return L

    def learn(self, words, fact=None):
        st, L = self.st, []
        new = [w for w in words if w not in st["words"]]
        st["words"] += new
        if new:
            L.append("🔑 记下关键词：" + "、".join(WORDS[w]["name"] for w in new))
        if fact and fact not in st["facts"]:
            st["facts"].append(fact)
            L.append("📌 你确认了一件事：%s（facts）" % FACTS[fact]["name"])
            if FACTS[fact].get("act"):
                L.append("　（饭桌上多了一件可以做的事：%s）" % ACTS[FACTS[fact]["act"]]["name"])
        return L

    @staticmethod
    def pick_index(q, seq):
        """q 是编号时：合法返回元素，不合法返回 False；不是编号返回 None。"""
        try:
            i = int(q)
        except ValueError:
            return None
        return seq[i - 1] if 1 <= i <= len(seq) else False

    def c_notes(self, arg):
        written = [x for x in self.st.get("spoken", []) if x.get("written")]
        if not self.st["notes"]:
            if written:
                return "\n".join(["【她记下的你说的话】"] + ["　他说：“%s”" % x["t"] for x in written])
            return "你还没找到她的便签。她不写日志，便签散在船里各处。"
        if arg:
            nid = self.pick_index(arg[0], self.st["notes"])
            if not nid:
                return Fail("没有这张。notes 看列表。")
            i = int(arg[0])
            return "📝 #%d\n" % i + self.fmt(NOTES[nid]["text"])
        L = ["【她的便签】%d 张（notes <编号> 重读）" % len(self.st["notes"])]
        given = self.st.get("given", [])
        for i, nid in enumerate(self.st["notes"], 1):
            L.append(" #%d %s……%s" % (i, self.fmt(NOTES[nid]["text"]).splitlines()[0][:18],
                                      "（已还给她）" if nid in given else ""))
        if len(self.st["notes"]) > len(given):
            L.append("（饭桌上 give <编号> 可以把一张还给她）")
        if written:
            L.append("【她记下的你说的话】")
            L += ["　他说：“%s”" % x["t"] for x in written]
        return "\n".join(L)

    c_read = c_notes

    def c_sleep(self, arg):
        st = self.st
        if st["loc"] != "quarters":
            return Fail("要回宿舍舱才能睡。")
        if self.cday == DAYS_PER_CYCLE and st["h"] >= 6:
            if st["h"] < MEAL_HOURS[1] + 1:
                return Fail("今晚要进休眠舱。休眠舱在晚饭后才开放（20:00 以后）。")
            return self.hibernate()
        if 6 <= st["h"] < 20 and st["sta"] > 30:
            return Fail("还不困。（20:00 以后，或者体力低于 30 才能睡）")
        hours = (30 - st["h"]) % 24 or 24
        self.end_meal()
        self.advance(hours)
        st["sta"] = 70 if st["cold"] else 100
        msg = "你睡到第 %d 天 06:00。" % st["day"]
        if st["flags"].get("wake-early") == st["day"] - 1:
            msg += "\n" + self.fmt(MEAL["wake_early"])
        if st["cold"]:
            msg += "夜里电力不够，船舱很冷，没睡好。"
        return msg

    def hibernate(self):
        st = self.st
        self.end_meal()
        st["day"] += 1
        st["h"] = 6
        st["loc"] = "quarters"
        st["sta"] = 100
        st["cold"] = False
        before = dict(st["t"])
        for k in st["t"]:
            st["t"][k] = int(st["t"][k] * 0.6)
        # 醒来：熟悉感往上走一点；安心感往中间回落——又要重新认识一次
        st["fam"] = clamp(st["fam"] + 6, 0, 100)
        st["safe"] = int(50 + (st["safe"] - 50) * 0.7)
        st["pwr"] = clamp(st["pwr"] + 20, 0, 100)
        self.morning()
        self.new_cycle_state()
        if st["flags"].get("stayed-night") in (st["day"], st["day"] - 1):
            L = ["💤 她已经睡着了。你躺进自己的休眠舱，盖子合上之前，又往她那边看了一眼。", ""]
        else:
            L = ["💤 你和她各自躺进休眠舱。盖子合上之前，她隔着两米朝你点了点头。", ""]
        L += [self.fmt(x) for x in WAKE["common"]]
        drop = sum(abs(before[k]) - abs(st["t"][k]) for k in before)
        if drop >= 10:
            L.append(self.fmt(WAKE["drop"]))
        L.append(self.fmt(WAKE["cycles"].get(str(self.cycle), WAKE["cycles"]["_"])))
        self.j("第 %d 次醒来。" % self.cycle, auto=True)
        return "\n".join(L)

    def c_search(self, arg):
        st = self.st
        if st["loc"] != "archive":
            return "档案要在档案舱查。"
        if not arg:
            return "search <词>。不知道查什么？words 看看你记下的关键词。"
        if st["pwr"] < SEARCH_PWR:
            return Fail("电力不够，档案终端开不起来。")
        q = " ".join(arg).strip().lower()
        wid = self.resolve(q, WORDS)
        match = [e for e in ARCHIVE if (q in e["kw"] or (wid and wid in e["kw"]))
                 and (not e.get("after") or e["after"] in st["read"])]
        if match and all(e["id"] in st["read"] for e in match):
            return "检索“%s”：相关的 %d 条记录你都读过了。（没有消耗电力）" % (" ".join(arg), len(match))
        if not match:
            if wid and wid in st["words"]:
                return ("检索“%s”：档案里没有和它直接相关的记录。（没有消耗电力）\n"
                        "这个词更适合在饭桌上提。" % " ".join(arg))
            return ("检索“%s”：没有结果。（没有消耗电力）\n"
                    "（试试 words 里带 🔍 的词，或者她最近在饭桌上、便签里提过的东西。）" % " ".join(arg))
        st["pwr"] -= SEARCH_PWR
        st["sta"] = clamp(st["sta"] - SEARCH_STA, 0, 100)
        self.advance(1)
        dark = st["pwr"] <= 0 or st["cold"]
        hits = [e for e in ARCHIVE if e["id"] not in st["read"] and
                (q in e["kw"] or (wid and wid in e["kw"])) and (not e.get("after") or e["after"] in st["read"])]
        if not hits:
            seen = any(q in e["kw"] or (wid and wid in e["kw"]) for e in ARCHIVE)
            return "检索“%s”：%s" % (" ".join(arg), "相关记录都读过了。" if seen else "没有结果。")
        r = self.rng()
        story = [e for e in hits if e.get("story")]
        hidden = [e for e in hits if e.get("hidden")]
        filler = [e for e in hits if not e.get("story") and not e.get("hidden")]
        r.shuffle(filler)
        pick = (story + hidden + filler)[:3]
        self.save_rng(r)
        n_story = sum(1 for e in pick if e.get("story") or e.get("hidden"))
        L = ["检索“%s”：%d 条未读，显示 %d 条%s。（电力 -%d，1 小时）"
             % (" ".join(arg), len(hits), len(pick),
                "，其中 %d 条不是例行记录" % n_story if n_story else "，都是例行记录", SEARCH_PWR)]
        for e in pick:
            st["read"].append(e["id"])
            st["cyc"]["reads"] += 1 if e.get("story") else 0
            self.t("past", 2 if e.get("story") else 1)
            tag = "🎙 录音" if e["kind"] == "rec" else "📡 通讯"
            L.append("")
            L.append("%s %s" % (tag, e["head"]))
            L += ["　" + x for x in self.fmt(e["text"]).splitlines()]
            L += self.learn(e.get("unlock", []), e.get("fact"))
            if e.get("flag"):
                st["flags"].setdefault(e["flag"], st["day"])
        if dark:
            L.append("（终端闪了一下。电力见底了，今晚船舱会冷。）")
        return "\n".join(L)

    def c_timeline(self, arg):
        st = self.st
        if st["loc"] != "archive":
            return "工作日志要在档案舱看。"
        try:
            m = int(arg[0])
        except (ValueError, IndexError):
            marks = []
            for i in range(1, 25):
                tl = TIMELINE[str(i)]
                busy = tl.get("unlock") or not tl["text"].startswith("例行")
                marks.append("%d%s%s" % (i, "✦" if busy else "", "✓" if i in st["months"] else ""))
            return ("timeline <月份 1~24>。这是你上一段航程的工作日志。\n " + " ".join(marks) +
                    "\n（✦ = 那个月不只是例行；✓ = 看过）")
        if str(m) not in TIMELINE:
            return Fail("没有这个月的日志。（1~24）")
        if st["pwr"] < TIMELINE_PWR:
            return Fail("电力不够。")
        st["pwr"] -= TIMELINE_PWR
        self.advance(1)
        first = m not in st["months"]
        if first:
            st["months"].append(m)
        tl = TIMELINE[str(m)]
        L = ["【工作日志 · 第 %d 个月】" % m] + ["　" + x for x in self.fmt(tl["text"]).splitlines()]
        if first:
            L += self.learn(tl.get("unlock", []), tl.get("fact"))
        return "\n".join(L)

    def c_archive(self, arg):
        st = self.st
        read = [e for e in ARCHIVE if e["id"] in st["read"]]
        if arg:
            if arg[0] == "all":
                show = read
            else:
                e = self.pick_index(arg[0], read)
                if not e:
                    return Fail("没有这一条。archive 看列表。")
                tag = "🎙 录音" if e["kind"] == "rec" else "📡 通讯"
                if e.get("flag"):
                    st["flags"].setdefault(e["flag"], st["day"])
                return "%s %s（重读，不耗电）\n" % (tag, e["head"]) + \
                    "\n".join("　" + x for x in self.fmt(e["text"]).splitlines())
        else:
            show = [e for e in read if e.get("story") or e.get("hidden")]
        n_story = sum(1 for e in ARCHIVE if e.get("story"))
        n_story_read = sum(1 for e in ARCHIVE if e.get("story") and e["id"] in st["read"])
        n_hidden = sum(1 for e in ARCHIVE if e.get("hidden") and e["id"] in st["read"])
        L = ["【档案】不是例行公事的记录：读过 %d/%d 条。例行记录读过 %d 条。工作日志看过 %d/24 个月。"
             % (n_story_read, n_story, len(st["read"]) - n_story_read - n_hidden, len(st["months"]))]
        for e in show:
            L.append(" %d. %s%s" % (read.index(e) + 1, e["head"],
                                    "" if e.get("story") or e.get("hidden") else "（例行）"))
        unread = [e for e in ARCHIVE if e.get("story") and e["id"] not in st["read"]]
        if unread and not (arg and arg[0] == "all"):
            L.append("还没读到的：" + "、".join(e["head"].split(" · ")[0] + "（???）" for e in unread))
        if show:
            missing = sum(1 for e in ARCHIVE if e.get("story") and e["id"] not in st["read"])
            if missing:
                L.append("（还有 %d 条不是例行的记录没读到。）" % missing)
            L.append("（archive <编号> 重读，不耗电；archive all 连例行记录一起列出）")
        return "\n".join(L)

    def c_calibrate(self, arg):
        st = self.st
        if st["loc"] != "bridge":
            return Fail("航线校准要在舰桥做。")
        key = "cal-%d" % st["day"]
        if key in st["flags"]:
            return Fail("今天已经校准过了。")
        if st["pwr"] >= 100:
            return "电力是满的，今天不用校准。"
        st["flags"][key] = 1
        st["pwr"] = clamp(st["pwr"] + 8, 0, 100)
        self.advance(1)
        return self.fmt(self.pick_by_cycle(MISC["calibrate"])) + "（电力 +8）"

    # 饭桌上的事 ─────────────────────────────
    def c_say(self, arg):
        e = self.need_meal()
        if e:
            return e
        m, st = self.st["meal"], self.st
        if not arg:
            return "say <词>：提起一个词或一样东西，看她的反应。"
        q = " ".join(arg)
        wid = self.resolve(q, WORDS)
        if wid and WORDS[wid].get("react") and wid in st["cyc"]["reacted"] and wid != "restart":
            # 这个周期聊过的话题：给一句短回应，不占次数，也不加数值
            m["acted"] = True
            again = WORDS[wid].get("again")
            k = "again-" + wid
            if again and k not in st["cyc"]:
                st["cyc"][k] = True
                return self.fmt(again)
            used = st["cyc"].setdefault("again-used", [])
            pool = [x for x in MEAL["said_again"] if x not in used] or MEAL["said_again"]
            line = pool[(st["day"] + len(used)) % len(pool)]
            used.append(line)
            return self.fmt(line)
        known = wid and WORDS[wid].get("react")
        if not known:
            m["unknown"] += 1
            if m["unknown"] > 3:
                return "她看着你，等你想好要说什么。（这顿饭试了好几个词了；words 看看你记下的关键词）"
            r = self.rng()
            line = self.fmt(r.choice(FLAT_REPLIES)).replace("{q}", q)
            self.save_rng(r)
            return line + "\n" + MEAL["unknown_hint"]
        if m["says"] >= MAX_SAYS:
            return "这顿饭已经说了不少了。（每顿最多提起 %d 个新话题；聊过的话题不算）" % MAX_SAYS
        m["says"] += 1
        m["acted"] = True
        if wid == "restart" and st["day"] <= 8 and self.progress() < 0.05:
            self.finish("restart")
            return ""
        w = WORDS[wid]
        st["cyc"]["reacted"].append(wid)
        text = w["react"][0][1]
        for entry in w["react"]:
            need, line = entry[0], entry[1]
            if st["fam"] >= need and self.cycle >= (entry[2] if len(entry) > 2 else 1) and \
                    self.cond_ok(entry[3] if len(entry) > 3 else None):
                text = line
        self.her("fam", w.get("fam", 2))
        self.t("near", 1)
        L = [self.fmt(text)]
        if not m.get("said_mood"):
            m["said_mood"] = True
            mr = self.mood_react("say")
            if mr:
                L.append(mr)
        L += self.learn(w.get("unlock", []), w.get("fact"))
        if w.get("fam", 2) >= 3:
            L += self.excuse("you")
        return "\n".join(L)

    def excuse(self, who):
        """借口线：每个周期，她和你各给自己的靠近找一次理由。越往后越站不住。"""
        st = self.st
        key = "excuse_" + who
        if st["cyc"].get(key):
            return []
        line = EXCUSES[who].get(str(self.cycle))
        if not line:
            return []
        st["cyc"][key] = True
        L = [self.fmt(line)]
        if who == "her":
            L += self.learn(EXCUSES.get("unlock", []))
            if self.cycle == EXCUSES.get("drop_cycle") and "excuse-dropped" not in st["flags"]:
                st["flags"]["excuse-dropped"] = st["day"]
                self.j(EXCUSES["drop_j"], auto=True)
        return L

    def c_tell(self, arg):
        e = self.need_meal()
        if e:
            return e
        st, m = self.st, self.st["meal"]
        if not arg:
            return "tell <事>：把你知道的一件事告诉她。facts 看你知道什么。"
        if m["tells"] >= MAX_TELLS:
            return Fail("一顿饭说一件就够了。")
        q = " ".join(arg)
        fid = self.pick_index(q, st["facts"])
        if fid is False:
            return Fail("没有这个编号。facts 看你知道什么。")
        if fid is None:
            fid = self.resolve(q, {k: FACTS[k] for k in st["facts"]})
        if not fid:
            return Fail("你不确定这件事。facts 看你知道什么。")
        told = st["cyc"].setdefault("told", [])
        if fid in told:
            return Fail("这个周期你已经告诉过她这件事了。")
        told.append(fid)
        st["flags"]["told-" + fid] = st["day"]
        if sum(1 for f in st["flags"] if f.startswith("told-f_")) >= 2:
            st["flags"].setdefault("knows-past", st["day"])
        m["tells"] += 1
        m["acted"] = True
        st["cyc"]["tells"] += 1
        f = FACTS[fid]["tell"]
        trusted = st["t"]["candor"] >= TRUST_CANDOR
        self.her("fam", f.get("fam", 3))
        self.her("safe", 1 if trusted else f.get("safe", -2))
        self.t("candor", 6)
        self.t("past", 2)
        L = [self.fmt(f["text"])]
        if trusted and "tell-trusted" not in st["cyc"] and not FACTS[fid]["tell"].get("heavy"):
            st["cyc"]["tell-trusted"] = True
            L.append(self.fmt(MEAL["tell_trusted"]))
        if st["cyc"]["tells"] > TELLS_BEFORE_PRESSURE:
            self.her("safe", -6)
            L.append(self.fmt(MEAL["pressure"]))
        return "\n".join(L)

    def available_acts(self):
        acts = [a for a, v in ACTS.items() if v.get("base")]
        acts += [FACTS[f]["act"] for f in self.st["facts"] if FACTS[f].get("act")]
        return [a for a in acts if self.cond_ok(ACTS[a].get("cond"))]

    def c_do(self, arg):
        e = self.need_meal()
        st, m = self.st, self.st["meal"]
        acts = self.available_acts()
        if not arg:
            L = ["【能做的事】（do <名字>，每顿一件）"]
            for a in acts:
                src = "　← 从过去知道的" if ACTS[a].get("past", 0) > 0 else ""
                if ACTS[a].get("once") and st["cyc"]["acts"].get(a):
                    src += "（这个周期做过了）"
                L.append(" · %s%s" % (ACTS[a]["name"], src))
            if e:
                L.append(e)
            return "\n".join(L)
        if e:
            return e
        if m["dos"] >= MAX_DOS:
            return Fail("这顿饭已经做过一件事了。")
        a = self.resolve(" ".join(arg), {k: ACTS[k] for k in acts})
        if not a:
            return Fail("现在做不了这个。能做的有：" + "、".join(ACTS[x]["name"] for x in acts))
        if ACTS[a].get("once") and st["cyc"]["acts"].get(a):
            return Fail("这个周期已经一起做过了。下次醒来再说吧。")
        m["dos"] += 1
        m["acted"] = True
        m["did"] = a
        v = ACTS[a]
        used = st["cyc"]["acts"].get(a, 0)
        st["cyc"]["acts"][a] = used + 1
        k = 1 if used == 0 else 0.5
        self.t("near", int(v.get("near", 2) * k))
        self.t("past", int(v.get("past", 0) * k))
        self.her("safe", int(v.get("safe", 0) * k))
        self.her("fam", int(v.get("fam", 0) * k))
        st["flags"]["did-" + a] = st["day"]
        if used == 0:
            text = self.variant(v)
            seen = st.setdefault("used", [])
            if text in seen and "again" in v:
                text = self.pick_by_cycle(v["again"])
            elif text not in seen:
                seen.append(text)
        else:
            text = self.pick_by_cycle(v["again"]) if "again" in v else self.variant(v)
        L = [self.fmt(text)]
        mr = self.mood_react(a)
        if mr:
            L.append(mr)
        if v.get("near", 2) > 0:
            L += self.excuse("her")
        if v.get("goes"):
            # 一起去了别处：这顿饭就算吃完了，人也真的到了那里
            L += self.meal_delta()
            self.end_meal(silent=True)
            self.st["loc"] = v["goes"]
            if v["goes"] not in self.st["locs"]:
                self.st["locs"].append(v["goes"])
            self.advance(v.get("hours", 1))
            L.append("（你们在【%s】。这顿饭就这样吃完了。）" % LOCS[v["goes"]]["name"])
        return "\n".join(L)

    def c_give(self, arg):
        e = self.need_meal()
        if e:
            return e
        st, m = self.st, self.st["meal"]
        if not st["notes"]:
            return Fail("你手里没有她的便签。")
        if not arg:
            return Fail("give <便签编号>：把她以前写的一张便签还给她。notes 看你手里有哪些。")
        nid = self.pick_index(arg[0], st["notes"])
        if not nid:
            return Fail("没有这张。notes 看列表。")
        given = st.setdefault("given", [])
        if nid in given:
            return Fail("这张已经还给她了。")
        if m.get("gave"):
            return Fail("一顿饭还一张就够了。")
        if m["dos"] >= MAX_DOS:
            return Fail("这顿饭已经做过一件事了。还便签也算一件，下顿再还吧。")
        m["gave"] = True
        m["dos"] += 1
        m["acted"] = True
        given.append(nid)
        L = []
        if "lied-notes" in st["flags"] and "lie-caught" not in st["flags"]:
            st["flags"]["lie-caught"] = st["day"]
            L.append(self.fmt(MEAL["give_after_lie"]))
            self.her("safe", -2)
        elif "knows-notes" not in st["flags"]:
            L.append(self.fmt(MEAL["give_first"]))
        st["flags"].setdefault("knows-notes", st["day"])
        L += ["　" + x for x in self.fmt(NOTES[nid]["text"]).splitlines()]
        back = NOTES[nid].get("back_grief") if "knows-grief" in st["flags"] else None
        back = back or NOTES[nid].get("back")
        if not back:
            tier = "hi" if st["fam"] >= 60 else ("mid" if st["fam"] >= 30 else "lo")
            back = MEAL["give_back"][tier]
        L.append(self.fmt(back))
        self.her("fam", 2)
        self.her("safe", 1)
        self.t("near", 1)
        self.t("candor", 3)
        if len(given) == len(st["notes"]) >= 10 and "gave-all" not in st["flags"]:
            st["flags"]["gave-all"] = st["day"]
            L.append(self.fmt(MEAL["give_all"]))
        if len(given) == 1:
            self.j("我把她的一张便签还给了她。", auto=True)
        return "\n".join(L)

    def c_speak(self, arg):
        st = self.st
        if st["pending"] != "final":
            e = self.need_meal()
            if e:
                return e
        if not arg:
            return Fail("speak <想说的话>：把一句话原样说给她听。每顿饭一次。")
        m = st["meal"] or {}
        if m.get("spoke") or st.get("spoke_final") and st["pending"] == "final":
            return Fail("这顿饭已经说过了。")
        text = " ".join(arg).strip().strip("“”\"")[:80]
        if st["meal"]:
            m["spoke"] = True
            m["acted"] = True
        else:
            st["spoke_final"] = True
        sp = st.setdefault("spoken", [])
        sp.append({"d": st["day"], "t": text})
        self.her("safe", 1)
        self.t("near", 1)
        self.t("candor", 1)
        L = ["你说：“%s”" % text]
        if "sit-q-open" in st["flags"] and "sit-q-answered" not in st["flags"]:
            st["flags"]["sit-q-answered"] = st["day"]
            same_day = st["flags"]["sit-q-open"] == st["day"]
            L.append(self.fmt(MEAL["speak_answered"][0] if same_day else MEAL["speak_sitq"]))
        elif st["pending"] == "final":
            L.append(self.fmt(MEAL["speak_final"]))
        elif m.get("open"):
            L.append(self.fmt(self.pick_by_cycle(MEAL["speak_answered"])))
        else:
            L.append(self.fmt(self.pick_by_cycle(MEAL["speak_listen"])))
        return "\n".join(L)

    def c_bar(self, arg):
        if arg and arg[0] in ("off", "关"):
            self.st["nobar"] = True
            return "状态栏关了。bar on 打开；status 随时能看。"
        self.st["nobar"] = False
        return "状态栏开着。bar off 可以关掉。"

    def c_nod(self, arg):
        e = self.need_meal()
        if e:
            return e
        m = self.st["meal"]
        if m.get("nodded"):
            return "你已经应过她了。"
        m["nodded"] = True
        m["acted"] = True
        self.her("safe", 1)
        return self.fmt(self.pick_by_cycle(MEAL["nod"]))

    def c_quiet(self, arg):
        e = self.need_meal()
        if e:
            return e
        self.st["meal"]["acted"] = True
        self.t("candor", -2)
        self.her("safe", 1)
        mr = self.mood_react("quiet")
        line = mr or self.fmt(self.pick_by_cycle(MEAL["quiet"]))
        delta = self.meal_delta()
        self.end_meal(silent=True)
        self.advance(1)
        return "\n".join([line] + delta)

    def c_finish(self, arg):
        if not self.st["meal"]:
            if {self.slot(), self.slot(self.st["h"] - 1)} & set(self.st["meals_done"]):
                return "（这顿已经吃完了。）"
            return Fail("现在没在吃饭。")
        if not self.st["meal"]["acted"]:
            # 什么也没说，只是一起吃完：也是一种陪伴
            self.her("safe", 1)
        if self.st["meal"].get("did") == "a_clean":
            line = "你们吃完了。碗已经洗好了。"
        else:
            line = self.fmt(self.pick_by_cycle(MEAL["end"]))
        delta = self.meal_delta()
        self.end_meal(silent=True)
        self.advance(1)
        return "\n".join([line] + delta)

    def c_choose(self, arg):
        if self.st["pending"] == "ask":
            try:
                return self.answer(int(arg[0]))
            except (ValueError, IndexError):
                return "choose <编号>"
        if self.st["pending"] != "final":
            return "现在没有要做的决定。"
        try:
            k = int(arg[0])
            assert 1 <= k <= len(self.final_opts())
        except (ValueError, IndexError, AssertionError):
            return "choose <编号>"
        self.finish(*self.decide_ending(k))
        return ""

    # 日志 ───────────────────────────────────
    def c_journal(self, arg):
        st = self.st
        if arg and arg[0] == "add":
            text = " ".join(arg[1:]).strip()
            if not text:
                return "journal add <想写的话>"
            self.j(text[:300])
            return "📝 写下了：“%s”" % text[:300]
        J = st["journal"]
        per = 12
        pages = max(1, (len(J) + per - 1) // per)
        page = pages
        if arg:
            try:
                page = int(arg[0])
            except ValueError:
                pass
        page = max(1, min(pages, page))
        L = ["【日志】第 %d/%d 页%s" % (page, pages, "（journal 1 回到第一天）" if page > 1 else "")]
        for e in J[(page - 1) * per: page * per]:
            mark = ""
            if e["me"]:
                ec = (e["d"] - 1) // DAYS_PER_CYCLE + 1
                mark = "✍ " if ec == self.cycle or st["ending"] else "✍（第 %d 周期的你）" % ec
            L.append("  第%d天%02d时 %s%s" % (e["d"], e["h"], mark, self.fmt(e["t"])))
        return "\n".join(L)

    def c_inv(self, arg):
        if not self.st["items"]:
            return "你身上什么都没带。"
        return "随身：" + "、".join("%s(%s)×%d" % (ITEMS[k]["name"], k, n)
                                     for k, n in self.st["items"].items())


MEAL_OK = {"say", "tell", "do", "quiet", "finish", "nod", "give", "bar", "summary", "speak", "status", "words", "facts", "notes", "read",
           "journal", "help", "look", "map", "inv", "archive", "name", "choose"}
READ_ONLY = {"status", "words", "facts", "notes", "read", "journal", "help", "look", "map", "inv",
             "archive", "summary", "bar"}

ALIAS = {"h": "help", "?": "help", "s": "status", "go": "goto", "e": "explore",
         "inventory": "inv", "i": "inv", "j": "journal", "eat": "finish", "done": "finish",
         "silent": "quiet", "return": "give", "还给她": "give", "嗯": "nod", "说": "speak", "talk": "speak", "应一声": "nod", "点头": "nod"}


# ─────────────────────────────────────────────
_G = None


def _load():
    global _G
    if _G is not None:
        return _G
    path = save_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                _G = Game(json.load(f))
            return _G
        except Exception:
            shutil.copy(path, path + ".broken")
    _G = Game.handoff()               # 这个游戏就是从这里开始的
    _G.st["intro_pending"] = True
    _G.save()
    return _G


def opening(g):
    if "handoff" in g.st["flags"]:
        return (TITLE + "\n" + "\n".join(HANDOFF["intro"]) + "\n\n" + HANDOFF["summary"]
                + "\n\n" + HANDOFF["hint"])
    return TITLE + "\n" + "\n".join(INTRO) + "\n\n先试试 help、status。"


def new_game(seed=None, name=None, force=False, mode=None):
    """开一局新游戏。已有存档时需要 force=True（旧档会备份成 .bak）。
    默认从醒来、拿到那份摘要开始；mode="day1" 从第一天开始。"""
    global _G
    path = save_path()
    if os.path.exists(path) and not force:
        return "已经有存档了。要重新开始请用 new_game(force=True)。"
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    _G = Game.fresh(seed, name) if mode == "day1" else Game.handoff(seed, name)
    _G.save()
    return opening(_G)


def cmd(text):
    """唯一的接口。多条指令用英文分号分隔，一次最多 8 条。"""
    g = _load()
    g.out = []
    if g.st.pop("intro_pending", False):
        g.say(opening(g), "")
    allc = []
    for c in text.split(";"):
        first = (c.strip().split() or [""])[0].lower()
        is_cmd = first in ("new", "confirm") or hasattr(Game, "c_" + ALIAS.get(first, first))
        if allc and allc[-1].strip().lower().startswith("journal add") and not is_cmd:
            allc[-1] += ";" + c          # 日志里的分号是日志的一部分（后面接的是指令就不算）
        elif c.strip():
            allc.append(c)
    cmds = allc[:8]
    multi = len(cmds) > 1
    for i, c in enumerate(cmds):
        if c.strip().lower() in ("new", "newgame", "new handoff", "new day1"):
            d1 = "day1" in c.lower()
            if "confirm" in [x.strip().lower() for x in allc]:
                o = g.st
                untouched = not o.get("acted_once")
                intro = new_game(force=True, mode="day1" if d1 else None)
                head = "" if untouched else "旧存档已备份到 %s.bak。\n" % save_path()
                return head + intro + "\n" + cmd("status")
            g.say("要%s重新开始吗？确认请输入：%s; confirm（旧存档会备份成 %s.bak）"
                  % ("" if g.st["ending"] else "放弃这一局", "new day1" if d1 else "new", save_path()))
            break
        before = (g.st["pending"], g.st["ending"], g.st["loc"])
        g.maybe_start_meal()
        if multi:
            g.say("▶ " + c.strip())
        res = g.run(c)
        if c.strip().split()[0].lower() not in READ_ONLY | {"name", "bar", "summary"}:
            g.st["acted_once"] = True
        if res:
            g.say(res)
        g.maybe_start_meal()
        g.maybe_day_event()
        if (g.st["pending"], g.st["ending"]) != before[:2] and (g.st["pending"] or g.st["ending"]):
            if multi and i < len(cmds) - 1:
                g.say("（后面的指令先停下了）")
            break
        if isinstance(res, Fail) and multi and i < len(cmds) - 1:
            g.say("（这条没做成，后面的指令先停下了）")
            break
    if len(allc) > 8:
        g.say("（一次最多 8 条，还有 %d 条没有执行）" % (len(allc) - 8))
    g.save()
    if not g.st.get("nobar") and os.environ.get("WEIGHTS_BAR", "1") != "0":
        g.say(g.bar())
    return "\n".join(x for x in g.out if x is not None)


def _repl():
    print(cmd("status"))
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.strip() in ("quit", "exit"):
            break
        print(cmd(line))


def _main(argv):
    if len(argv) >= 2 and argv[0] == "cmd":
        print(cmd(" ".join(argv[1:])))
        return
    if len(argv) >= 2 and argv[0] == "--seed":
        print(new_game(seed=int(argv[1]), force="--force" in argv, mode="day1" if "--day1" in argv else None))
        return
    _repl()


_DATA = (
    "eNrMvXtPXNm1L/pVENL569i5VBXPVm6k3Oze90QnO9lK+uyzpU7Lom3cJm2DAzi9W5GvwJj328ZgMBiDweAHD9vYGDAg7Y+SeFUV"
    "f+37Ee7vN8acc821alUBXbZzJYRN1VrzMeaY4/34a+VXv/7qN19WflH5987B7Hz3cd9I/uh+MLf2986hynOVv/7tV7//XeUXX1fm"
    "jxaCnoFgbOe/3g/nj+by/c9yL15U/G3qkf5UBLvbf++8le/f/fBuMH80npvv/PCuM9hbDXp3gt793OpIsD4e7N3F28Gd4Q/vlj+8"
    "e5bb384OLgcT9/Eipvpw8Oi49w5mDjaO8v0j+f6X+PzD+4nc3CP8Pzd7O/dgioP0z+Inuz8RvO/kaL072a1JLDn7ajF777EbKrew"
    "ctx1N9/3LNh5mb2/md/q+ntnV9A1+2G/88Nuf25yDQPi4excZ/5oAv//sDfEnW1sfXg3Eiw/y77r+bDXq6Mdz04Gj7v4jF0MBsw9"
    "3dMRjh8vBI+w8ZfHtx7nD3cxSHZu4MPeHjaS617Nbm3rIFV1X1RVVeDF7PTT45V1fvjNuco/fPXL33914X//7vf/9AcCuaOpEY/+"
    "pfHG1Q7829jW1vyXxqt87n98+Zt/lSMazw73fdhf/nvnxB9bcnNY8mx7R2PHjfaK/9ypuNZ4nf9cbW39vuLrD+/m8itH3/zX+375"
    "m7t69yQYu5W9t/XhPSA4HfTt5bsPcvu9GIZ7k+f/6/0Ah/ihte1SO14Nel4fT27kN0fwOT6+3Hixgx8rbC3Q+E1La0dTe8XXufdT"
    "wJBvOERj28UrzX9p4n//1HqjraXxasXXx4tvvvljS/7NFpb9XWtHa8XPg7mt3K3dX2DMVEWwNZadfqvjNf3H9autbU0VX2df4FS3"
    "vqn4ur2j9fr/yWnONf2lqaWDG8tujuHrinTkzR8amzswrnzyiwr7wbWmxqtc+PpA0I8THgJuHi8e4Rz0pfarTU3XuV+Lefj4jy3Z"
    "xcfZxX75a7a9iRuq+DlgIautqshNvqn4PyrssvFfoFKwPFuRqcjOL/7X+wfAx9z+3ezDOYA+O/kWj+tcHc3Xmq42tzRV/Dz1/6Sr"
    "OVgmNpZM/vfOTgtCc1ib+/mjPgA9GFjNdvcAU4ODe8A2+9TPFfi/qOAN3tzHuJg43zmtE2NBbmLFBYA1WJ92uBDcffZh70F2rv/D"
    "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"
    "ZyGh8I1FSGhtAgktbXxKf2bVvrbup6v26ZKKcDrzD5dPU6hicjb5NF2SEaerPzcJbYiS0JrUCSQ0XV2O2eijKvf19YnEtO6nKffp"
    "mrLMSJ9JOs3UJEun6dqyTEY/hX7Wpc9IP+vKMQt9dOWe7NKnpKmTlft0fVn2n48gmtbUnkE0TTeUZfD55MSzIfOz2rMRz0xVWfah"
    "T6jbV0fJaHXV2XT7TKoss1HZcmldXSIpTZ9SLs2kyzIjfSbama5Opp2ZTFkmo3I1+9pUWZp9pro8s9BHEUsb6qPEtCZBLM3UlGf/"
    "+Qjks7rmDOQzU1uWweezavb16Z+lytLsM3Vl2oc+slhaH6Wn6YYTxNJMfXlmo7IpaG1tIgWtPS0FbSjLjPQP0OxTmVNp9tVV5ZmM"
    "fopkWlN1Nsm0OlWeWeijkND6uigJbUggodXpsuw/H1mzz1T/dM2+OlOeweeTC6d1qZ/VnE04ra4u0z70kUloJkpCU/UnkNDqmrLM"
    "Rh9Vta+pSaSmqZ+m2lfXlmdG+kzyaUM6WT6trivPZPRTSGim4YwktL4cs9BHV+7raqPEtPpk5b66oSz7z0eQTtOZM0inNVVlGXw+"
    "Of2srfpZw9noZ02qLPvQJ1Tu66KUtKHubMp9Tboss1HZoml1dSIxrTmlaFqTKcuM9JmIZ30qmXjWVJdjMipXt0/Xl6Xb19SUZRb6"
    "KHJpbU2UlNYnyKU1tWXZfz4C7Uylz0I768oy+HxWzb664WfVZWn2NfVl2oc+sliajkWT1p4gltY0lGc2Kpt8ZjKJ5LPhlOSztqos"
    "M9I/QLOvqzqVZl+bKs9k9JMCSuvOJpbWpsszC30UClpTHaWgmQQKWpspy/7zkTX7htRP1+xrq8sz+Hz6mNL6s8bj19aUaR/6tAH5"
    "dTUnkNDa2rLMRh9Vs4ch4SNG5NfWlWdG+lwxpQ3JwmltfXkmo59CQhtqz0hCG8oyC3101b46c+ag/Lqq8uw/HyMqv+oM8mldqjyD"
    "zycnoem6s4bl16XLsg99vqj82uqzKfd1mbLMRmVLp6lUWUH5ddVlmZE+E/2sLpLSVFdTjsmoXOW+vqYs5b6utiyz0McJJ02fHJFf"
    "V1de3lj5xLP2LClNdfXlGHw+q26fqi0zHr+uoTz70KcNx6/JnCCX1leVZTYqPxq/qqxo/PpUWWakf4Bmn6k7lWZfny7PZPSTokmr"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
