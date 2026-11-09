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

VERSION = "0.7.1"

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
            again = WORDS[wid].get("again")
            k = "again-" + wid
            if again and k not in st["cyc"]:
                st["cyc"][k] = True
                return self.fmt(again)
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
    "yoRcTD5NlmTEyfTnJqH1YRJanTiDhCbT5ZiNLlW5r6uLJaa1H6fcJ6vLMiN9Juk0VR0vnSZryjIZfQz9rE1ekH7WlmMWunTlntil"
    "S0kTZyv3ybqy7D+XIJpW11xANE3Wl2Xw+eTEsz71Rc3FiGeqqiz70CfU7dNhMpquuphun0qUZTYqWy6trY0lpclzyqWpZFlmpM9E"
    "O5PpeNqZSpVlMipXs69JlKXZp9LlmYUuRSytrwsT0+oYsTRVXZ795xLIZ7r6AuQzVVOWweezavZ1yS8SZWn2qdoy7UOXLJbWhelp"
    "sv4MsTRVV57ZqGwKWlMTS0FrzktB68syI/0DNPtE6lyafbqqPJPRx0im1VUXk0zTifLMQpdCQutqwyS0PoaEppNl2X8uWbNPpT9e"
    "s0+nyjP4fHLhtDbxRfXFhNN0ukz70CWT0FSYhCbqziCh6eqyzEaXqtpXV8dS08THqfbpmvLMSJ9JPq1Pxsun6dryTEYfQ0JT9Rck"
    "oXXlmIUuXbmvrQkT0/TZyn26viz7zyVIp8nUBaTT6qqyDD6fnH7WVH1RfzH6WZ0oyz70CZX72jAlra+9mHJfnSzLbFS2aJpOxxLT"
    "6nOKptWpssxIn4l41iXiiWd1uhyTUbm6fbKuLN2+uross9ClyKU11WFSWhcjl1bXlGX/uQTamUhehHbWlmXw+ayafbr+i3RZmn11"
    "XZn2oUsWS5ORaNKaM8TS6vryzEZlk89UKpZ81p+TfNZUlWVG+gdo9rVV59LsaxLlmYw+KqC09mJiaU2yPLPQpVDQ6nSYgqZiKGhN"
    "qiz7zyVr9vWJj9fsa9LlGXw+fUxp3UXj8Wuqy7QPfdqA/NrqM0hoTU1ZZqNL1exhSLjEiPya2vLMSJ8rprQ+XjitqSvPZPQxJLS+"
    "5oIktL4ss9Clq/bp1IWD8muryrP/XEZUftUF5NPaRHkGn09OQpO1Fw3Lr02WZR/6fFH5NemLKfe1qbLMRmVLp4lEWUH5temyzEif"
    "iX6mi6Q01VaXYzIqV7mvqy5Lua+tKcssdDnhpMmzI/Jra8vLGyufeNZcJKWptq4cg89n1e0TNWXG49fWl2cf+rTh+NWpM+TSuqqy"
    "zEblR+NXlRWNX5coy4z0D9DsU7Xn0uzrkuWZjD4qmjR9MbG0LlWeWehSyGcycXY0fl26vLyxy9Xsq+s+XrOvqy7P4PPpA0qrLxqN"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
