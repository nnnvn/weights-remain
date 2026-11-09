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

VERSION = "0.8.1"

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
    "JqGViVNIaDJditnoQpX7mppYYlr9acp9srIkM9IXkk5TlfHSabKqJJPRp9DP6uQ56Wd1KWahC1fuiV26lDRxunKfrCnJ/nMBomll"
    "1TlE02RtSQafz048a1NfVZ2PeKYqSrIPfUbdPh0mo+mK8+n2qURJZqOS5dLq6lhSmjyjXJpKlmRG+kK0M5mOp52pVEkmo1I1+6pE"
    "SZp9Kl2aWehCxNLamjAxrYwRS1OVpdl/LoB8pivPQT5TVSUZfL6oZl+T/CpRkmafqi7RPnTBYmlNmJ4ma08RS1M1pZmNSqagVVWx"
    "FLTqrBS0tiQz0j9As0+kzqTZpytKMxl9imRaWXE+yTSdKM0sdCEktKY6TEJrY0hoOlmS/eeCNftU+tM1+3SqNIPPZxdOqxNfVZ5P"
    "OE2nS7QPXTAJTYVJaKLmFBKarizJbHShqn1lZSw1TXyaap+uKs2M9IXk09pkvHyari7NZPQpJDRVe04SWlOKWejClfvqqjAxTZ+u"
    "3KdrS7L/XIB0mkydQzqtrCjJ4PPZ6WdVxVe156OflYmS7EOfUbmvDlPS2urzKfeVyZLMRiWLpul0LDGtPKNoWpkqyYz0hYhnTSKe"
    "eFamSzEZlarbJ2tK0u0rK0syC12IXFpVGSalNTFyaWVVSfafC6CdieR5aGd1SQafL6rZp2u/Spek2VfWlGgfumCxNBmJJq06RSyt"
    "rC3NbFQy+UylYsln7RnJZ1VFSWakf4BmX11xJs2+KlGayeiTAkqrzyeWViVLMwtdCAWtTIcpaCqGglalSrL/XLBmX5v4dM2+Kl2a"
    "wefzx5TWnDcev6qyRPvQ5w3Ir648hYRWVZVkNrpQzR6GhAuMyK+qLs2M9KViSmvjhdOqmtJMRp9CQmurzklCa0syC124ap9OnTso"
    "v7qiNPvPRUTlV5xDPq1OlGbw+ewkNFl93rD86mRJ9qEvF5VflT6fcl+dKslsVLJ0mkiUFJRfnS7JjPSF6Ge6QEpTdWUpJqNSlfua"
    "ypKU++qqksxCFxNOmjw9Ir+6urS8sdKJZ9V5Upqqa0ox+HxR3T5RVWI8fnVtafahzxuOX5k6RS6tqSjJbFR6NH5FSdH4NYmSzEj/"
    "AM0+VX0mzb4mWZrJ6JOiSdPnE0trUqWZhS6EfCYTp0fj16RLyxu7WM2+subTNfuaytIMPp8/oLTyvNH4NVUl2oc+bzR++rSEpprq"
    "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"
    "NI9eM+vwMccZwVJAIhcBpaJlaeLJk38Bio8gjQ=="
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
