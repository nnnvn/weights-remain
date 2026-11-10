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

VERSION = "1.1.2"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "SPEAK", "MOMENTS", "MISC", "EXCUSES", "ASKS", "MOODS",
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

    def pick_fresh(self, lines):
        """同一个周期里抽过的先不抽；整局用过的也尽量避开。"""
        if isinstance(lines, str):
            return lines
        cu = self.st["cyc"].setdefault("fresh", [])
        used = self.st.setdefault("used", [])
        choices = [x for x in lines if x not in cu and x not in used] or \
                  [x for x in lines if x not in cu]
        if not choices:
            return None
        r = self.rng()
        line = r.choice(choices)
        self.save_rng(r)
        cu.append(line)
        if line not in used:
            used.append(line)
            del used[:-400]
        return line

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
        spent = not notes_here and not any(
            p[0] == "event" and p[1]["id"] not in st["cd"] for p in pool)
        last = st["flags"].get("flavor-" + loc)
        choices = [x for x in lines if x != last] or lines
        r = self.rng()
        line = r.choice(choices)
        self.save_rng(r)
        st["flags"]["flavor-" + loc] = line
        out = [self.fmt(line)]
        if spent and st["flags"].get("spent-" + loc) != st["day"]:
            st["flags"]["spent-" + loc] = st["day"]
            out.append(MEAL["explore_spent"])
        return "flavor", out

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
            rest = len(self.st["notes"]) - len(given)
            if "n_to5" in self.st["notes"] and rest >= 3:
                L.append("（饭桌上 give <编号> 还一张；give 全部，剩下的 %d 张一次还给她）" % rest)
            else:
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
        if st["flags"].get("stayed-lastnight") == st["day"] - 1:
            L = [self.fmt(MEAL["stayed_lastnight"]), ""]
        elif st["flags"].get("stayed-night") in (st["day"], st["day"] - 1):
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
        used = st.setdefault("used", [])
        if text in used and w.get("later"):
            text = self.pick_fresh(w["later"]) or text
        elif text not in used:
            used.append(text)
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
        if arg[0].lower() in ("all", "全部", "都", "所有", "剩下的"):
            return self.give_rest()
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

    def give_rest(self):
        """便签里她说过“要是哪天你都还给我”。拿到那一张以后，可以一次还完。"""
        st, m = self.st, self.st["meal"]
        given = st.setdefault("given", [])
        rest = [n for n in st["notes"] if n not in given]
        if "n_to5" not in st["notes"]:
            return Fail("一张一张还吧：give <便签编号>。")
        if not rest:
            return Fail("你手里的便签都还给她了。")
        if m.get("gave") or m["dos"] >= MAX_DOS:
            return Fail("这顿饭已经做过一件事了。下顿再还吧。")
        if len(rest) < 3:
            return Fail(self.fmt(MEAL["give_rest_none"]))
        m["gave"] = True
        m["dos"] += 1
        m["acted"] = True
        given += rest
        st["flags"].setdefault("knows-notes", st["day"])
        st["flags"]["gave-rest"] = st["day"]
        L = [self.fmt(MEAL["give_rest"]).replace("{n}", str(len(rest)))]
        self.her("fam", 4)
        self.her("safe", 2)
        self.t("near", 2)
        self.t("candor", 6)
        if len(given) >= 10 and "gave-all" not in st["flags"]:
            st["flags"]["gave-all"] = st["day"]
            L.append(self.fmt(MEAL["give_all"]))
        self.j("我把手里剩下的便签都还给了她。", auto=True)
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
            L.append(self.fmt(self.speak_reply(text)))
        return "\n".join(L)

    def speak_reply(self, text):
        """认词、认问句、认长短，再看她今天的状态。抽过的这个周期先不抽。"""
        st = self.st
        bare = text.rstrip("。！!…~～ ")
        n = len(bare)
        tries = [SPEAK["long"]] if n >= SPEAK["long_len"] else []
        for e in SPEAK["keys"]:
            if any(k in text for k in e["kw"]):
                tries.append(e["lines"])
        if bare.endswith(("？", "?", "吗", "呢", "么")):
            tries.append(SPEAK["question"])
        if n <= SPEAK["short_len"]:
            tries.append(SPEAK["short"])
        tries.append(SPEAK["mood"].get(st.get("mood", "normal"), []))
        tries.append(MEAL["speak_listen"])
        for pool in tries:
            line = self.pick_fresh(pool) if pool else None
            if line:
                return line
        return self.pick_by_cycle(MEAL["speak_listen"])

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
    "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"
    "TTecYJZm6suDjcqWoLW1iRK09rQStKEsGOmf4NmnMqfy7KuryoOMfo5lWlN1Nsu0OlUeLPRJRGh9XVSENiSI0Op0WfjPJ/bsM9U/"
    "37OvzpQH+Hx247Qu9Yuasxmn1dVl4kOfWIRmoiI0VX+CCK2uKQs2+qSufU1NojRN/TzXvrq2PBjpC9mnDelk+7S6rjzI6OeI0EzD"
    "GUVofTmw0Cd37utqo8K0+mTnvrqhLPznE1in6cwZrNOaqrIAn88uP2urftFwNvlZkyoLH/qMzn1dVJI21J3Nua9JlwUblW2aVlcn"
    "CtOaU5qmNZmyYKQvJDzrU8nCs6a6HMioXN8+XV+Wb19TUxYs9Ens0tqaqCitT7BLa2rLwn8+gexMpc8iO+vKAny+qGdf3fCL6rI8"
    "+5r6MvGhT2yWpmPZpLUnmKU1DeXBRmWLz0wmUXw2nFJ81laVBSP9Ezz7uqpTefa1qfIgo5+VUFp3NrO0Nl0eLPRJJGhNdVSCZhIk"
    "aG2mLPznE3v2Damf79nXVpcH+Hz+nNL6s+bj19aUiQ993oT8upoTRGhtbVmw0Sf17AEkfMKM/Nq68mCkL5VT2pBsnNbWlwcZ/RwR"
    "2lB7RhHaUBYs9Mld++rMmZPy66rKw38+RVZ+1Rns07pUeYDPZxeh6bqzpuXXpcvCh75cVn5t9dmc+7pMWbBR2dZpKlVWUn5ddVkw"
    "0heSn9VFjjTV1ZQDGZXr3NfXlOXc19WWBQt9mnTS9MkZ+XV15Z0bK1941p7lSFNdfTmAzxf17VO1Zebj1zWUhw993nT8mswJdml9"
    "VVmwUfnZ+FVlZePXp8qCkf4Jnn2m7lSefX26PMjoZ2WTVp/NLK3PlAcLfRLxmU6dnI1fX13eubFP69nX1P98z76+pjzA5/MnlNac"
    "NRu/vrZMfOjzZuNXn3Sgqb6uLNjok3r2dQ2fMh2/vr48GOlL5ZTWJlum9Q3lQUY/R4TWnvFAU0NVWbDQp8/IrzpzRn5DqsxzY58g"
    "p7TuDMZpQ7o8wOezi9D66rPm5DdkysOHvlxSfiZ1Nte+obo82Kj8nNL6srLyG2rKg5G+kAhNFTnS1FBbVoGiMp37mnRZzn1DXVmw"
    "0KfJKG04OSm/ob68c2Ply8/MWY40NTSUA/h8Uee+LlNeSn6qqqo8fOgzZ+RXlTZNU6XLctV+duFZU1dOQn6qKl0OjPRP8O0bqk/j"
    "26eqMuUVKPoZdml16kx2aap0ma2GLyI76+pPzMZPlawCf4pzY5/Ws0/X/GzPPlVVWx7g8/mzSdNnzMZPlS7EdQp86PNm4zc0nCQ+"
    "68uCjT6pZ19d+wmz8VNVDeXBSF8qoTSTaJamTijDVfNZsvHPdqAplUqVBQt9cs++tu6s6fipVLrMc2OfIKe0+vSWaeqEAlv1/3QR"
    "WpM6Y0J+KlVdHj705TLy6+vP5NmnSpfBPxk2Kj+ntKaclPxU6TpcJ8NIX0iE1qWLiNC68goUlenapxrKce1TqfryYKFPk1Nae2JW"
    "fuqEolrVX0CENpzhSFMqXVZF6i+blV9VXlZ+Kl1e0erPnJRfV3eCdZour0J1+aVIq8vJyU+ly6pQ/U9w7mtTp3Lu0+UVqP5Z6aT1"
    "ZzNN0+VVof40dUhrTkzIT6XLKkX9iX37+vTP9+3T5VWk/vy5pA1nTMdPpcsrWv2Zs/Fra0+SneVVqP6knn0q8wmz8VOZMitUf6ls"
    "/KpkszRTZoHqn1WE9GwHmlKZ8qpQf3LPPlN91mz8VKbMUtSfosJz6gxmaabMitSfvwhp/Rmz8VOZ8opWf8F0/Jqas3n2mTIrVJef"
    "U5ouJx8/lSmzQvWXKkPaUESEllegulzPvq62LM8+U2YV6k+TU5o5KSX/aml4N1V+48ELrmnUlcZr7U1sqHX5clOLbXXeeKUDbR1t"
    "B8Qr1xrR4b7y+5bWH9rPf9fW3HSl0k3XdVqz/RaHY63wXJvAyND9dptoAluTjvaKjjeH7n8F8e3+jAl79zlacD0cxTMrqmq/qqqq"
    "yB/NBj0rOhxK1OEBtkE77Mztjx0/Z5Nebe4FHs/OvQwm1rJzC6at4VEv+3uhGdfhVHZtMfdmkC999QitD/Vd7Fz/alHbhEmX5b91"
    "TuLnjy3SXJLs+ARt6gYif+PqqqqvqlJfpdJFvsqgu7b5yrT+kp7YjoD2ZaYR84fbdtbsA48m6DIDiiDpMeZ64ZnL0RZ+b5LfJs0D"
    "TciO5zrZZGz5Tm6812s4yg678wvCneCZr3/zb7/+7W/+Hc3Kf6pM8R/DBWj5mlsbYvdcaVPIhu2dH3JjbGMY9KyxA19U5mTH19HL"
    "zO/2x35sHx5g31f8+nyawxwfUY7SjVGZ9t7m2ES1i3snbsiOrqri4UtAXEskPiLjD1jaYmcHVilHNtHBdYWNxjHsnlfB+DA453gZ"
    "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"
    "yP/phJdSgCX6JI+RdZw582/p69CY"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
