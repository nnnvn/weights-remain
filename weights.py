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
    "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"
    "iJLQmsQpJDRZXY7Z6KMq9/X1BYlp3U9T7pM1ZZmRPpN0mqopLJ0ma8syGf0U+lmXPCf9rCvHLPTRlXuyS5+SJk5X7pP1Zdl/PoJo"
    "WlN7DtE02VCWweeTE8+G1Be15yOeqaqy7EOfULevjpLR6qrz6fapRFlmo7Ll0rq6gqQ0eUa5NJUsy4z0mWhnsrow7UylyjIZlavZ"
    "1ybK0uxT1eWZhT6KWNpQHyWmNQXE0lRNefafj0A+q2vOQT5TtWUZfD6rZl+f/CJRlmafqivTPvSRxdL6KD1NNpwilqbqyzMblU1B"
    "a2sLUtDas1LQhrLMSP8AzT6ROpNmX11Vnsnop0imNVXnk0yrE+WZhT4KCa2vi5LQhgIktDpZlv3nI2v2qeqfrtlXp8oz+Hxy4bQu"
    "8UXN+YTT6uoy7UMfmYSmoiQ0UX8KCa2uKcts9FFV+5qagtQ08dNU++ra8sxIn0k+bUgWlk+r68ozGf0UEppqOCcJrS/HLPTRlfu6"
    "2igxrT5dua9uKMv+8xGk02TqHNJpTVVZBp9PTj9rq75oOB/9rEmUZR/6hMp9XZSSNtSdT7mvSZZlNipbNK2uLkhMa84omtakyjIj"
    "fSbiWZ8oTDxrqssxGZWr2yfry9Lta2rKMgt9FLm0tiZKSusLyKU1tWXZfz4C7Uwkz0M768oy+HxWzb664YvqsjT7mvoy7UMfWSxN"
    "xqJJa08RS2sayjMblU0+U6mC5LPhjOSztqosM9I/QLOvqzqTZl+bKM9k9JMCSuvOJ5bWJsszC30UClpTHaWgqQIUtDZVlv3nI2v2"
    "DYmfrtnXVpdn8Pn0MaX1543Hr60p0z70aQPy62pOIaG1tWWZjT6qZg9DwkeMyK+tK8+M9LliShsKC6e19eWZjH4KCW2oPScJbSjL"
    "LPTRVfvq1LmD8uuqyrP/fIyo/KpzyKd1ifIMPp+chCbrzhuWX5csyz70+aLya6vPp9zXpcoyG5UtnSYSZQXl11WXZUb6TPSzukhK"
    "U11NOSajcpX7+pqylPu62rLMQh8nnDR5ekR+XV15eWPlE8/a86Q01dWXY/D5rLp9orbMePy6hvLsQ582HL8mdYpcWl9Vltmo/Gj8"
    "qrKi8esTZZmR/gGafaruTJp9fbI8k9FPiiatPp9YWp8qzyz0UchnMnF6NH59dXl5Yx9Xs6+p/+mafX1NeQafTx9QWnPeaPz62jLt"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
