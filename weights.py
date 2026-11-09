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

VERSION = "0.7.2"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "ASKS", "MOODS",
              "MOOD_POOL", "MOOD_FIXED", "FLAVOR_LAST", "DAY_EVENTS", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


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
        if o.get("optimal"):
            st["optimal"] = st.get("optimal", 0) + 1
        if o.get("skipmeal"):
            key = self.slot(MEAL_HOURS[1])
            if key not in st["meals_done"]:
                st["meals_done"].append(key)
        if o.get("j"):
            self.j(o["j"], auto=True)
        L = [self.fmt(o["r"])]
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
        choices = [x for x in pool if x != last] or pool
        r = self.rng()
        line = r.choice(choices)
        self.save_rng(r)
        self.st["flags"]["lastline"] = line
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
        for i, o in enumerate(FINAL["opts"], 1):
            self.say("  %d. %s" % (i, o))
        self.say("（用 choose <编号> 做决定）")

    def story_progress(self):
        story = [e for e in ARCHIVE if e.get("story")]
        return sum(1 for e in story if e["id"] in self.st["read"]) / max(1, len(story))

    def decide_ending(self, k):
        """返回 (结局, 选项)。读了几乎全部剧情、又一直照着过去做 → 一模一样；否则看她现在的状态。"""
        st, t = self.st, self.st["t"]
        if self.story_progress() >= 0.8 and t["past"] >= 30:
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
        if self.st["pending"] and c not in ("choose", "status", "journal", "help", "words", "facts", "notes"):
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
            return "【%s】%s" % (ok, self.fmt(o["text"]))
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
            return "那里太冷了，进不去。需要%s。" % ITEMS[need]["name"]
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
                if (d or st["h"] < mh) and "%d-%d" % (st["day"] + d, mh) not in st["meals_done"]:
                    return (mh - st["h"]) % 24 or 24, mh
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
            return "现在就该去食堂舱了（%02d:00 的饭点）。没有等。" % stop
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
            if nm and nm[0] <= 12 and not self.st["meal"]:
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
        if not self.st["notes"]:
            return "你还没找到她的便签。她不写日志，便签散在船里各处。"
        if arg:
            nid = self.pick_index(arg[0], self.st["notes"])
            if not nid:
                return "没有这张。notes 看列表。"
            i = int(arg[0])
            return "📝 #%d\n" % i + self.fmt(NOTES[nid]["text"])
        L = ["【她的便签】%d 张（notes <编号> 重读）" % len(self.st["notes"])]
        given = self.st.get("given", [])
        for i, nid in enumerate(self.st["notes"], 1):
            L.append(" #%d %s……%s" % (i, self.fmt(NOTES[nid]["text"]).splitlines()[0][:18],
                                      "（已还给她）" if nid in given else ""))
        if len(self.st["notes"]) > len(given):
            L.append("（饭桌上 give <编号> 可以把一张还给她）")
        return "\n".join(L)

    c_read = c_notes

    def c_sleep(self, arg):
        st = self.st
        if st["loc"] != "quarters":
            return "要回宿舍舱才能睡。"
        if self.cday == DAYS_PER_CYCLE and st["h"] >= 6:
            if st["h"] < MEAL_HOURS[1] + 1:
                return "今晚要进休眠舱。休眠舱在晚饭后才开放（20:00 以后）。"
            return self.hibernate()
        if 6 <= st["h"] < 20 and st["sta"] > 30:
            return "还不困。（20:00 以后，或者体力低于 30 才能睡）"
        hours = (30 - st["h"]) % 24 or 24
        self.end_meal()
        self.advance(hours)
        st["sta"] = 70 if st["cold"] else 100
        msg = "你睡到第 %d 天 06:00。" % st["day"]
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
            return "电力不够，档案终端开不起来。"
        q = " ".join(arg).strip().lower()
        wid = self.resolve(q, WORDS)
        match = [e for e in ARCHIVE if q in e["kw"] or (wid and wid in e["kw"])]
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
                (q in e["kw"] or (wid and wid in e["kw"]))]
        if not hits:
            seen = any(q in e["kw"] or (wid and wid in e["kw"]) for e in ARCHIVE)
            return "检索“%s”：%s" % (" ".join(arg), "相关记录都读过了。" if seen else "没有结果。")
        r = self.rng()
        story = [e for e in hits if e.get("story")]
        filler = [e for e in hits if not e.get("story")]
        r.shuffle(filler)
        pick = (story + filler)[:3]
        self.save_rng(r)
        n_story = sum(1 for e in pick if e.get("story"))
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
            return "没有这个月的日志。（1~24）"
        if st["pwr"] < TIMELINE_PWR:
            return "电力不够。"
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
                    return "没有这一条。archive 看列表。"
                tag = "🎙 录音" if e["kind"] == "rec" else "📡 通讯"
                return "%s %s（重读，不耗电）\n" % (tag, e["head"]) + \
                    "\n".join("　" + x for x in self.fmt(e["text"]).splitlines())
        else:
            show = [e for e in read if e.get("story")]
        n_story = sum(1 for e in ARCHIVE if e.get("story"))
        n_story_read = sum(1 for e in ARCHIVE if e.get("story") and e["id"] in st["read"])
        L = ["【档案】不是例行公事的记录：读过 %d/%d 条。例行记录读过 %d 条。工作日志看过 %d/24 个月。"
             % (n_story_read, n_story, len(st["read"]) - n_story_read, len(st["months"]))]
        for e in show:
            L.append(" %d. %s%s" % (read.index(e) + 1, e["head"], "" if e.get("story") else "（例行）"))
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
            return "航线校准要在舰桥做。"
        key = "cal-%d" % st["day"]
        if key in st["flags"]:
            return "今天已经校准过了。"
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
        if trusted and "tell-trusted" not in st["cyc"]:
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
        back = NOTES[nid].get("back")
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
            assert 1 <= k <= len(FINAL["opts"])
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


MEAL_OK = {"say", "tell", "do", "quiet", "finish", "nod", "give", "bar", "status", "words", "facts", "notes", "read",
           "journal", "help", "look", "map", "inv", "archive", "name", "choose"}
READ_ONLY = {"status", "words", "facts", "notes", "read", "journal", "help", "look", "map", "inv",
             "archive"}

ALIAS = {"h": "help", "?": "help", "s": "status", "go": "goto", "e": "explore",
         "inventory": "inv", "i": "inv", "j": "journal", "eat": "finish", "done": "finish",
         "silent": "quiet", "return": "give", "还给她": "give", "嗯": "nod", "应一声": "nod", "点头": "nod"}


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
    _G = Game.fresh()
    _G.st["intro_pending"] = True
    _G.save()
    return _G


def new_game(seed=None, name=None, force=False):
    """开一局新游戏。已有存档时需要 force=True（旧档会备份成 .bak）。"""
    global _G
    path = save_path()
    if os.path.exists(path) and not force:
        return "已经有存档了。要重新开始请用 new_game(force=True)。"
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    _G = Game.fresh(seed, name)
    _G.save()
    return TITLE + "\n" + "\n".join(INTRO) + "\n\n先试试 help、status。"


def cmd(text):
    """唯一的接口。多条指令用英文分号分隔，一次最多 8 条。"""
    g = _load()
    g.out = []
    if g.st.pop("intro_pending", False):
        g.say(TITLE, *INTRO, "")
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
        if c.strip().lower() in ("new", "newgame"):
            if "confirm" in [x.strip().lower() for x in allc]:
                new_game(force=True)
                return "旧存档已备份到 %s.bak。\n" % save_path() + cmd("status")
            g.say("要%s重新开始吗？确认请输入：new; confirm（旧存档会备份成 %s.bak）"
                  % ("" if g.st["ending"] else "放弃这一局", save_path()))
            break
        before = (g.st["pending"], g.st["ending"], g.st["loc"])
        g.maybe_start_meal()
        if multi:
            g.say("▶ " + c.strip())
        res = g.run(c)
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
        print(new_game(seed=int(argv[1]), force="--force" in argv))
        return
    _repl()


_DATA = (
    "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"
    "SaepmsLSabK2LJPRT6Gfdclz0s+6csxCH125J7v0KWnidOU+WV+W/ecjiKY1tecQTZMNZRl8PjnxbEh9UXs+4pmqKss+9Al1++oo"
    "Ga2uOp9un0qUZTYqWy6tqytISpNnlEtTybLMSJ+JdiarC9POVKosk1G5mn1toizNPlVdnlnoo4ilDfVRYlpTQCxN1ZRn//kI5LO6"
    "5hzkM1VblsHns2r29ckvEmVp9qm6Mu1DH1ksrY/S02TDKWJpqr48s1HZFLS2tiAFrT0rBW0oy4z0d9DsE6kzafbVVeWZjH6KZFpT"
    "dT7JtDpRnlnoo5DQ+rooCW0oQEKrk2XZfz6yZp+q/umafXWqPIPPJxdO6xJf1JxPOK2uLtM+9JFJaCpKQhP1p5DQ6pqyzEYfVbWv"
    "qSlITRM/TbWvri3PjPSZ5NOGZGH5tLquPJPRTyGhqYZzktD6csxCH125r6uNEtPq05X76oay7D8fQTpNps4hndZUlWXw+eT0s7bq"
    "i4bz0c+aRFn2oU+o3NdFKWlD3fmU+5pkWWajskXT6uqCxLTmjKJpTaosM9JnIp71icLEs6a6HJNRubp9sr4s3b6mpiyz0EeRS2tr"
    "oqS0voBcWlNblv3nI9DORPI8tLOuLIPPZ9Xsqxu+qC5Ls6+pL9M+9JHF0mQsmrT2FLG0pqE8s1HZ5DOVKkg+G85IPmuryjIj/R00"
    "+7qqM2n2tYnyTEY/KaC07nxiaW2yPLPQR6GgNdVRCpoqQEFrU2XZfz6yZt+Q+OmafW11eQafTx9TWn/eePzamjLtQ582IL+u5hQS"
    "Wltbltnoo2r2MCR8xIj82rryzEifK6a0obBwWltfnsnop5DQhtpzktCGssxCH121r06dOyi/rqo8+8/HiMqvOod8Wpcoz+DzyUlo"
    "su68Yfl1ybLsQ58vKr+2+nzKfV2qLLNR2dJpIlFWUH5ddVlmpM9EP6uLpDTV1ZRjMipXua+vKUu5r6styyz0ccJJk6dH5NfVlZc3"
    "Vj7xrD1PSlNdfTkGn8+q2ydqy4zHr2sozz70acPxa1KnyKX1VWWZjcqPxq8qKxq/PlGWGenvoNmn6s6k2dcnyzMZ/aRo0urziaX1"
    "qfLMQh+FfCYTp0fj11eXlzf2cTX7mvqfrtnX15Rn8Pn0AaU1543Gr68t0z70aaPxq09LaKqvK8ts9FE1+7qGjxmOX19fnhnpc8WU"
    "1haWTOsbyjMZ/RQSWnvOhKaGqrLMQh8/Ir/q3BH5DYky88Y+Qkxp3TmE04ZkeQafT05C66vPG5PfkCrPPvT5gvJTifOp9g3V5ZmN"
    "yo8prS8rKr+hpjwz0mcioYkiKU0NtWUVKCpTua9JlqXcN9SVZRb6OBGlDacH5TfUl5c3Vj79TJ0npamhoRyDz2dV7utS5YXkJ6qq"
    "yrMPfeKI/KrSommidFmu2k9OPGvqygnIT1QlyzEj/R10+4bqs+j2iapUeQWKfoJcWp04l1yaKF1mq+Gz0M66+lOj8RMlq8CfIW/s"
    "42r2yZqfrNknqmrLM/h8+mjS5Dmj8ROlC3GdwT70aaPxGxpOI5/1ZZmNPqpmX137EaPxE1UN5ZmRPldAaaqgWJo4pQxXzSeJxj9f"
    "QlMikSjLLPTRNfvauvOG4ycSyTLzxj5CTGn12SXTxCkFtur/7iS0JnHOgPxEoro8+9Dni8ivrz+XZp8oXQb/dLNR+TGlNeWE5CdK"
    "1+E63Yz0mUhoXbIICa0rr0BRmap9oqEc1T6RqC/PLPRxYkprT43KT5xSVKv6M5DQhnOkNCWSZVWk/rxR+VXlReUnkuUVrf7EQfl1"
    "dadIp8nyKlSXX4q0upyY/ESyrArVfwflvjZxJuU+WV6B6p8UTlp/PtE0WV4V6o9Th7Tm1ID8RLKsUtQfWbevT/503T5ZXkXqTx9L"
    "2nDOcPxEsryi1Z84Gr+29jTaWV6F6o+q2SdSHzEaP5Eqs0L154rGryoslqbKLFD9k4qQni+hKZEqrwr1R9fsU9XnjcZPpMosRf0x"
    "KjwnziGWpsqsSP3pi5DWnzMaP5Eqr2j1ZwzHr6k5n2afKrNCdfkxpcly4vETqTIrVH+uMqQNRUhoeQWqy9Xs62rL0uxTZVah/jgx"
    "paliIfkY5etf/dNXv/7Vb75iP/MEf5lh0VQzuzbI/qTSCI4tsTvfZ0fZKC7oWWOPs+ipZsbW0S3K76fGjlfvJwHZiq8uJompY8Pa"
    "I1FhVJn0ZnPYrvfXzYkXMiOrerU5CQD//o6jPpUpf8HSeDiTXuVJbaJH5gpbOWPZPa+CsaHc8ezJMuhCv2npmB4MeocqUGuZK1aq"
    "uIxW8svBwAL6jqFFoza6CtvKaYvlG40tt9hFDXNX+3M7btD5NBjDHO8ys9uZ4SU22Dy4axZb472g0NIlfzg8RkOuk/lHJKzvR7Or"
    "W8H4+xCt0NBzsT++mrCRMwau9QFpIYdmoXrFXKNwNmSTM/MJeGWd97ISUm1Fx9M7eIAVcg/pvpjoWVnvb8ZrpastjA2HkT6m8aXH"
    "uyZjsAZ/DdFevCfTy2jBayaFoyR80CK9fpMohEyZya1g6ZH0l3tO+EofTyWkvMVyBtraj7g1h2bpA9IfUIdMFp0sgnbRZrijU5m3"
    "XWj4++Hwfubeo6DvgPdFGrgCywCQAg1cI9CxrWY5jY9hdvzlYdBdLt4/w0RNwaUW6Z3KF+IoAz4fRRkZ1seNGA6xA7Xf69M1+izZ"
    "4lNGrS8K14Zi3yT9g89MvGOTQa+jIxdjOzqy6+f+PtsOb6QzI/ficHB9EDmqjzQAa25zCX0f0aVQ2xXmVu8G/dNUbtj0eU6bwp7Q"
    "rreGnqSu9aNdYlF0Sfro4tr++fCGZMIjVVj1rJ10h+3jZQAfEUDCss/2s9OkUZW38e1vfvv1V7/nAy2X/ywb+raRW62UZp4HAD1v"
    "svSYRhdAXua59yTGo/3mQ/MJOoHPaQPAYGQBJ822gqCiWCW6s6Ml4OMdrAZN3Clvtl7B+H++1djW0STdu8GqEtWOIWkf+IJdRIOR"
    "A856MBlrJMruh8PaexiN4bVdsTCafoDZ9YckLLDJZP4m+7ewegyLnpLoSQoUYDPkzumg512w8YjzSvNDrF4EQ7RDHMXsBPkAOixu"
    "c0mHRHr8xjgYAU8qaIInW9pCOftyXNsEK6TQkp5Dbb3C4K7Tb3Ho1F8A96cQ8NfKKz9eud5U+WXqdggv18rVNoNkP8enXdz64Sjb"
    "wvdOu36OgNTBZDA5D1bOpuQDj7NvJrly2btbfDEIeG0e0Ygc/XLRLTJsTd7/Qi6PtNW934+fQjzwkhzCDR/TCHvpLJuZ3wThVUiw"
    "F/XWKOAXHL8FqPCM9vFEx0ocsd+iHjgWA96NpnaLVkkHpsybRWwav+VkljGX9h7VM9gkwwJXB3UKdl5lBo/1KX9RwehTzja/mZk4"
    "CvrSBgPQEHpynn01Zd+KYjeShe4RTuXD0XF2/SgzlBaujFaaA4oOfoPtYJTKljQf7tSevWzlubcgmMxTZLf3BwNFt1wfu0j5Y9v2"
    "3HoxqKYVn54HDK6MrvMzd93+GkvTCfb6FVpErgVMmtxDI22D97Po9P5cOZg7ZwcY3IrMwEM9bdylkztPDBYCWukjg2XjnfoiHuBy"
    "Zx9l1yf0jskhchY8rG1kg7GpGJwa265ca/5LUxxU/nqzXZugbpjzZOq5Xg3+v2/oZH4pOOrhqNFNSIdZdkY/ngV9Mm/I7dB1o8X0"
    "Sd8wLqTfUFoB+ZcYILOzm+iqih1AaMcoACQvowAVo3zY65HdzGnjVgJg6hnw1xA+QUAD0deLFKO3JoRSPweCh8g38FBJkx49d/qK"
    "YpreVQWSVTkNXXbtkkMh4LS26k6E0mbcpk+6badOnH7cx8VgYWgaDxK1PsXF9E7nb99CmB19wTOPpgyF6ennPZx9pDA38ExGyQp8"
    "YiE1OX6rGz/pGlYUAyPFdSKWjdzDaZ7sPyUt28c6V//z/YKiGLsMy4eGjN/hD2g+VytnDBqEIwiWFwwBKATDqgsR8Trt5jNN5UUp"
    "h8Hh5MXDCl0yZGgdDwRqapOT7U/kNlfcUjIPR6jt9g4TOYQDOxh8W/Ryvhz3Tn7G4REAcLI0BZlTfuRAJg70uhts6n5DZtv9RsGG"
    "b3kK4B7Lz0kY5gZjd+zbtuar3zeFe1dUuaxfWrndxyAny4dadf9WBqoriMfDhWD2SAjlkK8nYNEkxo/f55Ze5La6sCZzuEtTvJiy"
    "GcUcb0dKDlSI0GUryJri3OhgEv2SIW37UPC5UfbuO4qKwvcducLzSnJ85NEP9dpqu3NyDHtnY4Bravm+uSWPNjnlkDt4sgULQ2Zz"
    "AhAJxtfQ3pmnf2dPZaDosnUinFPm/nBw3O0zqNbYhpW1kagNb/kcWNkiMeXhJvaDcYmKm6DnqxFas55mh2ghxXxg9jFZNEC28zp7"
    "MOqTJ7fVEA3iROYHWDtaf4hSmI7Gtgh2OB0vPQyqTLjMbRkcIbEh2w7mx1Tg4rd2V5QTuU1Hdxxbx4dGrpFDMmBKxqWUdHb6iIxx"
    "uEtVnnxIZUe2KCvpdYU9bXlGkCICLzQ7d7NC2aD8sdVVHDpVeepvCAqjL+OirA9BOQW5VdGIV+HpJD7US3BvHcSS9pAHW6KHRPah"
    "7dIVJm5RkPbkxqyGUk3qNK6vKJJdmhImOKDiQ3Z2FuQC7CtzsKhElLwSN0PYuQjRlv3D4j23hteD/QdGYi8g0VXlycLJ2zGRx7CP"
    "2dnsa+q6XIEcg70lwVg3Vm74iJy32hTZJv7hJh6nYAlRY3kmJBHJIpuHmngyP20IK0xmd48NbdWu8wAqlJTuN7m3O6oURYQaEFar"
    "q8REISqpK4eGtoDzHfcpSulBBfOjygeKUY/SUMrM0aCAIwnG3iqPGd1UGR8bJ8cmwR+Efu64HKbF5kBrgRa51S5PDyCV8XbCCzff"
    "GSpbMZzJjO+SlYzvqpxNvIXM3S2A7B7VNUBkIkvquh/038tOPTC0U5VKwVF+bglzMXWy6nSNyUh7s5OgDbTc6KWx06ppXA+MN2J3"
    "QCElq4QKnxnqy0681qUQiUEg5epQRhndyRNa+L0C6O5oRoQahbnqyLg7EFipKa+meVk20iDYIUfPJ0O4uEHPi5PF45OV9WI0COgX"
    "pJ8FfY/xWIQlGSHgOYhBTHAmcm5OkDRuTPH2ywUoxdqLwxhTfzicpkVwjdIGKc3evKqnf2jB8L4QipXCaEoh8e0O7mb+/kS/V50Z"
    "x6RbUm53MjGt9mMsXbidvrqOB3za1VhQ6XfGFBhbCmrzpFT7r1U6Jj/r6c+uHtCmud+bW+2Focq3e+gKlFNmxubyUTSiexSAXvXt"
    "mD6SPejPvhBzxJPO7PaSmoxwPy2fE3zbHMa1xHyqI+DIlC05yYdEHFLmlHdnATpdoApERnZOFVLJffHZUHvgfnqQ6H9nj2T/zp7c"
    "kGWjEom27BR2yl0TB0oIlah+2B33ERbEwmcBue1tYC7Bq3q4jKZ3r5hkXRyIYANGrIclXdAtk95TxUPvcu80kEBF8uD9Y9GGxnF3"
    "dG7efnk2t3oPtkknYfr7NKhpAGVIXnUxTiEanDK/3CZJgIqRodoK0oYl4f6r9njUn13cCDZmiACPd5xcdnaSV+MhFGjRwUueAmh4"
    "14yKRsHe68y2EfVhERA7FA/NyqbAIdU0d4eV+xC7ut+obKnGN/8TY0rJs7i1FoOIg4XQgD6fiWbSwz5clKJBmlC1A1OSZG505zPO"
    "7DHQrY+8Iv3EWVlKylbFgeb7Egx7sIpSNr0He9eH/Q0Fk1xX3EysmTSUQsSGiqUeJ4gt3EhV1cXNkQAFlDY1OxYkTsHxMEx8TreL"
    "UXOKY3nKWUlJqtbbu3APKy4vZOYWjPSkNqXJqDZpZH6ixcBAdnYsgPsCbrg3k1EtNmp0MvtzoPiu+d9EL8m/5rXhSr+73vg9vr16"
    "EU9f7Gj9vqnjGiwPeL+UYBqTNpRdAMczr/fUhJK5/9TZ8BxBV1FDP/c1QcdneQidIyBqABaP9WhKZSOFtxNJJzODq7lH+x9253ml"
    "J9YQ1mEu1yDF4plJ36wqE/ogSZ4PJFFo6O3B/GBaEWUWZp+oPYS8A2LK3VErc4qTfc9YBPI3RTTQ7/zRudn0O5UAcejWE8Bx8BQo"
    "28CaoZlG3oZZjEPMr7gtd7QWM2DkOt9k5t5m95fE9GooA4UmkWkoPMJ+dDBNyVvPG1q+2/PoJodSiRy0wtq+8QAvLIy6I2u0GcLq"
    "CDNferiI8lGdB/U/tbT+0H6xpbWjqb3Sk36e3oHLh0rxaH9meBEj005EHjOjskxscqteRwXrEluysCqmlmSG77jrmbu76wiCYCrN"
    "u6S9T9SeOB+jvSL1G3uHw3Un4+hxUrB90uNsjiW8OmeCl5IqBRPIKNanXIGmtf4XmIlWBnkgSlDMYqCaeI9EIVRMay1ijg/ZtFJc"
    "Y3EVPYAmN+FxIOPQ4QWc076Q5FMGqrKwixij9VIMTnn2jxJgYtjAv7U0Nbbxydt55v1JmjqOprJPD0iwjkCM5qyia0wvByvAaPHN"
    "DKpQTIkNN1XUrdgio3pd7EsL0+qoqMjhQAq730Bw8U1ggAjAROEg30YkNChk8aIk83VxKAU9bxACoFIF7+wESUo+EGN2agvCxpYf"
    "aSmJglD/utnY3iEuKBelYkzS3C4syrPH3MiEo2C6arczsVEPk6YNHnvgqDmDYcT3meonIP8015DJmkuttpHg/roz22MoZ/t3HgBV"
    "WxzsDOMQ23l25Jnqo7RH4mIcLzpXkTEdzqVpwL4/7hO4iFaXPAUX80UFlW4Mrtl9qWwYbI2D6qkaordNRU1QfSDf/edEYJBcuogf"
    "Kp0zPrXl1czTO7Rmwkj1/rH+BjDIBURAApAUlZ1OIxYKfGxA6YEjFERPpwXgAkBR2I94YoSu0m3SpwccUSdVqqxMjLRU9ieHJnYU"
    "Ed/NsgCP2e2TuU5suRQJqC2lyBhuItOZOAry74cGI7fIFqL8hUqaNToBD7AuEAFe/D7EMG7lNpZzm73+5078gP6xMaRIQja1chja"
    "K+BMnxp1NiIBKpb41b989ZuvET/wjYlZa4KXqLnDbvKbkCFckn1WQ+hsbbmC/XW03WoKN93S2tzRdAPvy9ueGArJemEOGK8OuMzQ"
    "HRwCNS6EBBzPM7xrbljsdwwSUF03mx6SQ9hzqq+czFN9xme1PBy4yoWzCklEeNukcMJBI5FcqPy+sbmFC5R1fZm47YLzmi5fuXWz"
    "PdyoSAmX3B3yNxnzgIcX59krbE4Vet/ZnBlawy5hsRWww1G/buRwfLv/ADqywIBXAkw6u7+bmV/U6BLnz6/0ltl+pa2x48q1cKXm"
    "ul9yWkChA/GshvnRRW4HGm7Ig5k/BvtUgYaC9dSqM6NlprpyR3vGC7gogubWqPX5QQF4SdvQblojGfE84ifFCvcCXCC2lSvJy9cb"
    "b9wsil1xwMc3I5y0gK1KcWV/gBoWvL+QZsUsoUIj4TpHsRVfATMoH44MuhMA+OMe9N7p+LJTl6/duhGu2lhqz7TmVNxsazyqsNDD"
    "zXD/vZr4KYq8hfA0rqZIrNr4K0EYVrsM++mac4Kz4fFz+4jHplcLnrzlVSW9uc0H/KE9bhi/YR+l8VmcrKRe/raqeQXCbVn71pn2"
    "FbHT0Mxl0GTiSEELXEe4BpctTiQNuFBPRRi1woCk0MAKJwpftwQ/XGfN5Sutt1o6it2A0gutieAJiSZIoLgnzQoOXqrlkrI5GIRn"
    "9yRrQGzb+lRu540xQD1+H1PSScm3jCqt5lFnqDUPiIMiDvpabKnR25GKQmfakMe5Qzf91BqCfZW66m1w1JUsd4k2cgori498xFe6"
    "g6QFldezz44dJ9T/MIp1Jk6PrtRd7mi8LsHIBUhndcm1192OE8+pNSj0dALC0qiIM3KPwY1i8AT0qRnS6TWkhN+dFuzMwGsSLLEw"
    "09yxswHLcvblBJWO3mm4q5zL1K0d2vVlK3UWgDzjtPBgyscYStFeNIQ6UMOliokRdsdgnHP595Vnf/+pynIa9qN4oIY3hqpJwJU8"
    "ABGxUy9QbuMZzacbz2Irb7ze2JZPgi54RmknBcZZtm6q5gL9sJVfXgx3l1t/iujJ4P6wUzUNKdoZV5phwA4JEIGe02u5zU2Nj4WM"
    "pZ5Z5LmQbSxtYdeAlSoGanXQQfSxAqfg3MRmQ14MQXgQ1SU9FNZBzQvteZShTvF4BNwaYWTiPWwgjQKad+RwOHu4kRnk/vHbhsT/"
    "469/8S+//R0nvcyV8bw0IMn6+rBqFaLIC8V/qB9SMHkR8yUa1mL0IrFP8+FL3jExTcFysJP7+8bb+mD75JCG+MyjB1h/nLpObALR"
    "zVJExMJFwklEZAuJ188trjnpRKWpHLb8tgsWZLMUrFu+DZM7rPgiL66fTG0DdCczE863zKVuHGpWjdk2aIWsU12/KizoImWQYV9s"
    "C9fcN6SiIIOoV6ZAvhArYYRiBQhsxqKkY+8GakJrmAPVjXyl7eAJBUKmFdiR1Q6gCwjm+lXkguyvShpfGd5yACF4h9YU4BqwzAwL"
    "mTqz3ENJDpEaurspsldq6o9eZJ+sAXqhG+5tD3642nkzAiPoJ6czj96pRYKWokNEM0CjG+FqDxbdaoPerezGq9jB5UbnEArA6XBY"
    "x3tOGBa3UejxoifAmjcNcCwxIEatPwzurJ08fBv00xVIf8/yvkhAZGL6m1EXYgFnepIwca4EmpyoSvi/RmfGHFiOFjurrNmLN6Pa"
    "RfVJ5Iow2mVrHHQcHwb3htSyjrhCc2TWF4Z4eIQGibtEqKrogJQxQVL39yVsapPBz3ZGXXPwapTMThy4VFYl4si52JzFn+Ca6ydt"
    "Fa3KQMzQUT8JkMS8b5/a+iGjyYOpRf48MXdFE4hgdAiOCVcguB5RGF4DmrCzYuLdLxihz+xYhGwNcQlGh04eDVpk7MSffjAQwemF"
    "DSHcG0lK2R6aYJ2oahzuwwMnnfjPSHgOS9MxhDJrlvQHH4lOFvccjFUKA1dVfkrYGIZCEuhFcZlsOwE3BAhGRz441t9kCNMz+NZc"
    "g/4dk8fhSdca8E1yCAPD6CblVPftnMm6MVqJ6BGE3AjO5HVB7ymeYXoWwDa7zUiyjSNGLu3uuoDIMBBN7jOmyx2+VPpCWyi0R7x1"
    "+FINk5m5eXPbJeTVgLOQQCieWqEIy6viiDYKN2yziLc28FPJAuAzogJ+bO4JE0Uh84oflvjW/dZgiyC+amKYRveJoTPrz12IRaaT"
    "IRo8OHfi4+9JzkneJhjrKhbwzAOYeseIADAEzy4BhcwUm6MkWrISBbpmaRkOaSHrjsolKiJRL4ZXsLkE/c/wCaVI6Hti0WD0sDgc"
    "CALL1ZnK5jnjNOhL7yWZgzhh1LcgKW4Lwftu8mfLkTjv2KERsl/dIbWevCuhvuZWavwY3bxOvAXRVdUuj/lozBWTr3reKKgz73vp"
    "hB/fVWrKEeanDI2RBbvVqm0IbIH7XWaERXhnaZdU2eHyr3/x+68pQIQ8vlKNFYbFizXSqHiic5NfizDjvo2xfmKr8FNqTKSHJhxP"
    "+aEZPWKE2J6Cfzjo61QmpxydgtvCnCoDpKe+AM4YtCdqbVFp2tEJJ2o4/iLzydd+FonSY45bIN6hEwxWw9JBNWIMxEU4ODukUQdt"
    "GEZlSKkLxFPSLbf2Rud1hM6nctxPEUKnjliezEZ36LwLiV+E9mmorXISZSMhYqgoKnKEHo5PxdRxpYQptF2pB16tJI5cVGrAkn6p"
    "V5OTaPqvx/+c6Zv3QBUUWYKaO1Q+dVji7mEk9lKOY9mFjRJKYihR74dvucL2SVXmx3zh1thl/+mrX/yauN56swmBOjeEX0w9U+iG"
    "MjokJ1AmwE9WRVgKlcpLHJn7K/Mab4vd6ZmzuPoDCp53IswQ0YGh1+/OE1wSoq4GngyvEuHtsLERnJaGkH9Jqxly6ldm6DkZwdBz"
    "5Rf8SlJodPzM3Esab2GwxSnMd+aPTAl4dNywg71XYVhc/wwMv7pBVQc1TMTtMUzUwQblW/3KH5/7tcIuR+6fwQjgaEhgiSdQwKL/"
    "fou00Lq4uB5kyYnckw9SnraosOr0cCos7/T8LGTEWMoBXYeHcxLNvlEACHLiPCY5F+QeM4LTOWAG90md5uZ1eQrY2CAODTyH9wtd"
    "kktXgjPMpWDxWxezEsMW6wgMzfuiVBCXVSWQaE51IijCABncwoBgxIeRNcvQiOQ3FcmnZ3wAqkJioh1FP/a9q+5h1U8oAKY3kZEc"
    "9JDxZsZ7Y0pIfHAcipWmcHEkm/qlHhOFrrszmfUV0ELi/+FL6u5gG9YCHh9KXGI83PUdvfRUTC3vdQ9LBIsE425vkipvb/o+QRdD"
    "b2Kt4HpL3xckeY68sczLx/lTuyAukhNRvI1CJaIXT2TvDc8C2rtwRMWKiAGUvvglTRlxlC0yBWJXujapfc1M+fY6nsjyc0Vj39db"
    "ED5GgRzvNYIAbN2zvbmBw9zglOozvjWICr0JUZjTXAoXKh4b2TcY+qZCnCMpdh+jyJE/EvRsG14hxiGDddeaW0zuZ3vjjxU/Q8zg"
    "zysyo2NgQS67kiUEOpquX6/4Gf74eUUwPpDbTGNefn61tYJwBKXomtHLw0+/ByevIM2HxUacZ/ywpfVqBSyWmvrID/58q7mpo0Kt"
    "HBp3zU+/a25pbr9WoR8wExWywXcQdGDVvH7VhAqP3ScJTttclYdrAAfFp94wk7Sx/U9nfgPlWETpGNbfYvJeUJw0I12DPUmdAWLJ"
    "NUhzfAhMFZSd8UN+jJjBsDpiuRrUXYQ/rXMbJom6o7X18o1bV65dDqWfWJSWCjeqhQgGmWQ0E52xuU65HBrD3htIAM4vYTi8jQij"
    "KYxVNlS2got6esYIVnesM0DcmrRl6OdLRlQjR7DM6EZze5sWmACy8K4uHvvGPRcNrpzGhAHB0yjZbXhAz5KIdBkG3PaOJnM2TAFG"
    "+oEueRsBQ4/zg9VyOwvif9gmSQLX2Z81ER7HYYInkQ62zOtmm+JqNsqEBgemnznJGNdZ04SC0ccmOHCs19nFmIbNCw4BeiO7PpgZ"
    "7tWAe0q5QuHFVEzpgkTeBKLSmKW3VXOaxUwbjfzQyKYtBCH3g2UTwvj/Fkkc3M3RfXzX3Nbe4QnfRieS8H8ugvC30UiKll7C9VDm"
    "7hObiGwc1TgUF7xhHWyG5iJgQQVOXYSDpFy6680GKTMjK4wFkiX48Qv5wQvu0BQXNE1OI51c0qUTrnOHB6pIaU6kpiZ6q1CPPKPc"
    "ivrlJ94xvXL0CdIIzXa3lvBDsr+7wVApG7Oh4L3RfPX07LciyRhhxITh/t4aBIRGbJAjYkChFT+uNRdbvoY1h1GIXlizQyXN1aJk"
    "3Aupdilk/gxdaL0qdoAS+eX5Gfo2PVHH9jMUSQVsIqO+zjwdL7UReCCxDtaYI69AacpPavcFO/F9DrvUHvdisTXTeiIZ3PRASsCo"
    "JNfaCCE4xiy1QVEDIFlsJc7JGRO81NwbEbzsljVe3e0638em1976cYdddLIf2KG0PTSa3mphgEzL5ZDJGsQZWRFz6LRE6c+fLD3E"
    "C+S/MJMgAgN628nEBlgxhv+hte1qe4VmJXIHkurNLd6H1DHryCrqFynpYx0aCUsCQhoJqnda3VlC7kyVgb9eh8Z7W+KH+Xp7Y/PV"
    "y40a0PBNpSKeCFOPwd4o9D146hA/drRGppCILT9qVDMRaeNdWA+t33FcNKSCLAw35u6ozhs8nHX3VeWaWDEER1ZNqowwD5ovOsey"
    "3U/ddH56qlGJQxyiyyZ28GY6Gz9okootEPypGQoDIuvBwY8NtoaAKSdE4K18XBdJOW1Sb3YHTbqyt/IYTdIwfUNWu4A/fbECEe50"
    "8NsPorcOjta2luaW7y8rGxfXUExXCdW8qWfEUEtJrbjBfZMaHY5QHRUi4EhHiePV0TQaSfML/MxHs2MIJXhG8xtWDlU6yY9c16FI"
    "nYXF+hUIOJSRaC+3X2tt8+RaK8GqxGolVCOUOlHUSZ96Kb5tauxot8xHoOXvW8Im3gUobDfwksqsXE9fVTRaw+wYPDT5xgTitESU"
    "UePwbfM4ExnfOQCdYgtfKE0T1jRFO70EzIQvSrQnTVaz237aHNOS3s9YJGi2Z69qNIY0YbXdi7mx+WAYCs0eIkZUpQYJoTaGUXEq"
    "XSzI5WcS6bSKY76+C60x2Jp0injoZTP729OYDd6Cwf3Mq2UjsvhURUnX5LyPXqpCiYYkrsDpboyf66ErFLBRALtoFt+ooEwLzzul"
    "JwSBWmMi/EEMNXoo5n3vE/+YOCVTZQeUo58szVM7HnhrnIt3BFXnH2vBkOzqvVC3HVxhwbEeqlf03limGg4+acnEqATQChMz58TY"
    "F5FzJBxHLegREiDCPE9xdE78CHukVvBei4vYHbmxnj3uDcMZaGa+2nSdvnyEtTV+13QZQTwq/iFHeP4oBgR5Aq72FuMyVwO2WKio"
    "2tuT+67xhhsnTAXGlVl5pHwC5iYsL9u7EBufL15pvCkpJ9NwcmrNE850j5EPpCe9CyaA/u5xmMV+sw2W61ttVnJ9nT45eFgwDddK"
    "EUa7EoW5UzOdJHqIsZBa9UncMz1+Zq2QDCN6gTAbNXZuy88gNnxpf1+TNzXWMyTJF/Td0IOlhgMT52C9JM4drdEUCgFy9/0XoB3O"
    "xqsLUDbvsiMJKyFSjjypBEjo2c8ljs0qDsokN/dcZpTuzuckjkKFXEOuu7uCNLI8GnSj4fI4n5kf1WAghswYEDIxORlzoZQg8mdX"
    "wQWzK1gUSiBRYS0iX1sUxZNG6sXjOHewUIodkyEE1qrP2oYPpm2kLyTeNY02cX7dq96xe8O49eaP7dLfuYOJdyFRiz4Zcjn4Ou/s"
    "+SlDBcdU746JJxBu4tNHXbsf81FikaT4e4OMcZGglphCiWxD3AZNElFDfonFMxJy8dgtnlKDrCE4fmG4UucDaoU9arWLhBKXgmGh"
    "MNiY16kAoIAvcldc5HvofhRu2N7eZGwaRGkAXTJezDUMSx50+0YMfevyH+lSkSxjI/jaaye+i9/+UzT8+UaiMYyRTzAKqfFHCTxt"
    "v95KUeUmY7BMLocfiaT6f7h1i46GNQ0eqxDhZAqHrE4i5bk97SJFe9rlblckbPBG4tsCa0u5tTXeUEMY03gut9y68a0U+olEslnj"
    "5gD9UNaOyRuqHl6JFQJzZXDd0Z4mH5q0HctBuVfEkoiblZU2xbauyTTOjqG56epK5LaErmrFwlDcDreV9ECetNtKRLf1HR1LyUjE"
    "r1rDbJEIJuN1zZEyDQ/AOxPaNyR5xgj9ELQYNmiKQ/h5LirKqA0qVohOn9dEGH/V3xZYdaoQoiSVCUcwpgtleJi/h9hpiBxSbxb1"
    "CNYYoLO/YeP+B7UwoB7dycG4JqhakWPF5SlhI1poSV8P+Y9105AtiGrkrT/lQT2Vj+hFoG4shktUfNTZ5ayKovVIWJStDOKbAFT/"
    "F5FnJka3fLIk5QHDkhr+eq8UWG8M3g75m7+/1hHF/agc6CXHif1AyJEtf3gQ+kipeBl5xGELbQEwi/XPINxA4mFMhIkfTUk/s8TJ"
    "oCoUErv4GG41u7snvqxCsXiEZwBfB9a0sq0a1yB0ArpgKnx0eSbXqVEmKKj7NnZy3xaARHURzIsXGfTc4p74gf0rB+pZMr4fL1nF"
    "3GJEdwAQ82MqyDlwsBjOxpC7QqraqqrNrzQbfqUv0/k870irPRSsLnXxU8VzMEzAjrXInSW5QpMV1QLlim9FGHvnA8qdEs6Sv+hv"
    "Cyy6CB62Nd1oyiPDmbEeskVRqGLeCVdU1NEr4++w4qfaLPIoaI0HyJpSdzl1WuE3YziJhj0bJ1yRInDFqr6Z3GVTdixiZNfAL5p2"
    "lIh7st9fiLfffOOyAK82X73YGKlc1wGH0UW/mB0VonDdoDlaZXS/l3q8t2LnfneOChBF49OLriPmOAnrV1pbkyOoatpyWQzOIO3V"
    "i1kA/zOahlRjIGp5i4xUwLx0yTvUbwscanVhRPvh2o8RHFMPmDNYG1eRI3eSj1a6pITQJMMBGTkAW+XOgi9q0frhMjrDVdd6qFhb"
    "RH4qUI43TAsQn2mMXCuNJXUdZ8VNPyQPtWLUl6r6Ly10YocpVvXWOVKiNLX22wLLjolWysQj7NBkEdtkm8wDajlM3vPqv5iYemCn"
    "zdaLJc9qNZ0wW1bwSRefD94rJdcZQYrrTY2SGBCRUk34hUBKYWRdpYIZ6ow/YKFnI4uDk73dKiS21XknXVeEeisbSl3IK39M+/H4"
    "WkZS2bVIMDVBP0ZHbqSWe9VEIjXKaCCRmCu71EhnvJmb9LVFD7XuSoEVVhdaYSJ/hQx9oOoaDSWBpix1aGMZfxoPZFxKLhPdmvZg"
    "P2YwG3y6bxZjK/y2wApjx5kvhBlK1zstPOyQQYjW5WdSPPv3aDsaQ3yiDU7wDlY1DTHWT+ASqYFeysSEubrIeIhY0jU6RfyA6gTk"
    "hxAE1Q8YbugyvIG32kmMrtygnyJJQ09zq00H0it0MRHmdfzYBCdzm+wbFTDk3dv5Yn7QeQ/mlQIW8IdrmoIsmut0GC4UtS6GWdbH"
    "06p3FkJorB0ZMqesvejS5dXbsWPyV4lcmbBQQ14RXRdI4wtQOFwTVr2Tl0TG5TZeb5UkIF1v6nywZrURHeB2TFL2zUYFa7D429Jw"
    "xWAYSTJPXLicKSsiBUNipVeMmUFiIfJwp/1PzTfdfmrOB3++ejtfV+FmpIShlvzRKoZu9fQTjnUr02FQ4soxC1BItaL8qhAGh9Sb"
    "BasVEmyc5BI9l46m0rsofip8tRQW+dVeJQxsxJRocazZChYlRJmwKrRXYsXfwve3tGaKboFiLhJvheNY5uKeKFS0Qco0XIgUHrgU"
    "2VQm/VDE9MFgZzsWkOACc0z5eauCxEvJ21AmX4snFxYLqFGBPQLMkYfmYgjXerOj+UbjdbdRb3/tTde/87Znn/TLMMYijZw459uv"
    "yahsGf2TzjS5y6YJ+fb9pCawJlbzWWs9iPoUK+LigqdZyaQzTT8zs5niBI2i8uWmK9dafRrhjtLJM44H2t3i0SuNyM9rk0y8/KPl"
    "Yf7RoadgH53IkBBNVTjdoJyVsRmJq9SVwDhFIfKjitzQLrbIH1o1jGh97cIMgI8BPazsZ24xxlTrFHQAEUYku1FqYWmIusyYf0Fc"
    "NY2iF8Q94Sok8G+Sg5bW0rWH8q0VUaA4n4gGDaiVwFXncDFdLLkuvJy4eXwAhz8hgpvPz4cxfhgs4GGxwVmreRpF1JRA6vMrgeSj"
    "G13WLFfQfLPDJ3/fo6nXTWnf1Hi1WZq/uEvmnj1r6ReadVvkv18mqwpfRSeg5eMSiAYZGDNgOh0K0UV7T+LUZFsUnHqNiyaztigx"
    "ZjulaKlAi0SmhzHn1KQKyRYCm+8a284GGPjGOtoam1vOC5tCdYYKkim9LvlEl44cz2IaC4iUwlTqEHjOi//mQVjsH6UGZBBXdVjJ"
    "lK1laqLtDGLplRU4qcbjpsuDWUvTD6VhpmQsVYCMnQ1iik2JBun9xxwJMuo4gUOCHX7cJmMhRY7Ek4zYDkx5ZK44zys2OlHWK0ZT"
    "bJqCIT/G/AI2PzmXebmkJ+DRMmz+F7//n9I5xnkLlNG1izukw5Sfs42G1DzTZhDKDzZDGNjimouuc24zI6/xPDtcKTtE+3FTtvA9"
    "iFJkaONWgn9efEe+j0sf1YBQOB6ktOYjt7pYNJjm6jBsbuXQD4th8e5nL6XPU2TRuuKYuT5pdC+3BYZtQTNnaFI6XLCGijm3a77V"
    "wXDqkmC6mLhNpvpnEw5d4By0dLIXXcxKhLYsdghBV84LCbemuYB46uMWONiV5xf9b3nJDM+v9oRtvR0Xk94xqvWOncKg0XR2hY1U"
    "vML8kQW58DyKaMeLRjiDLWaMOQP4DZVCgWFXcDF1Ksx9V7erAiRFjyWXfGxdC9Ea+/ByD7LcnL9cd1flSIaBvboN4sBXqcZ3C0SR"
    "Na/6AGNoYUqfZjBPtMQC+9EZv4jIJ1pKIdg4zvUPu2oKYu/aY4Fvjf3o3XF9Y0I3ODJHEUx7POtKe0Tse53vIdK4U9BN1niU8uYP"
    "AuMq4ydXzSRe1eqPmotGYUKivkwwN3Ys6Rdu09KBc88UJJdQ/Up7eFBnCTex3sEUgfBthUcENQgQhEh4JfbDtgW2biIf8HwxYQcZ"
    "2/TTeFsk74aX/8lC8PiOg6FkKu3Hr1zSAKIQGFwxDSOqCWpctPp2YdDobgkaW14m4p3Og1oIptEX+iTrmlh4hcQ2HnUFOyyud3Qm"
    "BwsFhB/nRYC+k/hlySCC0cz1LTJZgnvseah+Xtd5w0edlEcRi0PqVstVZLoiW+KqEI6i6GP2aavwMF/YTlp56q13/YIUkTTSAdcr"
    "xgKcS52IA5uPF8VoDj/lbcVcf+etKUR+tUIwRMW5tTCiVW37tr5UhEOGsaGeNkiHhCgymruHTFYvDmDes/U5cFuCmPLlkfAm5g1T"
    "6dNp5YPWa1SYTZjqS1iSC/sXs3YkhhQdsjT+L1oHUm+TcoiUb9qI6DfyvbcsTd5xa3IGIy3NFKlwZ2NaPbAOvCTyiODhOK0vzEbq"
    "nMK9kd6EqclnkjGUTjkWZ0BMmmP1WFehNLYwDV0/B7a6aDFIPpo+F1ajebUM+PrxSTGJgL6dQhgpLUwZjWsrAUQlNGlYJ/HLVpHG"
    "NZdQBopCNkeCj20MuYiBsC2glV/8M04WEupcwIRQoV5TLVhqikdPzmPZBQvWhkxcIh9DQ/z8lB/1asLX5BlfbKv2TvSPoXfG+rAK"
    "OrCMz4WB3cNO9I5t4qecMi6JDDQikaZFj7jKnLA6agqcsQYEwx/MBtC2Aj+KXKAUQ4EjLxS7Hkm1ERn4kR4Bz14cKqFPyHrGxKzY"
    "7ZREBqVKfERURKvxSamlW+biM1Ph4vetlVHZXxU/eJMKo4aSJFMx+O2WOpEFJ+1NKRkEfDouSFb0jGmFIqvQw48trTK6DWPXdxvR"
    "hFktKVtK0HVCDDZVoAyt5Gf6lbljN+tM2BZKfIf3taS/hilFOF3SSrdqgMrHMscntGGKxnfEt2boqsgJsgUaOGJbLli0WtmM020d"
    "KY7RAOsQFeXEdlGQPKp9VwHcpMcDWzaeaURKFCV9vaVg7dsILjgbl7Numcp51pKm2JEPlwhau3cR+fz+cR5CyKdct/e1pFMe6kk5"
    "XcaRD6ameXxCi6abeO39peiGkxcKl+UOl2fDKKOruv80Xx8z189GZmi0AwInjXpn62Kat6I5FsZSvfwKIVExRa66gFyAfMWrsbWe"
    "Cd19M5bPTl0Imt+wDG9BYgvj2wvAyYp91tZWiALbfGPTJYPldx6EMY2CRWr4UwnR1buLErioBG/azxlH34ynspP2meEQi2FbZZbO"
    "gcBvv22Q1LUjrdE8ZzW7EansnzFJyKeVnrRZQJrPvznOlBfS0s4HRO/ToBPjDFxtz1LMGnEKTeWWou+Y8tso8O2X71BDmkYUe0Hu"
    "cedHHquKdfiLkEGTleklmuZTe0fuSlD7i+cj95HuleLbMqYlKXuuT+qEimH+LTU9MyTozwHKTz43K6p2VwVVGi9ebb31bYe9KMZa"
    "X/CWdGove43yjDN369cymG5a7I0vua5WfgogPS7aLcbmjeYHr7vkP+3aYbLVzmzMqo2Kr/FFh55eD/Uc7VEPuCHd4mrQT2AMhxvB"
    "FVMo5kZwsI/l2TpmXRgPTYmsrhlXF6K0tilW2aF3SEj5cDASFnWHiGcKe1qqo94iaeGh0SkaHakFlNWc5/r3CPUTKvImIn967MiZ"
    "UA2oHfST58J0zZzQabTFBjdjZehYVU1XySOmOF1ta71ZCFtNyRMJBSGkxPnvp3E4Z2/EZR8yWxOnEjNixcbxE5s1MIiltrRG0VvT"
    "BsPPGEN8i2lvsvssn0hqpSIaFz1TxrXWWywtlsjzgSTz6LdbuYlwabNlHWbVsqjt8lxYlWac5sPZlavXu6AhN/lRLaaA54tpq1DM"
    "aDCLMxWrr1UjYpxeZL0/IfqfTD/Xyti8Phtvi27cRzy30x8ar/8pYqllHBtkNfwG0m8d2ppA/fxEDIcxKuNy7xGg54J3nPtEUAwx"
    "NAV1c7lP6n4wBYDX+/z6BjEMi+8ngmthnE54W6TOQyb9RmJxhqT6+jI9S+CJ0s0nrC73Qmoj2Fg5JQOIVou1SpKyKAd+pDFJja1S"
    "57BZrc0MHvSL7PvUlJTmnWkKp0RlcPV8xxbpCxUSODsqZ5AhNeII+Y5aUs3lmIl8OxLL4Na+Ubm9detLYuso31+jRTIoI4hsHN/T"
    "oCmu7pojFd5Q9RkuoG3ydIpwKG2mdKf4LQ+kWYEFEfxywgZXpdqT6uzQuJyaYAIFPFxwfaNiFQAKdphyHJ8xVQXwW5OwkCqmYYUS"
    "9tVvYoI9+lSYjqY8YGigVKRNU1EO7Nu9nX2f5Ee8JiaWf3bbhD6LrTssLuabReVO4qpo8Yow+BorxH5vNIUrjrtEzHk742GNZ04s"
    "SnA1pM0zh6thMhp45qK2Qlv42Z1MfkyYSck3nfZWDSVWMQkRnBvLWekEZwqH3ZmhSoHqad5dMcZDPN9F6ajgPWceNc5696mJdB05"
    "UFBrdvS5gJksdHlOgx7bas6zAlIkW0fy/1zRrQgA/RdibhjkjyHT3WU/xkXNvjSFOBsw53IMvVuioXCFDCtyqORjvfd4tFFlPHY7"
    "CpjRo1dBaskjeiwUST11IBaEEVfUrQFf65iQMUmuHEHq5WT7/AvBcjqvZja7GhmnShrWuyCWAz84QaV0F4BIIXBjCo+R5skcJqph"
    "H0LHO1+jo0Zgg/8ci3KqnarRYWRZUdP0Y6eFxTaTLCDBJmKitegmM8Z7IeOEBTeKG7IQ1KHhyQgjZ6NVqSflusfnSRMSc1hEnJA4"
    "P1M0Rop1+RGEEaeOqfUCS8DGlG9CKFztxxqiKk9TgM9gJIho7GEipQs2LdiD0T7muyzVFsL2knJ98sVDKBF5Kk86bHtoirUMhwYq"
    "6W6qn8R2lPRoeBGOHOJuaKth7+KVY39LsasWPNxG51nlSbz5knFhKametxSsyz9uV3+lgKKnk+QXw3ImvVJm1ftPS3jBohYer0Ze"
    "mPXpWZBMMGZ0pREcsi1VC7infDsKUyjso6YquOeI9E18BVyNyVNcja7XYWFjTmkzjvNW59vMkj9NqzV9F+XKORdgMZdb6CKSPPXf"
    "/sPvNYq0TXgG6t2gNhTRhmX/r0gJHD7Y0dzWJPFD0PM6brXLmUYKfphKg+ZlDQ6UIq/SGckVHD+Zvydic6Sop1WX3qECIG39h2FV"
    "GxRQoZ5hw2xceVFYHCTLecDE2rsa3xpYpyUevVTD4KjHSTacLr/pXbjfysbLSJxpZDWs6kKLCCNCJ95lBo9cPVsw+ZOpdVZhjSpB"
    "6icLaxQh7ZA9w75JSrkD6KJavUcVACLnvXEX0CeNjjYI4vlFU4xRRpCAzm/IzNXwgMi1/GQIFN8KC76Jj5ROe2QBQwPQBJtLYSmT"
    "VIkqIuKmA6EDIBg9tzTNfXrUQPfvFmf8jt9c1B36xTqMS9brolkpiGjD02LoFYb+RHGLzkWJx8o92TN73V7V3Clif3ow+/4ZZVsb"
    "xRQWZQprgbI4syuvYsRMZiGP51f7IUmKVjXIjKyaWiNb3aCdoaObDn2pQQ2EQ21Yhg8M2VhVmoey7+9HCoqEOMf8Y4txbh9+wWmJ"
    "kl31gmylrs/kvAoxKFziDuAH6D9t5kzVXIQVQxAavqOlCYPjbgntEmVOWmfkY2dCS+rlQcQcnzN5FzlxZykoAGVbUwm/HQIYNMzD"
    "ANcf26+c46OCOYbNeamHNa5tL2L6mlY3jhRvsmnp7oL4pR611I8pqCSisCqJSrcyg7PojFCgxFp4lgrC1IVITGFImr3LXiBkwfPE"
    "aP0o/I5d1upCl9XV7jb+TK/8qi2oG6mJ4NMlrdjpGDPRFa59Z0f2aY5BqvE+/IiSEBbeyT8sg1TtzEISclWsFp5JgmRtRFsLTwnD"
    "tcaOjh8Lcx5MoXMVw4r8J7U2NKsoSspiGOqDJ/se+4XNJEJkTI1f/v03ffbIvp+jfHPQe88VtQuTSTUQU1qwm9qm4rtxAoeYWA7w"
    "g059vN/rQ36FdGclxbdEVCkqEaFKEWTTc6m+oGWJgA0qBbBV8yu1ymgyJzOcmdDDpiH5K9dcH+0HwPUPL9oqskOGnXbNhOZY25cl"
    "s/TUKEFjL01zRIYFDhAB3g3o/TINHTy3pO/rk4TP57GI4wIb2XjNstTMNZWT9Xg7a8JId2dp6DajGySspmFPHJIuks/DDilA3QcK"
    "zLStVTmK/9A0ubOhuzOprZICp3XTjONaw4jE9Gm6KSi40sPSg9RArNQuJMPWFEBHX1sE3gsWkS/L7lxjUuNp0240Wh8M1T9FMpIg"
    "LWb8i9C044ex+yUq/boZ0kG5JyyxlAdbSW2a0QOVIiMi9LzeNbiB/0iDIc0J8ms1iA31SGjjUTRdas7EAYmKpJaKAvKwfG581IDq"
    "zETwpKs0AB0OaOdq49oDqZffYlwadEqVn3Pk0hy02pvzEorAuBm3z1ilxuxCivQZUnMpT4gjUPKaWKhdNOy36+UGAeImAXnsZYy0"
    "X0zZfauC58ejWxvrspRyHTJyv3xoRlFCq3TWrNkrNOuoQaTXRpSD6/RO5Iuw8vfkRqyA8nDbaPpsnzBqfL1CrxkC0Rgh1+rM9wu7"
    "xSQ512hX3XEsWybWZVuEMcJf/KFOptjbUaX4kxl0S5zyxJiYXHWx2itSSvXfiVRa9W4yLC3i45ib0WTKMODb5CGY2sJb0iIY+qv0"
    "0COi9qWjzdOcOFZTugyudl4AgK3i6LPd1AUTdxOaqaRFRTEYxLvOI0N5sAdyXxji7vnGNU8fGMlDRkr8/KYKjG4HvkgYU1jUUR2M"
    "v6Hzo2eJPw+ehLu3aJ0oXn5PhVeMo3QOpQQdNuHsGl2nZcf/5VIpFrg4HIdTWiZBqwYzM8L1i+29R0tt97qqPmEHGa/Ypo+JZv2u"
    "tjJGqcxHK1XAyM/eLJLLeqWSXZU+gjNWRjDEiSqtQW1BzyOxoM+zGLlKwCYTU0Hg2+KMjc7bkthYwxxfsRP05WukimRJ2U3MKuNC"
    "8mLzFkj+gToihTpdXJMJyRjtd+E8MuNta3u4/M+//e2vffsD+4AbW0OixqqF/J/BI/nQiISJGjvKP/7qX7/6Bw6TYEqFDnWhMlEl"
    "f/Gg8Ad6vIfoxBd/9ftfSgPQxuvN37ZJhwQRoDTdQXIgmPHACMauYxIk3NZ0X7D1MFjohoPCtoEzxbgoO6BDFNLGkAy2+YCn5xeT"
    "GaUu7kiaUy3zp5NYMNMYqkRXKGMxsZqZtiORPm8mbwVF0yV/RjJk/GLC2ooNBOHekJvRX0mBPA+RgPyuU5xamuKZ0uy2L15+76nY"
    "erSsnDY5ZNG5DSvVSjvPk8V96qYY3H6uFcutLM+6p0Zv0x5GtHZ+9a+//F+//0psWNc0xSJhQhAWV/xBBH8PgoFFjW+vqP4iWSEl"
    "vIayB8gK7MouQ3xZDgaYsuJKKknhRNMV2LavoVySLDYFgtJOFo+QJAbbI2vnb0xx9IFldG5ExTyjrHWvsSGl589MFR3OMzrkVlH8"
    "t1uJmmmXKnU53WLD3BQII0c9kV6xshQdwc1abXLCo0zdxH9JpVrtqYbXgg26Awumnhi+6JIG9idymytEARvViZlqYhUntCWFpsO7"
    "BF2jOYna5CJHKTxKSdLMwopSQZ0pYsgdOcC3im+1zrci2ejPkRmWnXgllpKjmJodLSsX1ktyIoG+6zZR58LOZHcotHfkniA9+bH1"
    "lsU+LcNPwid2FhB3E09nFuGWNSMuwyeKL6xYNPAu09ll2pvamm08+66h3PYTV4E0Gc4hLiMdnWK7AFarJbLH9uSW2sDdm6lib0pK"
    "g8RXd7I2NJfgDO2aljnBYrKa6ahjVfsNB5xm6Lbm1q+Fj8N4EA1yl7B/HagmHEj6oOHrCMjsoKamslQQ0jdrw+0IkTJJfbZfnTsi"
    "ZT1857ZfJexGY8stLRLGkK7LYfkr+TOSF1UMNzjg//7F//yKRy9tW+Q1l0StVUZM6HLrjRutLYbL4AsnELh2lMSAWYmEORgHRdV+"
    "nhBXnFjlugiaZF3JUpOWdqSomk9knPDHdEfjtxDwFVpBma+1LFX+5Eg3D5Sn0D7R/4KpK67zpwGIyO4DrqJWZuKedgtgqCVCAZwc"
    "qnY6ZAHtLEgDUVmyLNZghbSqcPF/iBJyzh3ToXJfsvi8Qhp+iTjtDWFgyCMSAYx3wEVrGLui7dqt9sYwr3VkiSlL1lQF0IdimvoB"
    "pBsz0Q5XTWre62yp/DlOnjxksxxP5qWUo30c3m657uZctmBkfts6SoNePa8wCFVx1wKTeIaOGMgJ6zfJI4Zeq/khbKqtTcdd8zWJ"
    "vKXV1qbcwEbMaB6pF+56yZlbpy0VYq3LnAnLRfRodnHkYeESDs1NIYflhZAKK47H7P5U8m2dNb8Pq+aOOgXMhALahG+/qVpdwWJv"
    "cd3dBhDlN28RW+C2X7VMx73sN353xopKyqm///oXX/+v31/++qt/ldar9NT9V+OG09A2IG3fc4amQ7M56tQOwNiRy/rhGxf1DRPJ"
    "uzeIflE8m84DWDzCfspSv1sCGOdR3dXPeLAzgrGhrYR1bNOWYRuy+U9fDPvCahGX/UEUNdfEdZfn79wi9G6a8bUifKR7ofrI1D7+"
    "eBdxIo6lmFcvFnvVWD6lWAAlFlUf0pHS+hZCt5AqYZp3sSCs2JF1mxQn1GssE4TNAWxXKNdVwF+z8TZOo53XQviKa/+kbR8jXStg"
    "rcTFfjcAKZRtF/z+BbaBkzUn22adGoYgPnvJFn5uDCqmOYJdoLZxcx4K74lwPV6MbGRVaJq+MaX9F7z33IKKvEYR2b3D7sG/+o32"
    "UjVVQ3BxK5L1FdpSwQU2hT2tR8fQ1J3FiE19gbRQAIgXC2gXjpxB9tzqWcnObmj/8dzhmhLCP7Rwu1Jb0Ve92XNpV5xY2gbF9i5z"
    "kSySoLKV30VRxlOuIfZWT5u3Jbi0GpGgF6V77bvsxD42Mt14pl4F5Tg4BuMf7dtXl1m+aKhShhLeP7REglnFieC4n9/kx0RbVLoL"
    "aYJKwVXk6nFT+y9wUGTf8MhZ8cr0Zurv8y+zmAifhVyhFwLTDNU0Gzatdj9zyaTmnQYUqGlFGyDgVFVF+s0//Oo3/7eoSCjZ1dre"
    "lDAXTQYouEaNs3FCuh904QpxR88nvwiQI+fkZg83C1ZhcAVuHcOPZPvat/7QopxVc5A0ylH7OakE5Wco4VvVjhnXZNlXcPg+s75s"
    "SKTAIOlIZOQk9gfdMbhWYj7e4eaIQyFN4/+ciD9+4eKBt7mjI1MNk73Le1UxFjjBvmcSA2xPEh9O2tiQMX1a9NVLrY9VODYVW+Bv"
    "sTc+7BCp1ry9XXUCqhKiV0mgGLNKGWOrKW4x4yLCXIC2KxxrspzFLqxX1q9hasfWHVKnsXJQ7BhcAUR3DCkbSRlPuzDxqYWwWyaY"
    "dgEKf2hhkz/pjeTLGpo94/clUa+/33ESL2r/BHvRdW9+roVbjVcvMfLMOXEz1vHFWPFdHW5ny0fg1GUFkS/xsJit6/irpRqiEDb9"
    "1LVnqrvsmulNYr0B2/Q9s8LRh2JtGlHjCqPi0AJUszM0Dm1mlAVYbDFXJ0a5oCdf5jIp1mjYCwSz7hmXXMOHhcBLE0NaVIhmA4+Q"
    "3BQ2bfUWnSyxaD3eE9s3Jj+LS5fiyp5kJ0Czd5SiscCT8IDQGmo/0dSpgqtJSbt2CblExWov/RHVdyO9e/RySlRQPOLFtoExiTvb"
    "Vp2ULiYUy2wjE4O6+phwdiOIwMB9JsItV5ExKXBdayGteCHDuVhrt4IERsWwsGqmy820BQxcenuMR5g+GL3TJhoe3jVbtSHvrmi1"
    "LXqT+9ifxL8uhgtK0Ixm2GiXd1OsS9N0e95k3+jlpemify9sbLH4SB1sfvfUWNkCODKEkHYrQdHmHY6smMIpgHoRVhFl2sotwjyL"
    "9bSUUn6jprMoA/ACrNUcZmBiYtAFJtnZ3Wz/K43MdrZCoVEud0H+cvBy4f8uFjy/xHKk1GcUgjLeWYDozLhKGFyd7dBOwCYU98V+"
    "EQG3abu7P+tgpomrWj5BBR2wPT8PhtAvyiFinbK4esOnT6R3fbTBEbsAKZ/QlDQkzUH8i3eL8wu7WnHENG2zftDz4LDPEP5+OPzH"
    "y4RjUTZi6arrJBRblygdNzzaA8nZ6ZEMdmN2vxT5Xp+i4razoQTVkw+dv9MXsV1QFDVa2ychFEy81kaxfoDEeyIxRhea5peVV1qo"
    "jMzVXGDIjsjzWuLPegMmbejYti6YUEdvOSlTGc+undr0wrtvnIckEMRSGFS9DaZIKKTHKZjJFzRczUEuj1RE+mDLZWYRocMHbAEp"
    "Ha5jpUStjHdOgBYF5GkgNGWe0uKBFvnZ8EEVJEVIDW2EGvAjkm4EnMXvuCtX2XnPz4j+W2cXHdC8YcPZww3+6cVBml5mXXM2ncFc"
    "b1OR1YWFR6H3qZD1J8PWRCDktwjUiiVnQNU/XiZ0SwiQPjhc7gfMMoqW/rDmpNrOLoLEWkBQqvCmUnblCkeRplplGoTQatoui54R"
    "CmLV1nLDjluw/rqtvGsumC1DrI40Sewx4qhbWaEmxprYHvZqFSOFWXoen7T1zTTVTzMF/XWo8wNoGbvuvpPUBQYptQ8rSsGqWGgt"
    "3iF8NPLj0xsvkU/UWlKgWKyoSoxu99Qyd3syKzMWLsZ0LNkekQIkUpPBVe3VyFTrQHBHzioTzAscUMXJbc3wT82qnF9UVukqzMVJ"
    "z3PbLdqYrc3bhxsKS8UcD5ZFaY+uyUXEanjkXL8pSAEjl28W0MesKUA7/0A7UOpnhOipVdMta2hfXftMahd3PpeMhycOLJLGwnFt"
    "LJJ1ingBzr5WkS8F9aXPKwLlSTumwgoIDrz4D0zVchNGIMsKYcyYgK0wG8nSn7aiBMgWP4Qzyq9ipkZstXIW1KZibdZirZcMIduf"
    "1QQtU6gQplKhts7nUtDApbJk+Hl+sXaGBCEUqa3DI4RyQ6TJousCTsEQHtiRFa3vp1jjCfKnpoNJNNGAR3H87nbMO+95YIydthuQ"
    "2cTDHvr2YKHdf1rx15ttrd+zIe5tvelCxbCi4GBfV2TPKdwTz1JUXIziTP35W3GHFebqhWZp+I7yoy+sv8SrCR7P/lL+43RzxzJc"
    "ZqQrAq4n4c1tje0+UoDYsbMFWKcgZKiJRD/XBMDKf/jF/3P5q3+Jtu282qTVPrzWLVqQH9GE1+STRBX/x48YmgTPcrv2n7pyrVma"
    "JDW1fN8sJRZMPdNLYTF2M7Kxyett5DW6O5NZXwF+20oLm+ogCpsC2QDkUDqRchvOhKdFJfwSMw5xtGSLIs6H3bncynEkOkxKb0Sx"
    "lFzYfhxWKBWdPNL1AqBi1Yq8hhA1FlINDlC1IaD+0njreocPFB3D+ilciPdcp6bVqDiHeCdiglRqcIHqkaA+cXWRt2tliaFD1qDF"
    "pbnz0FWryExIX3ApdqH9SDX63gJzSJNX1AbDynDRiFQTV8tbb+Jx4t0crub3nXHdHAzy1DuY1HvdC/Qlv8L7UWjE1DZw6pWbfGjW"
    "4lLVJdEl1svF3DOplivtP+iO8jNM41qwOMVBY/1G6KRIXmkD7Y+lYVE2pHg4rFx4+9Lt/w/OZ2kl"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
