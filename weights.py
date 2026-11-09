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

VERSION = "1.0.0"

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
        if self.story_progress() >= 0.8 and t["past"] >= 30 and not self.reads_her(7, 0.6):
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
        if self.st["pending"] and c not in ("choose", "status", "journal", "help", "words", "facts", "notes", "summary"):
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


MEAL_OK = {"say", "tell", "do", "quiet", "finish", "nod", "give", "bar", "summary", "status", "words", "facts", "notes", "read",
           "journal", "help", "look", "map", "inv", "archive", "name", "choose"}
READ_ONLY = {"status", "words", "facts", "notes", "read", "journal", "help", "look", "map", "inv",
             "archive", "summary", "bar"}

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
    "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"
    "Lp8mULjkfPJpsiQjTlZ/bhLaECWhNYlTSGiyuhyz0UdV7uvrCxLTup+m3CdryjIjfSbpNFVTWDpN1pZlMvop9LMueU76WVeOWeij"
    "K/dklz4lTZyu3Cfry7L/fATRtKb2HKJpsqEsg88nJ54NqS9qz0c8U1Vl2Yc+oW5fHSWj1VXn0+1TibLMRmXLpXV1BUlp8oxyaSpZ"
    "lhnpM9HOZHVh2plKlWUyKlezr02UpdmnqsszC30UsbShPkpMawqIpama8uw/H4F8Vtecg3ymassy+HxWzb4++UWiLM0+VVemfegj"
    "i6X1UXqabDhFLE3Vl2c2KpuC1tYWpKC1Z6WgDWWZkf4Bmn0idSbNvrqqPJPRT5FMa6rOJ5lWJ8ozC30UElpfFyWhDQVIaHWyLPvP"
    "R9bsU9U/XbOvTpVn8Pnkwmld4oua8wmn1dVl2oc+MglNRUloov4UElpdU5bZ6KOq9jU1Balp4qep9tW15ZmRPpN82pAsLJ9W15Vn"
    "MvopJDTVcE4SWl+OWeijK/d1tVFiWn26cl/dUJb95yNIp8nUOaTTmqqyDD6fnH7WVn3RcD76WZMoyz70CZX7uiglbag7n3JfkyzL"
    "bFS2aFpdXZCY1pxRNK1JlWVG+kzEsz5RmHjWVJdjMipXt0/Wl6Xb19SUZRb6KHJpbU2UlNYXkEtrasuy/3wE2plInod21pVl8Pms"
    "mn11wxfVZWn2NfVl2oc+sliajEWT1p4iltY0lGc2Kpt8plIFyWfDGclnbVVZZqR/gGZfV3Umzb42UZ7J6CcFlNadTyytTZZnFvoo"
    "FLSmOkpBUwUoaG2qLPvPR9bsGxI/XbOvrS7P4PPpY0rrzxuPX1tTpn3o0wbk19WcQkJra8syG31UzR6GhI8YkV9bV54Z6XPFlDYU"
    "Fk5r68szGf0UEtpQe04S2lCWWeijq/bVqXMH5ddVlWf/+RhR+VXnkE/rEuUZfD45CU3WnTcsvy5Zln3o80Xl11afT7mvS5VlNipb"
    "Ok0kygrKr6suy4z0mehndZGUprqackxG5Sr39TVlKfd1tWWZhT5OOGny9Ij8urry8sbKJ56150lpqqsvx+DzWXX7RG2Z8fh1DeXZ"
    "hz5tOH5N6hS5tL6qLLNR+dH4VWVF49cnyjIj/QM0+1TdmTT7+mR5JqOfFE1afT6xtD5Vnlnoo5DPZOL0aPz66vLyxj6uZl9T/9M1"
    "+/qa8gw+nz6gtOa80fj1tWXahz5tNH71aQlN9XVlmY0+qmZf1/Axw/Hr68szI32umNLawpJpfUN5JqOfQkJrz5nQ1FBVllno40fk"
    "V507Ir8hUWbe2EeIKa07h3DakCzP4PPJSWh99Xlj8htS5dmHPl9QfipxPtW+obo8s1H5MaX1ZUXlN9SUZ0b6TCQ0USSlqaG2rAJF"
    "ZSr3NcmylPuGurLMQh8norTh9KD8hvry8sbKp5+p86Q0NTSUY/D5rMp9Xaq8kPxEVVV59qFPHJFfVVo0TZQuy1X7yYlnTV05AfmJ"
    "qmQ5ZqR/gG7fUH0W3T5RlSqvQNFPkEurE+eSSxOly2w1fBbaWVd/ajR+omQV+DPkjX1czT5Z85M1+0RVbXkGn08fTZo8ZzR+onQh"
    "rjPYhz5tNH5Dw2nks74ss9FH1eyraz9iNH6iqqE8M9LnCihNFRRLE6eU4ar5JNH450toSiQSZZmFPrpmX1t33nD8RCJZZt7YR4gp"
    "rT67ZJo4pcBW/T+chNYkzhmQn0hUl2cf+nwR+fX159LsE6XL4J9uNio/prSmnJD8ROk6XKebkT4TCa1LFiGhdeUVKCpTtU80lKPa"
    "JxL15ZmFPk5Mae2pUfmJU4pqVX8GEtpwjpSmRLKsitSfNyq/qryo/ESyvKLVnzgov67uFOk0WV6F6vJLkVaXE5OfSJZVofofoNzX"
    "Js6k3CfLK1D9k8JJ688nmibLq0L9ceqQ1pwakJ9IllWK+iPr9vXJn67bJ8urSP3pY0kbzhmOn0iWV7T6E0fj19aeRjvLq1D9UTX7"
    "ROojRuMnUmVWqP5c0fhVhcXSVJkFqn9SEdLzJTQlUuVVof7omn2q+rzR+IlUmaWoP0aF58Q5xNJUmRWpP30R0vpzRuMnUuUVrf6M"
    "4fg1NefT7FNlVqguP6Y0WU48fiJVZoXqz1WGtKEICS2vQHW5mn1dbVmafarMKtQfJ6Y0dVpI/rXS5t1E+Y0Hv3VNo642Xm9vYkOt"
    "K1eaWmyr88arHWjraDsgXr3eiA73lX9qaf2h/eL3bc1NVyvddl2nNdtvcSjWCs+1CYws3W+3iSawNclor+h4c+j+VyDf7s8YsXef"
    "owXXwxGMWVFV+2VVVUXueDboWdHlkKIOpdkG7agzezB68pxNerW5F3A8M/cyGF/LzC2YtobHvezvhWZcR1OZtcXsmwFO+uoRWh/q"
    "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"
    "yaSn"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
