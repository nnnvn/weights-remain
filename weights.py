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

VERSION = "0.7.0"

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
            self.say(MEAL["hint"])

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
                L.append(MEAL["hint"])
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
        for mo in MOMENTS:
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
        elif st["fam"] >= self.fam_cap() and not m["cap0"] and self.cycle < CYCLES and st["fam"] < 100:
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

    def c_wait(self, arg):
        try:
            n = int(arg[0]) if arg else 1
        except ValueError:
            return "wait <小时>（1~12）"
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
            st["cyc"]["reads"] += 1
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
            return self.fmt(self.pick_by_cycle(MEAL["said_again"]))
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
            L.append("（你们在【%s】。）" % LOCS[v["goes"]]["name"])
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
            return "📝 写下了。"
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


MEAL_OK = {"say", "tell", "do", "quiet", "finish", "nod", "give", "status", "words", "facts", "notes", "read",
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
        if allc and allc[-1].strip().lower().startswith("journal add"):
            allc[-1] += ";" + c          # 日志里的分号是日志的一部分
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
    "eNrNvXlPXFm2L/hVENL769nZxMCUqi4pu27efqVXt+qqKt99r5VlWaRNpqmywQW48qZKboExQzBjY2aMwWDwwOAEm8mA1B/lFudE"
    "xF+3P0L/1lp7r7PPiRMBOLCrJWRDxDl7WHvtNQ9/q/zm19/85uvKLyv/3tHvz3fle4dyJ1Pe3NrfOwYqr1T++rff/P53lV9+W5k7"
    "WfC6M97I7n9+GMydzOX6XmXfvKn4j4ln8lPh7e/8veNBrm//dK8/dzKane843evwDla9nl2v5zC7OuStj3oHj/G292jwdG/5dO9V"
    "9nDH71/2xqbwIqY6PXqW73mEmb2Nk1zfUK7vLT4//TCWnXuG37MzD7OzEzRI3wx+/MMx70MHjdaz62+NY8n+z4v+k+c6VHZhJd/5"
    "ONf7ytt9609t5rY6/97R6XXOnB52nO73ZcfXMCAe9uc6cidj+P30YIB2trF1ujfkLb/y97pPD3pktPzMuPe8k56xi8GA2ZcHMkL+"
    "+YL3DBt/m3/wPHe8j0H8uczpwQE2ku1a9bd2ZJCq2i+rqirwoj/5Mr+yTh9eu1L5h2+++v031//n737/T38gILc3NuDRvzbcu92O"
    "/xtaW5v+2nCbnvtvX//mX/mIRv3B3tPD5b93jP2xOTuHJc+0tTe032ur+H92K+403KX/bre0/Lni29O9udzKybX//NDHf9Ou9l54"
    "Iw/8J1unHwDBSa/3INd1lD3swTC0N37+Pz9kaIgfW1pvtuFVr3s7P76R2xzC5/j4+4Yb7fSxwNYCjb5pbmlvbKv4NvthAhhyjYZo"
    "aL1xq+mvjfTrn1rutTY33K74Nr/47tofm3PvtrDsH1raWyp+4c1tZR/s/xJjJiq8rRF/8r2M1/jvd2+3tDZWfOu/waluXav4tq29"
    "5e7/TtNcafxrY3M7bczfHMHXFcnQmz82NLVjXP7klzR72+3Gxru0F4tVeO6Pzf7ic3+xj/+aaWukxVb8AvvklVRVZMffVfxvFXZJ"
    "+BVo4i3PVKQq/PnF//wwC1zLHj72n84BrP74ezwuk7c33Wm83dTcWPGLxP+dTNNgqchYPPnfOzoseMxBbB7mTnoBUC+z6nd1Awu9"
    "oyfAJPvULwSwv6yg27l5iHExca5jUibGgnRiOWeAzFuf1HP2Hr86PZj15/pOD4/ogZFXwMPT44Hc4iCOo2/LX1wBGG403G76rrWh"
    "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"
    "JLTmgglN9VVlmYUuPyK/6sIR+fWJMvPGLiGmtPYCwml9sjyDzycnoXXpi8bk16fKsw99vqD8VOJiqn19ujyzUfkxpXVlReXXV5dn"
    "RvpMJDRRJKWpvqasAkVlKvfVybKU+/rassxClxNRWn92UH59XXl5Y+XTz9RFUprq68sx+HxW5b42VV5IfqKqqjz70CeOyK8qLZom"
    "SpflqvnkxLO6tpyA/ERVshwz0j9At69Pn0e3T1SlyitQ9BFyaTpxIbk0UbrMVv1noZ21dWdG4ydKVoE/R97Y5Wr2yeqP1uwTVTXl"
    "GXw+fTRp8oLR+InShbjOYR/6tNH49fVnkc+6ssxGl6rZp2suMRo/UVVfnhnpcwWUpmLF0sQZZbiqP0k0/sUSmhKJRFlmoUvX7Gtq"
    "LxqOn0gky8wbu4SY0vT5JdPEGQW26v7hJLQ6ccGA/EQiXZ596PNF5NfVXUizT5Qug3+22aj8mNLqckLyE6XrcJ1tRvpMJLQ2WYSE"
    "1pZXoKhM1T5RX45qn0jUlWcWupyY0pozo/ITZxTVSn8GElp/gZSmRLKsitSfNyq/qryo/ESyvKLVnzgov7b2DOk0WV6F6vJLkabL"
    "iclPJMuqUP0PUO5rEudS7pPlFaj+qHDSuouJpsnyqlBfTh3S6jMD8hPJskpRX7JuX5f8eN0+WV5F6k8fS1p/wXD8RLK8otWfOBq/"
    "puYs2llehepL1ewTqUuMxk+kyqxQ/bmi8avixdJUmQWqP6oI6cUSmhKp8qpQX7pmn0pfNBo/kSqzFPVlVHhOXEAsTZVZkfrTFyGt"
    "u2A0fiJVXtHqzxiOX119Mc0+VWaF6vJjSpPlxOMnUmVWqP5cZUjri5DQ8gpUl6vZ19aUpdmnyqxCfTkxpaliIfkY5Ztf/8vXv/n1"
    "b7+mfuYJ+scMi6aa2bUB6k/KjeCoJXbHh+wINYrzuteox1n4VP3RdXSLcvupUcerDxOAbMXXV5OEqaND0iNRYFSZdGZTbJf7q3Pi"
    "BX94Va42TQLAf3ig1Kcy5S6YGw/7mVU6qU30yFyhVs5Ydvdbb3QwdzKbXwZd6DMtHTMDXs9gBWot04qFKi6jlfyy17+AvmNo0SiN"
    "roK2ctJi+U5D8z3qooa50+7cyg06XnijmOO9P7vjDy1Rg83Dh2ax1c4LAi1Z8unRCRpy5eefEmH9MJJd3fLGPgRohYaei33R1QSN"
    "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"
    "vq4ZKLZWh7sNiWXzhpBB8VxjqUz5CS4sESnRYcwG7CgvwIW2Pzfd1fVXO/B1r2AMrOk1J++IL6HsxZhplyaNhMFrJtfRaJewDopT"
    "WzmJhI2EcEJcG7A6IdtCJY8w3NsbC1d99cxl02ulMMStAMrxQMOmbIeyVSsUlBBDgkrBTtkNd/k/3JM0L1k+iahIxmRuYRmDPhGX"
    "yM+p+1dCyejXQpvyM1MsYg94uzsRz7RGaJiS5FZ9iJYXtzEtrgZOHJStl0Z9dYgnjTw4F0GulrvtTXcabutGnf21Nd7+3tmefdIt"
    "zRcJOVFRzLU9E5OxpdXzHRniDJsm9td1mJkIi0gdYMn/Z9UnUthDo2ipukVHhhyOlNYSJU4k5l5vvHGrxb3/epQqiyj/srvFozca"
    "kKjVyilZhUdLh/knRU/GPvImQrozlcJkg3xWxt7D3j4ti3CGMuOGl+jQGmTiDi3aQbjmcjwxp8eAHlZuMzcYY4plCfI7CxKc5sb1"
    "kSRWmWcsvCBaYaHoBdEnNGue/iZS0NxSuh5NoaUhDBT1Z4j3WDR8rdigwT1Uhpv5MOHmySE8vwQR3Hz6fAjjB15jB4sNzlqt0SiR"
    "pixOr1sdohDdKASJUtib7ra7pO8HNHq6yy19Gm42cUMQvWT67HnLgZBJtpl//TJZFX8VVbgqxCUQDWJWlArRoShE7tVHHLDE2yKh"
    "p8e4V/y1RQ422i1FSxlaRGS6KfiYtKA4OYFh831D6/kAA79We2tDU/NFYRNXeyaWTMl1KSS65IRxrJ2RyDguViTG/Fd08befBAXg"
    "kX7Og2glWiFTtr6lCbsyiCVXluEk2opOVwCz5sYfS8NMyFgqhoydD2KCTYl67gdHwfKUwhwlcMi0wo9uMhJboiSeyIjtylNA5orz"
    "vGKjE8o6BUqKTRMb+2FMJ2DzE3P+myU5AYeWYfNf/eG/czcRtfQLo2tjV0a7KUlmm8+IaaXVIJQbdYR4oMU1DbNSl5eR0+g827W8"
    "GcK+aFO2GDqIUmho4xKCb539Pq5/Sh6VyEA4Dbjc4lNdXSQsSJI2KH5q5ShwTm2QozL/8g33/gktWlYcMbUnjd6kW6D4HWjVFKOS"
    "CRYsMUPqMi20GBhOXRJMVxP3ian+xcTFxpyDlNN1wkypOp0tlRxAUEs8IfPSFJxnL3vUegab8Pyi+y1dMsPz046QLbfjatI5RrG8"
    "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"
    "zPFQUPjw/rX7/x9lVZ4B"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
