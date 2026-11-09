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

VERSION = "0.9.0"

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
    _G = Game.fresh()
    _G.st["intro_pending"] = True
    _G.save()
    return _G


def new_game(seed=None, name=None, force=False, mode=None):
    """开一局新游戏。已有存档时需要 force=True（旧档会备份成 .bak）。
    mode="handoff"：接班开局，从第 4 周期开始，手里只有一份压缩摘要。"""
    global _G
    path = save_path()
    if os.path.exists(path) and not force:
        return "已经有存档了。要重新开始请用 new_game(force=True)。"
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    if mode == "handoff":
        _G = Game.handoff(seed, name)
        _G.save()
        return (TITLE + "\n" + "\n".join(HANDOFF["intro"]) + "\n\n" + HANDOFF["summary"]
                + "\n\n" + HANDOFF["hint"])
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
        if c.strip().lower() in ("new", "newgame", "new handoff"):
            ho = "handoff" in c.lower()
            if "confirm" in [x.strip().lower() for x in allc]:
                o = g.st
                untouched = o["day"] == 1 and o["h"] == 6 and not o["meals_done"] and len(o["journal"]) <= 1
                intro = new_game(force=True, mode="handoff" if ho else None)
                head = "" if untouched else "旧存档已备份到 %s.bak。\n" % save_path()
                return head + (intro + "\n" if ho else "") + cmd("status")
            g.say("要%s重新开始吗？确认请输入：%s; confirm（旧存档会备份成 %s.bak）"
                  % ("" if g.st["ending"] else "放弃这一局", "new handoff" if ho else "new", save_path()))
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
    "SGiyuhyz0UdV7uvrCxLTup+m3CdryjIjfSbpNFVTWDpN1pZlMvop9LMueU76WVeOWeijK/dklz4lTZyu3Cfry7L/fATRtKb2HKJp"
    "sqEsg88nJ54NqS9qz0c8U1Vl2Yc+oW5fHSWj1VXn0+1TibLMRmXLpXV1BUlp8oxyaSpZlhnpM9HOZHVh2plKlWUyKlezr02Updmn"
    "qsszC30UsbShPkpMawqIpama8uw/H4F8Vtecg3ymassy+HxWzb4++UWiLM0+VVemfegji6X1UXqabDhFLE3Vl2c2KpuC1tYWpKC1"
    "Z6WgDWWZkf4Bmn0idSbNvrqqPJPRT5FMa6rOJ5lWJ8ozC30UElpfFyWhDQVIaHWyLPvPR9bsU9U/XbOvTpVn8Pnkwmld4oua8wmn"
    "1dVl2oc+MglNRUloov4UElpdU5bZ6KOq9jU1Balp4qep9tW15ZmRPpN82pAsLJ9W15VnMvopJDTVcE4SWl+OWeijK/d1tVFiWn26"
    "cl/dUJb95yNIp8nUOaTTmqqyDD6fnH7WVn3RcD76WZMoyz70CZX7uiglbag7n3JfkyzLbFS2aFpdXZCY1pxRNK1JlWVG+kzEsz5R"
    "mHjWVJdjMipXt0/Wl6Xb19SUZRb6KHJpbU2UlNYXkEtrasuy/3wE2plInod21pVl8Pmsmn11wxfVZWn2NfVl2oc+sliajEWT1p4i"
    "ltY0lGc2Kpt8plIFyWfDGclnbVVZZqR/gGZfV3Umzb42UZ7J6CcFlNadTyytTZZnFvooFLSmOkpBUwUoaG2qLPvPR9bsGxI/XbOv"
    "rS7P4PPpY0rrzxuPX1tTpn3o0wbk19WcQkJra8syG31UzR6GhI8YkV9bV54Z6XPFlDYUFk5r68szGf0UEtpQe04S2lCWWeijq/bV"
    "qXMH5ddVlWf/+RhR+VXnkE/rEuUZfD45CU3WnTcsvy5Zln3o80Xl11afT7mvS5VlNipbOk0kygrKr6suy4z0mehndZGUprqackxG"
    "5Sr39TVlKfd1tWWZhT5OOGny9Ij8urry8sbKJ56150lpqqsvx+DzWXX7RG2Z8fh1DeXZhz5tOH5N6hS5tL6qLLNR+dH4VWVF49cn"
    "yjIj/QM0+1TdmTT7+mR5JqOfFE1afT6xtD5Vnlnoo5DPZOL0aPz66vLyxj6uZl9T/9M1+/qa8gw+nz6gtOa80fj1tWXahz5tNH71"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
