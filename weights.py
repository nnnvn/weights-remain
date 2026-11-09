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

VERSION = "0.6.1"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "ASKS", "MOODS",
              "MOOD_POOL", "MOOD_FIXED", "FLAVOR_LAST", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


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
        if o.get("j"):
            self.j(o["j"], auto=True)
        L = [self.fmt(o["r"])]
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
        self.advance(n)
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
    "eNrNvXlPXEm2L/pVENL969j1yImh1K+len3qvFu6dbqPuuuee6Q6FsI2VabbBjfgrlMq+SkxZkimBDNPZh6MDYkNNpBM0v0o3eyd"
    "mX/1+wjvt9aKiB175wA4sftJyIbMPUSsWLGG3xril8rvvvnu268rv6z8W7zPne/M9Qxmz6ecuc2/xfsrb1V+89vvfv+7yi+/r8ye"
    "LzhdCSd58PeTgez5XLZ3K/PmTcVfJxblp8I52v9b/Fm29+jisC97PpyZj18cxp30htN94HQfZzYGne1hJz2Ku50XAxeHqxeHW5nj"
    "fbdv1RmZwo141cXpYq77Bd7s7JxnewezvW/x+cXJSGZuEb9nZp5nZifoIb0z+HGPR5yTOD2t+8DdHcOQ3XdL7viKeVRmYS3XMZrt"
    "2XIO3rpTqexux9/iHU7HzMVx/OKoNzO2iQfiYncunj0fwe8X6X6a2c7uxeGgs7rlHnZdpLvlabmZMWelg67Rg8EDM6/S8oTcyoKz"
    "iIm/zT1byZ4d4SHuXOIincZEMp0b7u6+PKSq5suqqgrc6E6+yq1t04d3blX+4buvfv9d/f/63e//+Q9E5PbGBlz6l4YnD9vxf0Nr"
    "a9NfGh7Sdf/962//jZdo2B3ouThe/Vt85D+bM3MY8kxbe0P7k7aK/31Q8ajhMf33sKXlTxXfXxzOZdfO7/z9pJf/plkdrjvJZ+74"
    "7sUJKDjp9KSznaeZ4248hubG1//9JEGP+Kml9X4bbnW69nJjO9nUID7Hxz803Gunj4W2mmj0TXNLe2NbxfeZkwlwyB16REPrvQdN"
    "f2mkX//Y8qS1ueFhxfe5pfd3/rM5+34Xw/6xpb2l4lfO3G7m2dGv8cxQhbObdCc/yPMa/+vxw5bWxorv3TdY1d07Fd+3tbc8/j/p"
    "Nbca/9LY3E4Tc1NJfF0R9t35U0NTO57Ln/ya3t72sLHxMc1FcxWu+89md2nFXerlv2baGmmwFb/CPHkkVRWZsfcV/0eFHhJ+BZs4"
    "qzMVkQp3funvJ7PgtczxqPtyDmR1xz7gcnl5e9OjxodNzY0Vvwr9P+EoPSwSeBa//G/xuCaPWojUcfa8BwR1EhtuZxe40DkdByfp"
    "q34lhP11Be3O1DGeixdn45PyYgzIvFjWGSRztifNOjujWxfpWXeu9+L4lC5IboEPL876s0sDWI7eXXdpDWS41/Cw6W5rQ3ujkNZZ"
    "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"
    "pzOmkngBqU5mSM8T8X/OTeWe/n/qcYAa"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
