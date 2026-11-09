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

VERSION = "0.9.1"

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
    "eNrMvXlPXFmWL/pVENL969r5iAjGVL2S6lVnv1u61VWtqrx9W8pKWaSN01TZ4AJc2amSn8CYIZixMZjJGAwGDwy2sZkMSP1Rqnwi"
    "gr/6fYT3+6219z77nBiADNv1JIRNxDl7WHvtNQ9/rfz6V1//+qvKLyv/3jmQme8+6RvOHT8M5tb+3jlYeaHyV7/5+ne/rfzym8rc"
    "8ULQkw5Gd/7r/VDueC7X/zz78mXF3yYf609FsLf99847uf69D7sDueOx7Hznh93OYH816N0Jeg+yq8PB+liwfx9vB/eGPuwuf9h9"
    "nj3YzgwsB+MP8SKm+nD4+KT3HmYONo5z/cO5/lf4/MP78ezcY/w/O3M3OzvJQfpn8JM5GA/ed3K03p3M1gSWnHm9mHnwxA2VXVg5"
    "6bqf63se7LzKPNzMbXX9vbMr6Jr5cND5Ya8/O7GGAfFwZq4zdzyO/3/YH+TONrY+7A4Hy88zuz0f9nt1tJOZieBJF5+xi8GA2Wf7"
    "OsLJk4XgMTb+6uTOk9zRHgbJzKU/7O9jI9nu1czWtg5SVfdlVVUFXsxMPTtZWeeH316o/P3Xv/jd15f+929/90+/J5A7mhrx6F8a"
    "b13vwL+NbW3Nf2m8zuf+x1e//lc5orHMUN+Hg+W/d47/oSU7hyXPtHc0dtxqr/jPnYobjTf5z/XW1j9VfPNhdy63cvztf73vl7+5"
    "q92nweidzIOtD+8Bwamgbz/XfZg96MUw3Js8/1/v0xzih9a2K+14Neh5czKxkdscxuf4+Grj5Q5+rLC1QOM3La0dTe0V32TfTwJD"
    "vuUQjW2XrzX/pYn//WPrrbaWxusV35wsvv32Dy25t1tY9vetHa0VPwvmtrJ39n6OMRMVwdZoZuqdjtf0Hzevt7Y1VXyTeYlT3fq2"
    "4pv2jtab/yenudD0l6aWDm4sszmKryuSkTd/aGzuwLjyyc8r7Ac3mhqvc+Hr6aAfJzwI3DxZPMY56Evt15uabnK/FvPw8R9aMotP"
    "Mov98tdMexM3VPEzwEJWW1WRnXhb8X9U2GXjv0ClYHmmIlWRmV/8r/ezwMfswf3MozmAPjPxDo/rXB3NN5quN7c0Vfws8f8kqzlY"
    "KjaWTP73zk4LQnNYmwe54z4APUivZrp7gKnB4QNgm33qZwr8n1fwBm8eYFxMnOuc0omxIDex4gLAGqxPOVwI7j//sD+bmev/cHDI"
    "B0afA1c/HA3mFodwZP1bmcUVgOFy4/Xm79oaO5oU/MHyM0ASh4B1YJ5gYLbiv9fL8gHazOIQnhK8x9VLNOBf3te5NXdjZF0z7Y0/"
    "Klx5Wh1N169X/Ax4JX9daQUi7w8KQn0vm/xwdJxdPzJb5R73FrIbU7zXu50fDt55CHmF5zm1SSr0ZEs//POt5qYOjnW1uaW5/ZoA"
    "OVjZ042QwAy8y3R2Yffc1GNSOtCYYG4fNzvoexyMrgBaJ52jJ3OvcX2DsSFvTu6LYBsbwjiZqRUQqpO+ocyDfkUAfI5X7IQnUxsk"
    "dlMbJ0sPcd4YNXswjRlAE4PZR9n1CSz98rXW1vbwVAVUQ5n03knfqJIAntzcWu54GvMEXZhk0M4A2ONSBWMj3LtgVNDbk3v7DqhP"
    "Ojm/EhxNBaMD3IOsBSvObc4Lvk6fdKZPFvcECYcUhpn+yb91zgGSf+ucxyqC/Qm8ZaE6xL0NP9Zb6nbI0xjO9I8phcBCcxvP/KXr"
    "coGSwcDah8O5YGw4k77PK9m9FvT34khzG8uYGaef2X0GCopp7dg4K1xcvWqZ0TFcfgAtM7mFDQCYJPHjA7nNNJaI66SHo3Tf/YmB"
    "AQXFI8UevR65rgG9XzpU0DuM8Qtub6yb2I1zw/3qPgQ5M5yop98gVgVgpsgm7wQ97z4cTALTWxpv4ESxXVy8n/uUsfHKlYqfZbrf"
    "BL3TugCiNqhH8KpT8QNokZk4UiyOYQbH+a6xraL16lWl2JmRtCJy5vGouQxNP+Ar0AUAihi9Oug+r7jW2HJFX82MrGRH1/n9q05D"
    "Fm/duNHY9iN2AGDPL+rhgXXmBl9lJvtwVJgfv8EfBRNICAwRrCcRxOb9DQIhT+aXOAto2NM7uBjkhjImX8cl6FkNdncz6VXQHGXN"
    "isP4s+L/Xbi/SO7pdiaQtWzi5OAhUCZ3/Ch7sM7z2BjSI8O4J11Y6JZeCWKiCARAQFLo7jeZ9BEZgiBDsKWfDwUDjyscu1Hu/evf"
    "/hIM+q+Vf77V2NbR1NbO//M0wZMdz8BjV5raL+MjiDfB+8fB/oCAhaKOkxwUUPgkB+ayMMc7uD8QLG+T+nRtEiAzk7n0axUtMGLr"
    "d39sAtPlfPoG/wci3NhOeQF/g9DgsR8a2660tX7XRFlB+CNW0XSp/VYz5YiOpv/g3/o+qdLQHRHPlnEhguXJYGWKm17Zw6SgdPgQ"
    "D5CcHT4GdYFIw5XcvlCJhUYmx98n9/cx/ndNVzivmcZtXukLB72/T4pz1J95sH1yOEY69ugB9pyZ6sLowGsVFjMTm9mXE2YyB7DI"
    "lPrph4P7mJUy4ewkJ77Vcr318p/4/bXm75qAbx1N0fX4B5AZWsN0mNSQP2wYkqgVLiG8UnoDKeq6jwVTOgKy9t/LTpHVBt2jRMGJ"
    "NWBtduI1P8dtBSMe3THLxhnGYcTDxaeA3+gW/xOuzB01PuZqcPN63lB83t424vPWaDA/let+F+x2C+IM4AKRRszjgoJvv8DzMvFt"
    "TH2jqd1HS8dkfbTsxMlkBh4Go4+FORM5MwMDmeUebB0DqbwaPMEVOyAq7j5zeOh2pONyFx5qZp69KoqamflN/ieCmJdv3WwPEVPf"
    "BmLqUekL/jkJZO3aorPoRx5M7ZpBnFVMVeEfe8/OboMPBRvdH47nM0Nd4e4wuoIgOvTikNmABVgUqXzAucvDDzcnqH5QClglzsyP"
    "BY9eZJ+sQcaCRqGXP/O2Bz9Erflxd4BGkPPO0AmgkTPkLQIaLJFVBOsPgztrJw/fQojjZFOL/JnbCu4P8zwP+rMvNoHHKgbGTlJH"
    "DzaWuUf5f+xU9fUYUPic+QIo7U3vQUe/B2/MPOnMbi+ddEIA2ITaB4H1ZOENWI1K0yrTkN1DD5kbJL0R6sx97axAMKAcdYx99VvR"
    "VQUBJwmrDByMbn44WMGtdDoHhai5QQfYppbv8a4P17n9CFCD0WGKNY/JyczNe3UnO3KQvdcdjI7jk+z0IZRfAvioPxgjIJ3oldvc"
    "Br8WAnoI+kDhEVxm6UUw9jK32pVHxN1UEbDqXHhSJ/JAGT9ReVBpBGCgyqxV3YayD6ZVt1HCkZncww8xcWMQRMSS8Z2VoGcn8yh6"
    "kfRTYkIaTHHQv1HyQWYoHSyvUpocuYedqm5MDr016jA/GH6QHXxpmAfYtirDuBFprGkg+/6xge1cv5I0d0DftTVf+d4/INU3wgPK"
    "PFwIZo9oGng1Guw9kNHuZ3qWAJfc4Usq4YJiFC/mj30ajjuo1DvvIPw3IpAwHwFQ8iL1f3k0xlovQwu6fC0kYmdZwcnSOPnKCOSm"
    "10AdYyoQioCvMhtvDZVaepHbOIL8DGkITMGSKIFB9NAEGsILn2f3j71D0w+owYFezC8CbU8eHOtv3pvpGXxLdJql8E8oLr3QdeBw"
    "eD5vd6AmGAOIHJHaJMITcgad8JBOZiERDwbDXUHPi2D/qdDgTqALfwYOg1eiwGyOcrOQmoXHgW6qyJt53AcJTA+ppQnyxJeVRngJ"
    "74jMSN4f4z0ydvQ6yaM+Dsu0+iQmB9tUgeTD7grReDANMOv+nUUpkx4HxQYUKEP2DfFgFZqyIV2+SuCE4NZobmlQeYGclWwnelbr"
    "PoWkRrU1erL/9D93YEb7sJvGmNCv8HH2wfPc0N3/3AmWcbsP9bNgbBykMDf+Fk8/mMbu8LHjOhiYVGlr1JCnnRUwCLmcaWX4PPDV"
    "e7mxw+zEgd5AnjNusvBZd8Kt16/cbL3iixBAXRWbgqF9j1juLwTvuyPWMICkf0xhw0VsHVK4k+kz6T6uaXlVFnEn2FzIPp/SY77W"
    "fOVKU0vllx1tt5p8rmQnJYWOnrQbIHrY+PsCf2f6d3we9HyKsi1Q/mCciPdwAT9USztHQDKhePrUVBZL2YNC6s4iETINTFYFt/Pk"
    "xUMC7XDEUtC948y99ejpiuzmX0Aone9H9UlftuPQ73tx3TLju1DUVCIArXbSpbAOUaYe7wnd7QPpxWMCxhmoNRbFfDjly8jK3wwM"
    "LUhExCX+v+8EIpAgCWITNSzXo7AweVdkUu6eZiALG7V7Bnuvg75OKzkJ5vzq66/+RbQjubIh/lDOgv4+NxwiT657Nxh9QqwdG86m"
    "h3ilRFZSnZi8fWmSD0Bcoo1iHPxQ57lQaYykfxUjKTf8fWNzi7lKo/1kw0NkcbgaNOkMDBghTJA8OOqkwUUMtJmRNYATv7E9vjs5"
    "b5ewqq+TBBk2NPQOf1xtvFH5ZepCZVsTzJ+A8DdVQAGYQA5HKNpDbtmnCRVnhcHFkBoKsTLgXKZ/HEKOE0g/7O4r3yZaqqD9ZpEC"
    "5fymWcC3F75JYRK8qqvGNzREDDzOvpl0W8SOoecaA/fdJ1a2HNRt/a3zKX4ws+K7s9iGM9SaGezAPO3NPQcFkuad19mD0aDTMHwd"
    "mJvZHaYFSz6BPJNZf4JlYofE3mNIIM8tHKu//dZnHd6JOfZBgxQA8HpRDDXh1nQyioRPu/gAbCO4rHNiaRN5jwLG2x18qLI+9bL9"
    "p3he7vqmbj/YWYQEGj9Vn3UVYBly3Elfq2y/1XL1eusPTW382scCfyNuC0B5cD/cMsAFDNxtCmYrgAy/DRwt51eu5HggN9s/rnRd"
    "N6sQp6x7NAV9yIl94VFWm6M8gLL5jHDyvC14myZXUXN1qiA9jEuffTGtVOXDXk8+4FWYpQtjPS2uEwVhSg7U+iW8I+WVg/9lZEVl"
    "YQjjweA+1zC3lnm1DDID3iMYSgGD1xybUISFHnv3OJMelqlnOHX/CxBP2N4Ul4KHs/EDzAy8zR0d+acHGw72QzZgBRp3kom8IyPK"
    "0UMzRFEFZl2AzEN6t/TMWA8V0FfLBiIwE2HFByuwSam5Sm8W7MTB+JJ3FjUyif+4IqMhTK8X9eKqaAxwBfcOoa/g1Iy4FV7N+JS4"
    "ZFAtYN4aA2YsGKya2lBRBnIjwE+ajk82gDf9SqB8chAc3idFeL2IZyxQazBfVckl0z8036lXy9zJTVE+sB5cvO43WFJIM2DajVIi"
    "YrNQH94NIOPWK3Ofe2EVf6lWQ7uY2gt/rbx6vfF7in+NzVcuwrf1Y+XtgitUgGA0zK6QC8miACTYe6NMlWrD8dsPBwfBwGJ2ozMz"
    "sBLbiPEE9o/jjlEodceZt57vW7EaXoGQJMQ4kZXUQoBAFXuyBdtrsDyskDnpGnYLIwoImsP/ASeRyt1GDDbmRTui5UTVeZwoM/CS"
    "pHB5W9mNe0ORBKD2r5qPDwVZEg5FHAV2VXNpWE4hAgU92yGW15gDsVNhbNVdMnPzBOHy85OHb6Ca+TfLERd1EpNezKX1SU4Luwzu"
    "1Nw8HFFYJt8Nz0HpTkvz5T8pVCIwN1YAcSObmY6naf/Vqza3YOQYyC77E0bgou9iRt0aCiWP6dvhLMBr+C/hXXn1kltCPlWxr7mr"
    "SaTzD3y0P/aJobNzC2bho7T/8kWQ3ZfjhhZUx4YPEQuSWt/z7N13oHa4+WZjoyuRCy/b462DxzxNJsnLMPqCN8cSkUqB7Y2mjqbW"
    "tjhJHyeVxm+hIzOGdsrtJUeyyOpELsqrE1TTVcwD0zFa7utF3Ha1lESQG7oPiDDUHzKnuf6IdeptFwX2Q2PqhSxPldx/+NuiF8LI"
    "7x4pMoKYotzwVu6VITkxPmQJQy8h5d0DRgbIlvQ2UDDdXs2/DT3rRrOaeleApVqXR3ZkCyAyrHl5NfP0DqVDe4FUNjeWglAk1RvQ"
    "3tryfT7bDU1QiuVAtPvvKWuQ6VMYhdFNeC5vBZQI/Valj8hx8MCeTmbWhyIHIX+SJa8P+TD35KOORnhEvs07htzhAX7sbGYhXCYO"
    "5nDUEm2RKMHXrOToJB5nviXOClHAkURE2BqdBzOA4xFTYRl9OmmoYHoTCxYQdMKyYvgnWEdaDHQ7CyRT99+HfD+Py+k5ETHcVTES"
    "rW44SoXwggoEgBQORPXN8LI+3Ja4mGk+IBgBLyIWDb+UXiloKLn+HSitqqQ4puZJPTKifzKhPiz6rzucVB5x0ncp9QiVB9XRoA+f"
    "YoYrUzoUXZDqOU5c9CQdMzbE3yJjq3YWI3x6x1SI1+cNCoOhHz9UB6AH8dCxk6/5Pe3Kzi9ycWNT/mmKWvYeV/FkKiIEEd5Tzzzm"
    "4tRVUZx9AEcMEUWkSQ1CQeASaCFVayjxkGpks7xtGwUogdpr+bCQAZWIQoQXCqoInyfi+7MpBcEwRNGefrgygYMx9TBfMcRJg0nQ"
    "3y9z6m8wxtzmCl8Rk1eU6LQ1Ed9Fg2tvutzW1GFtNpZqeH7liKcIn8+vFNCZ/Bes2GF4Zmb9uVuvCjSGPTW23FKNwx6WiHOk8r1R"
    "amU/ya3eDTZm8J/qL5K+WhdFHjIzeSMmr+lZZdd3qMPkibRGesKNtzDUo9MTIakcPIY8A3E7O9abnXjlMC0GCH8TFVinRg7N8JCn"
    "VmgRAAL1vII5KHc8e7JMxylD5oa6sstHH/aXgwE6TsID7x4ymkDvgpG80whPWSExPXgJ7m80ZoleCBHL4JWsIXgwELLzqPqjD4CG"
    "wV4LaODS+cMZqkE2P+2+olHEo4K8ytHpwtv6cjxfaTDWB5ge9noYm3J84MR8COaCFZ7JMkaJ7bnQgj5yoCQs1MSnNuxFHASIjagE"
    "qQhCaT/lLn2ejikYH550ZhZWQrZzoYh1tAjl1aGojiAOspAw7Aw1TuU2r8xDAe5TIzr91wgoFDbkTAEhNEJa7DiC6vgSlrQVsW8O"
    "b1mzVIwkiSnC2h6EDT91FnHVlDySSaIM+P/zr3/x9aXfffWvv/7VVxK+iIH++ufb4WUWzQO/zZk6FuEJYJXCwV2AkSeeA9dXEc7h"
    "8LXyghnf103z9SpIqyqw6iuOF4i22gmO4HQvmDYoBx8R67A9ghqYAxy2n2hUBNCAFNoqzaQl//yLX34tpsirl4wx0lIlseKp2dB5"
    "igdUAqFLZm1QBWIXdOYsf3jHxNDMbyIMS+UQ3+hHIidKSOMljRJlpJyaQ61HwaCSvfUqBRsD4eCxb5dUI1kwC5Ds+yY7I8bLM9DA"
    "yFSHB2Cx0QM0umJ4C50s2N54Fdu/mCRSXL0UUY4LqLHWHG2tVLRCA+fhQAk11yG1hmmwA+cGTA4mcZTO9RGMQKABXbuPCC61CKlV"
    "gMNSgXqphnVcAJqfBKTGKRxCMlxpIXi6l2hS8kljIY0aOzDRpWIXVarLtdhLrwxZjTKOauY632Tm3qpWkd1f0tVZrVOhmlCghgpa"
    "Ud3JwjX6BRdjdamg7yD77Jh+kSdvjAEFyppoVx+OadGNqTCqjKo31ldMFFWNHCEKiH5CAwAuUA8Gn+Xgs480cpshiJ3TivhK1wkr"
    "mD+2Jk6mdk+moJM+cfJu7CAI8NFFSutqURRu7Nkt53z7aMwSFMqalr4qDjOg09sp2YVQpszdhdxqOoretQ69U3oSvhmigM0gXLZq"
    "G6pu+yq8BFIKAEUEEpXwGXigaAUjjM/EK76wXQg5BcE4voruOktIeofvOI6au7vrsxr6E9y73b0SarOvHrroxlN241W6b8/sGzHE"
    "ftjDxR3xwnn0U2uly04sIGIveDQI1yWPfGMyswWT9JZG2xMijwZPZkZhFModT4BIKh3G5THg23rlhCsJVu41LvHiFJCKLqyy/o0d"
    "GMguTcHno35eXDZFHQl34yzEUbmxShmNabf7DT5XmJIQrE9IaPCAM9FFwXSx1pA/o6QXUKothDzRgbRFvIQM411/qI44vKAKdxwY"
    "Ih0oD9EICydk6Z8mXsqjcFxMIVAVe10nzbddGiPG2Es1+6izkgfSNaNhvkWYgiFfPzQjzPQH/8p4ymsBsMScyL7vOAIQyHf4mR+L"
    "QiZia7KgMEsoSGB8I8pRZ+x8EEQDzmTECJEhvOP3N4qdWskAkI/4Qml2mR9zPINRk43tTU6TQjyUaCdmqAuVNxvbO8hRQ3oi9gJl"
    "LiqnZDpXgC1qfFc5Re1bfiAfooZ8kYqmoY30yTzDhBwW/4Vzf/PNXysv/3j5OmZP0cUdnQ3wpkMLpF385uomUe80r/QiFQN4eDTq"
    "RO78geRZdEJ84I26/1R93cZy4Kaqscyu6rYk7RgoecxVZ6OZ/O5MZmxdw1I17o4ns/YcEWmY3MVfKRJQMACOqqsBLrO7M+oVwknG"
    "l1CnM2OHxCL4kaKgM9EZ4qKAI0yhQFx52qWQh37qlMpvZA+j/aW20XOcff8M8+gedOlqe1JzC1gxJHeXxlRstKInrMj8die3/wKa"
    "jwkQlaHyUcY7O7Dibd9Hp8iSGdgLD67kCZ3MM7oLm4IaKjYNUg5n+mPqhGfQMzlYOD8hNpXuBLDZQoM736A8NiBZJ8MBDBmWxKuC"
    "oh/GXgnuP3WUqTSGqdgr2GMCzyGVQC4Hy1FJUPHZh2fs8umf/jnqThVrcDXdbXIactSoRufqpcb2P/nsA+48ihLctfM4Rsws8gDD"
    "1OQ/+gyGpaJyJhqT8qOSYhMV0uUIVRe0pFFMPEpxewddo4yWUTp5h/F/ua1uFy+IoeivGBuh2W4x7rLIbS3hRxNj3JHFiVPy9oWi"
    "y4y44+fSocN7eFHtzQT/8Ya/HOeX940PRJPxJeraFt1iE0UISLVlBFUlluYgqIftnClq6FRCpVY9lZtMIOBqmiadLVpHmL5gXXTA"
    "G8SWqvuTSC8P+FoG7/a9N44uxGlebWkgMv8z/Uxd4wI169AaHdaj0etmriTmF1Iv9gOoZKHRu6BWT5oDrbQ7tCWZdyUdLY+gll6m"
    "sxPHgkb0+BSDzo7UiNyGZZjrAoaEUmKpQYy5SZJIIbEBPDF1OmJSB4daOdQML0dTSywP7zp85g3bO8YNc/5Y3i0NAYrcJHMm6p/X"
    "5C41hZlwCi/KqRRsdxaU4DkTn6/FUWGAmd/3H/jK7eQ8zIYhPQPKNbb4otCu0KmJd5nBo4JyUKKUHERwI7hLE4iXECP2WAVSmlxH"
    "DjLbU461ab6RQ1RaZtUWIpIBNmB5R68SZ2WaxWUizrw9hTkNm72vIa7Lap0z1pY7D4Oud8HWRHZpC9dC6J8VGpB7oJpL/CoSYbF0"
    "5Ii6sRHyB4qlEWzpQXOqelYI4HBe1y0mHfPohtKSEzJk/ZnL2dV7dPjOH9k59SxwFJEMA5XzNIqi4FlcDK07Cf8M3IuIu/GjhYtd"
    "e4J44KUzyxUi66VH9cVvBQwzsAkw4fJ4EZQSt+v4RWgVDWn0qcPnywsx17B6sYwl8yHw/JmG98enMgfqZnDygboc3edGGqdOMChE"
    "dsb32gtmDYI1eZPE5Uzv6MJtgHTPj2lwpxMgQdTw4z/mwiOpFU+88wU8PBY32ORfpZ6lkDgqYkXtoExnxSw0bnY6g2joo3mzyE++"
    "9WQSveap0E0TYlo4ljrV5jeJTiptoBSAXGM+gNiz4z7cI+WPUYvvjO+gNWIoyAFkeCGKOpoSPxM+BVBqpmBPv6Nqlso+CbM9JKyT"
    "Bpy5Bbow4Ypcn0CCjFE8o1pB/kbsmZloFWNgdseW96S/RyzsZOrAD3AwUqkAN/8INdlONQ1lHeYsoUX0igjhcTyCHcmYyOPNvx+b"
    "7uDzr7G5Z26nGpLre9cRTSoJveZElL5l3jC7DheEZjq4SrpHHc4Lcv0Ax29bHnpxqPlNzRCOoJf9ROc6RQ6u8nnM5daWK2LX15gv"
    "5n82XmxvQpj67XyUxPDufHTO/FMS3ec5tzi86m6ljxclhnKI4D/jT+SrRSqCGB8SQDg5l3m5pACI3NNCtnkEzoo9nfG1MUN8JMjS"
    "fehubrUFngOnB8TmjqYbNo+DzrqWy+4Mvm9twpCu0MU1ZAm3C7uPGjv8yEerM2mgpJOLDMglg8MPciUVmN3M9x2oWRySZkz3Eqoe"
    "Rt76gk1hjlV929da8xZlgm1EG6eRTbwZsiiGaYL+KqD1FcouNtcEiBJzLpAUICZAYkRNbIt4GTSkqggL8p0rRqawc7pzDBYfwbFM"
    "5EmrX8ri0uCx4qoRcMPgQCMduuhB95Vk0fAt8iMXvGGub8wgis0UDjQS42OIXCmPLTjk8rHIk0a8ohK+QVXTu91sBO/EUUjTbBAQ"
    "AYQYImux0vIEehy++CihriI1CKlUamkzJMJPCiuuNUaClPndijhA/5bGKvk0Wy7+OCKQDBeCE8aLWJLYqpckMpqAoM5Cz9RgsNaS"
    "z7jt1V340F8M29f8WOHgIncgNfZAnICeKnitrVu+wMU2N4bsKGJi9R3XoX3G+mciYrS17OqF4Ls7m9YCaCL/IHw7xugJ0XqT3kWi"
    "rYuK+rrM2Lr0WoiA3mcs9RCgxICOdfFUl1hRJQv/ozia1X3oqf1DSg5cmE2MimuCA16POPcjOOSvSPdsSA1O8Ggq+/RA0vZDbu77"
    "/eGpQTCU5muqaxQGUQIXOUfzY5mDxQIm2tqzgAPWP1Mw4A71Z8RjETqaIIIge5SIuEezgG/hiMgUEp2q5Jt2wo2pkIDQpv67X/6P"
    "X/0bSlRhVc1MDWyrSuHg/tTcIn80XSamNTVeUd8nCvCo45OFMbTEFMIO4bfkKz/YKkvfekG96k938YxeEpeteeUPwwPyZuH/EyhB"
    "tPwMX6DYTXUVrbMTC8H4oQbraBqIpspo6m/uCI7Y/YqG2v8mRSwcqVayGpqwRCp1WSRQ84OeHs3nZPZ/KEqG8QOQIFWupQXgaZcL"
    "HuCHYh/FhzJn6ChD6BwgjqSHw3swpXMedcohdmzTZM2iFkbbj3rHb19wR1BT4ghqznIEXkC3O42WW9ev+6Gl/hNnOJQa71DkTHgB"
    "pIaRhvFwV/1jWpTFGlXVCQv/KYAU89JKrvm2i5QQD6EhsoTu4QPnRkGxKEmsYHgxjllk5LMB8nJVfQjIy603bkQhWe9BEn54FPIx"
    "2cgWksbBZ2muj9fmKy9QN+9ZA1PUFTODi3tfqouxPsJTiCwzXmT7UEHPJQ1xyOCfWDOhQsd9NLGMvVS42GFo4bRuTY70YCszvKEC"
    "ivJQ0cXo+iSVB0UaGYSnRGOgvGHC2Dvy4WELfta30ZhplCybYlmvoojbUAJxG05DXA39LQxv52AsEBp9BuRt8JA3VWUoSjL1ZaJG"
    "IeCBPqTDCNw4WiXwEGh9tIGSQSdzcyZy6PAVfENSguyuxVCl4uojK+ZpKRbPy1WK8T4mOZ8RzROpxlJ4nohQ7e1VeKpiiG4CYIqR"
    "iggymwEsMmvAS8UvL9ZVIBALiIVCZTSvTA+H60d8gi1HyGJrUnyQ5Z7iYyn+FfpGqi+tu4JbmSkYydc19F6f0bojpDdjVOhL4Ggi"
    "9V0JJE2cyuHisArjh87N5xI+o6u2fK76y6oktmAgSqfK8rASVzI6gR8jrx8cV9TaAnkJlLfr71XaK3FBBJOaJrwoIGMuy5NlDQS9"
    "FAkNsnKpZShSd0ZErCmJhzWnEVyrtEayQgtzr8gjZyG1TjWjaU5VQ1hEpKwGC6PJdaUGZ8VH71UTy60Rxbs9eEmzzcn9bT5sFk6j"
    "fVO20p8VhYQOHzjHxXmIaaKuFJ7WnYankXpNhWFYqKRTSXyt8wUzKwMo4YRfTYQBKFWaFwwgdPcg6EvrYiIyjZg5dpjre8OKiCN3"
    "goV9BDScTN+FRE1Amoo5Q1phgTSZBWrEZZzWBH1m+e+PqCaiCGnFL6Nx8LGVvkzncxeHiq9wAnowmhpwdiksUYqZJRrODv8LUam4"
    "KHEtCfqGuPhFBpb8MlEVHgHC0AvLYyygCgqw2RtsdTmZN1a7iykTO69Plu5mu9etEEe9S9QKjfjUGn9ikR1kdkSvGM1sXIxa1s9I"
    "KJJVpQhFssoDrp9c6oDrlW91VNh+5mO491xII/wRQSO8ANeZMKDOMixE1p08O4hE1m0v5LafhFn2Y8g12SrI7GKhd8LYgPCZNwj0"
    "GaeCAOn3EcTjHZSkyW0sarb9h/1++PX1FhRDzWSiBGomE6ehZh70fhJKunlUZVNqIHijqhIvoZdUDDFWizpmJ97kNne0npIVnigb"
    "QchfWHEWd/On3Gt9zz0r9YhdLcqzX+hkKc02mTor1C6cgbSeEYJ2zjAqxgBRsyf8XCSKl5LQyOxz8BUAYUSqHOzPKmBsaLpNe6bL"
    "mvH1VpMiM5I0SbXpQYdQWZbxSRK6FIw+ZClMydKIarGikJ1Lhf2PqqqqUtTzNFiHpbxM1oeIV1oG9zRmJYbDqDkgUUVzANNEhrfJ"
    "hnY2VVANDQCxXfkbSZQkVT6lEuobE2l8cwU+vN58lQwhs4UQs4kYWZLXQRn8V7JvDrIHCz5d592QtzNv78OGUVFd90X9f+NR7g/h"
    "82BoMrv0Br70zLvlYHrNbTCyo2SpHaVO25EpiIWDab0l3C3YehgsdKPscZH9mIJZjxeDvt5QkO0aDXY2Kqq+qIIUKxWtwL9gMquo"
    "F0EYEr4bNr78Ute4+jTMsiJmzBTEM9H/nI5d7tWKFISg0R31iujrFRcT9V9U/+1ut4bjBD1duY3d3FB3ANG6t+cUTKsudS41p52L"
    "L3CE9aMEZ4ocjHuq4hco5Pt/VajsxmFNdhSJkD4EwQAcCjuqqK2vkLIeI/HVl5T9a09bveqRfAmUlKfxpBOqT5GFG6UTCgU1JL0c"
    "6ReZ0VExoyDvaCP35E52fy++xNoSmHOqQA0jIbxdop3cvHn9RyL+/n2ElZ+FHsmDUB0kbm+GVZheb1TUgHgcHFKiQCDk6DusIHs4"
    "dgqK1J3VoFUYyGcjqxFQx0hpfcMppDSy3JL2t4Z/OO1M1X6ROiftLKkUVJ2KQ2ehncWw6BQ6mojS0bpUATpaArUSJUXyROK0wzoH"
    "YY0cmCOmkPoLEdP6EsQ0sv6SfDqR/P8J+aypK0w+E8myrFKnks+ipElJaUP16aS0FPKkSgK/+lSZ4nTaGiFKBelpdSJKT5OGnkYW"
    "Wn1mC9WnoqB19eegoIlSrqBE7WlocUYKWlTSORc1TdZ8UXc6NS2FRbUlD6fuc0umNVGKWps8RTJNlOTOifpPTkIbGgqS0NRZSWj9"
    "R7N4FaegxXDtJ1HT6tqQmpZCrIazm5s+lnBanzqfcHqKSSzxWUgofGMRElpbgISWNj4lP7NqX1v301X7ZElFOJn6h8unCRQuOZ98"
    "mizJiJPVn5uENkRJaE3iFBKarC7HbPRRlfv6+oLEtO6nKffJmrLMSJ9JOk3VFJZOk7VlmYx+Cv2sS56TftaVYxb66Mo92aVPSROn"
    "K/fJ+rLsPx9BNK2pPYdommwoy+DzyYlnQ+qL2vMRz1RVWfahT6jbV0fJaHXV+XT7VKIss1HZcmldXUFSmjyjXJpKlmVG+ky0M1ld"
    "mHamUmWZjMrV7GsTZWn2qeryzEIfRSxtqI8S05oCYmmqpjz7z0cgn9U15yCfqdqyDD6fVbOvT36RKEuzT9WVaR/6yGJpfZSeJhtO"
    "EUtT9eWZjcqmoLW1BSlo7VkpaENZZqR/gGafSJ1Js6+uKs9k9FMk05qq80mm1YnyzEIfhYTW10VJaEMBElqdLMv+85E1+1T1T9fs"
    "q1PlGXw+uXBal/ii5nzCaXV1mfahj0xCU1ESmqg/hYRW15RlNvqoqn1NTUFqmvhpqn11bXlmpM8knzYkC8un1XXlmYx+CglNNZyT"
    "hNaXYxb66Mp9XW2UmFafrtxXN5Rl//kI0mkydQ7ptKaqLIPPJ6eftVVfNJyPftYkyrIPfULlvi5KSRvqzqfc1yTLMhuVLZpWVxck"
    "pjVnFE1rUmWZkT4T8axPFCaeNdXlmIzK1e2T9WXp9jU1ZZmFPopcWlsTJaX1BeTSmtqy7D8fgXYmkuehnXVlGXw+q2Zf3fBFdVma"
    "fU19mfahjyyWJmPRpLWniKU1DeWZjcomn6lUQfLZcEbyWVtVlhnpH6DZ11WdSbOvTZRnMvpJAaV15xNLa5PlmYU+CgWtqY5S0FQB"
    "ClqbKsv+85E1+4bET9fsa6vLM/h8+pjS+vPG49fWlGkf+rQB+XU1p5DQ2tqyzEYfVbOHIeEjRuTX1pVnRvpcMaUNhYXT2vryTEY/"
    "hYQ21J6ThDaUZRb66Kp9dercQfl1VeXZfz5GVH7VOeTTukR5Bp9PTkKTdecNy69LlmUf+nxR+bXV51Pu61JlmY3Klk4TibKC8uuq"
    "yzIjfSb6WV0kpamuphyTUbnKfX1NWcp9XW1ZZqGPE06aPD0iv66uvLyx8oln7XlSmurqyzH4fFbdPlFbZjx+XUN59qFPG45fkzpF"
    "Lq2vKstsVH40flVZ0fj1ibLMSP8AzT5VdybNvj5ZnsnoJ0WTVp9PLK1PlWcW+ijkM5k4PRq/vrq8vLGPq9nX1P90zb6+pjyDz6cP"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
