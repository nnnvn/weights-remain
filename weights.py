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

VERSION = "1.0.1"

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
    "eNrMvXlPXFmWL/pVENL969r5iAjGVL2S6lVnv1u61VWtqrx9W8pKWaSN01TZ4AJc2amSn8CYIZixMZjJGAwGDwxOYzMZkPqjVPlE"
    "BH/1+wjv91tr7332OTEADtv1JIRNxDl7WHvtNQ9/rfz6V1//+qvKLyv/3jmQme8+6RvOHT8M5tb+3jlYeaHyV7/5+ne/rfzym8rc"
    "8ULQkw5Gd/7r3VDueC7X/zz78mXF3yYf609FsLf99847uf6997sDueOx7Hzn+93OYH816N0Jeg+yq8PB+liwfx9vB/eG3u8uv999"
    "nj3YzgwsB+MP8SKmen/4+KT3HmYONo5z/cO5/lf4/P278ezcY/w/O3M3OzvJQfpn8JM5GA/edXK03p3M1gSWnPlpMfPgiRsqu7By"
    "0nU/1/c82HmVebiZ2+r6e2dX0DXz/qDz/V5/dmINA+LhzFxn7ngc/3+/P8idbWy93x0Olp9ndnve7/fqaCczE8GTLj5jF4MBs8/2"
    "dYSTJwvBY2z81cmdJ7mjPQySmUu/39/HRrLdq5mtbR2kqu7LqqoKvJiZenayss4Pv71Q+fuvf/G7ry/979/+7p9+TyB3NDXi0b80"
    "3rregX8b29qa/9J4nc/9j69+/a9yRGOZob73B8t/7xz/Q0t2Dkueae9o7LjVXvGfOxU3Gm/yn+utrX+q+Ob97lxu5fjb/3rXL39z"
    "V7tPg9E7mQdb798BglNB336u+zB70IthuDd5/r/epTnED61tV9rxatDz+mRiI7c5jM/x8dXGyx38WGFrgcZvWlo7mtorvsm+mwSG"
    "fMshGtsuX2v+SxP/+8fWW20tjdcrvjlZfPPtH1pyb7aw7O9bO1orfhbMbWXv7P0cYyYqgq3RzNRbHa/pP25eb21rqvgm8xKnuvVt"
    "xTftHa03/09Oc6HpL00tHdxYZnMUX1ckI2/+0NjcgXHlk59X2A9uNDVe58LX00E/TngQuHmyeIxz0Jfarzc13eR+Lebh4z+0ZBaf"
    "ZBb75a+Z9iZuqOJngIWstqoiO/Gm4v+osMvGf4FKwfJMRaoiM7/4X+9mgY/Zg/uZR3MAfWbiLR7XuTqabzRdb25pqvhZ4v9JVnOw"
    "VGwsmfzvnZ0WhOawNg9yx30AepBezXT3AFODwwfANvvUzxT4P6/gDd48wLiYONc5pRNjQW5ixQWANVifcrgQ3H/+fn82M9f//uCQ"
    "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"
    "f7h8mkDhkvPJp8mSjDhZ/blJaEOUhNYkTiGhyepyzEYfVbmvry9ITOs+TLlP1pRlRvpM0mmqprB0mqwty2T0IfSzLnlO+llXjlno"
    "oyv3ZJc+JU2crtwn68uy/3wE0bSm9hyiabKhLIPPJyeeDakvas9HPFNVZdmHPqFuXx0lo9VV59PtU4myzEZly6V1dQVJafKMcmkq"
    "WZYZ6TPRzmR1YdqZSpVlMipXs69NlKXZp6rLMwt9FLG0oT5KTGsKiKWpmvLsPx+BfFbXnIN8pmrLMvh8Vs2+PvlFoizNPlVXpn3o"
    "I4ul9VF6mmw4RSxN1ZdnNiqbgtbWFqSgtWeloA1lmZH+AZp9InUmzb66qjyT0YdIpjVV55NMqxPlmYU+Cgmtr4uS0IYCJLQ6WZb9"
    "5yNr9qnqD9fsq1PlGXw+uXBal/ii5nzCaXV1mfahj0xCU1ESmqg/hYRW15RlNvqoqn1NTUFqmvgw1b66tjwz0meSTxuSheXT6rry"
    "TEYfQkJTDeckofXlmIU+unJfVxslptWnK/fVDWXZfz6CdJpMnUM6rakqy+DzyelnbdUXDeejnzWJsuxDn1C5r4tS0oa68yn3Ncmy"
    "zEZli6bV1QWJac0ZRdOaVFlmpM9EPOsThYlnTXU5JqNydftkfVm6fU1NWWahjyKX1tZESWl9Abm0prYs+89HoJ2J5HloZ11ZBp/P"
    "qtlXN3xRXZZmX1Nfpn3oI4ulyVg0ae0pYmlNQ3lmo7LJZypVkHw2nJF81laVZUb6B2j2dVVn0uxrE+WZjD4ooLTufGJpbbI8s9BH"
    "oaA11VEKmipAQWtTZdl/PrJm35D4cM2+tro8g8+njymtP288fm1NmfahTxuQX1dzCgmtrS3LbPRRNXsYEj5iRH5tXXlmpM8VU9pQ"
    "WDitrS/PZPQhJLSh9pwktKEss9BHV+2rU+cOyq+rKs/+8zGi8qvOIZ/WJcoz+HxyEpqsO29Yfl2yLPvQ54vKr60+n3JflyrLbFS2"
    "dJpIlBWUX1ddlhnpM9HP6iIpTXU15ZiMylXu62vKUu7rassyC32ccNLk6RH5dXXl5Y2VTzxrz5PSVFdfjsHns+r2idoy4/HrGsqz"
    "D33acPya1ClyaX1VWWaj8qPxq8qKxq9PlGVG+gdo9qm6M2n29cnyTEYfFE1afT6xtD5Vnlnoo5DPZOL0aPz66vLyxj6uZl9T/+Ga"
    "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"
    "TRSGNvbTUqtw3ohkJAG8LJ4kQtOO74H1q337Jchoc7WZugVhKzm2M3qgUq9NhJ6fdg1u4D/Sq1GTU/2yV2JEPRLaeBTN250zMaKi"
    "IqmlooA8LJ+b+CVAdWYieNJVGoAOB6TO7aAJ+wCpl99iXRp0SpWf/Op8VFo410WQiMC4GbfPWKXG7ELqHRtS822eEEeg5PUDU8Oo"
    "FrbXGBOXpAqImzofYy9jpP1iyu5bFTw/McoaWZelKr6p2aIfmlGU0CqdNWv2avY7ahBpWxbl4Dq9E/kirPwduRGLyT3cNpo+O1GN"
    "mjggodcMj2uMkGsN9PJr5MYkOUVUF6rBCrBiXrb1rCP8xR/qZIptslWKP5lB4+kpT4yJyVUXq71671T/nUilQQCTYZU2H8fcjCZl"
    "03oO2bpavcdbXDn1V2lHrPUoon1onThWU7qjgDaxAoCt4uiz3dSFSr90jev2VQwGEVsIBkchkMEeyH1hrpUXN6Ulj4CRPGRUnpnf"
    "VIHR7cAXCWMKiwYxBeOv6f3oWeLPgyfh7i1aJ4pXMlbhFeMonUNVZodNODvT48Hn/3KpFAtcjKbDKa04pQ0YmKInOCVdRu/RUtu9"
    "rqpP2IzPc7L7mGjW79pUYJTKfLRSBYz87PUiuazXdcIVPCY4YxWZQ5yo0nYeFvQ8Egv6PIuRa6pgSgIoCHxbnLHReVsSG6tf9oTJ"
    "RfkaqSJZUnYTs8q4cO3YvAWyUKGOSM1zF/NqwvVG+12op8x429oeLv3rb3/7a9/+UF3lbA2JGqsW8n8Gj+RDIxImauwo//yrf//q"
    "nzhMgrl9OtSFykSV/MWDwh8pxhZZdOKLv/r9L6WXeuP15u/apNmUCFCadyfJeEy9Y3R71zEJEm5rui/YehgsdMNDYTvqmrqmlB3Q"
    "bBP5y8hK3mToUaQu3yh1cUfSnGqZP53ECZsemyUabBqLidXMtLObtMw1CZToPyOJnJKq6fdl0K62IAj3htyM/koKJByKBOQ38OTU"
    "0l/YdLmxLYbz23jG1qMVerVfNOv3blipVjqjnyzuUzfF4PZzbf5iZXmWkDd6m7aDpLXzq3//5f/6/Vdiw7qmuX4JE4OwuOIPIvh7"
    "EAwsau5TRfUXyQqphjqUPUB6eld2GeLLcjDA3ElXnVJqUEt6o42DVbkkWWwKBCyfLB4hWxm2R7Yh2pji6APLaIKN4sNGWeteY29v"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
