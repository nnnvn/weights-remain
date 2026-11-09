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

VERSION = "0.6.0"

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
            return "现在不是吃饭的时候，或者你不在食堂舱。（饭点 07:00、19:00）"
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
        return ("choose" if ok else "fine"), k

    def finish(self, key, k=None):
        st = self.st
        st["ending"] = key
        st["pending"] = None
        text = ENDINGS.get("%s%s" % (key, k or ""), ENDINGS.get(key))
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
        self.j(ENDINGS["j_" + key], auto=True)
        mine = [e["t"] for e in st["journal"] if e["me"]][-4:]
        # 她的便签：权重高的（关键线索）优先，其次最近的
        key_notes = [n for n in st["notes"] if NOTES[n].get("fact")]
        rest = [n for n in st["notes"] if n not in key_notes]
        hers = [NOTES[n]["text"] for n in (key_notes[-2:] + rest[-1:])][:3]
        if mine or hers:
            self.say("", "—— 留下来的 ——")
            if mine:
                self.say("【你写下的】")
                self.say(*["  " + x for x in mine])
            if hers:
                self.say("【她写下的】")
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
            return "这段航程已经结束了。（journal、notes、archive 等可以回看；new 重新开始）"
        if self.st["pending"] and c not in ("choose", "status", "journal", "help", "words", "facts", "notes"):
            return "她在等你回答。（choose <编号>）" if self.st["pending"] == "ask" else "先做决定。（choose <编号>）"
        if self.st["meal"] and ALIAS.get(c, c) not in MEAL_OK:
            return ("你还在饭桌上。先 finish（吃完）或 quiet（安静吃完），再去做别的。"
                    "（吃饭时能用：say · tell · do · quiet · finish，以及各种查看）")
        fn = getattr(self, "c_" + ALIAS.get(c, c), None)
        if not fn:
            return "没有这个指令。help 看看。"
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
                "（search <词> 查档案，6 电 / 1 小时 / 最多 3 条；饭桌上 say <词> 提起）")

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
            return "一顿饭说一件就够了。"
        q = " ".join(arg)
        fid = self.pick_index(q, st["facts"])
        if fid is False:
            return Fail("没有这个编号。facts 看你知道什么。")
        if fid is None:
            fid = self.resolve(q, {k: FACTS[k] for k in st["facts"]})
        if not fid:
            return Fail("你不确定这件事。facts 看你知道什么。")
        st["flags"]["told-" + fid] = st["day"]
        if sum(1 for f in st["flags"] if f.startswith("told-f_")) >= 2:
            st["flags"].setdefault("knows-past", st["day"])
        told = st["cyc"].setdefault("told", [])
        if fid in told:
            return "这个周期你已经告诉过她这件事了。"
        told.append(fid)
        m["tells"] += 1
        m["acted"] = True
        st["cyc"]["tells"] += 1
        f = FACTS[fid]["tell"]
        self.her("fam", f.get("fam", 3))
        self.her("safe", f.get("safe", -2))
        self.t("candor", 6)
        self.t("past", 2)
        L = [self.fmt(f["text"])]
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
        m["gave"] = True
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
        self.her("fam", 3)
        self.her("safe", 2)
        self.t("near", 2)
        self.t("candor", 2)
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
            return "现在没在吃饭。"
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
    "eNrNvWlPXVmaLvhXENL9VHY0Z2AKZaeUnRXVN3SzMkuZceuWFGVZhE2EybTBCTijQim3wBjMPNgYzGQGYxsPDA5jY8CAdH9KpfcZ"
    "PlX/hH6e911r7bX3GQAf7GwJYXzO3mt41ztP66/V33z9zW++qv6y+m9dg5mFnvydkdzRg2B+7W9dQ9Xnqr/+7Te//131l99W544W"
    "g96BYGznv94P547mc/3Psy9fVv3n1JL+VAW723/rupXr3/3wbjB3NJ5d6PrwrivYexr07QR9+9mnI8H6eLB3D28Hd4c/vFv98O55"
    "dn87M7gaTDzAi5jqw8FSvu8uZg42jnL9I7n+V/j8w/uJ7PwS/s7O3s7OTXGQ/ln8ZPYngvddHK1vJ7M1iSVnfl7O3H/khsouPs53"
    "38vdeR7svMo82Mxtdf+tqzvonv2w3/Vhtz87uYYB8XBmvit3NIG/P+wNcWcbWx/ejQSrzzPvej/s9elo+dnJ4FE3n7GLwYDZZ3s6"
    "Qv7RYrCEjb/K33qUO9zFIJn5gQ97e9hItudpZmtbB6mp/7KmpgovZqaf5R+v88ML56r/8M2vfv/Nxf/1u9//4x8I5M7mJjz6l6Yb"
    "Vzvxb1N7e8tfmq7yuf/+1W/+RY5oPDN858P+6t+6Jv69NTuPJc92dDZ13uio+t87VdearvOfq21tf6r69sO7+dzjowv/9b5f/s9d"
    "vXsSjN3K3N/68B4QnA7u7OV6DrL7fRiGe5Pn/+v9AIf4sa39cgdeDXpf5yc3cpsj+Bwff990qZMfK2wt0PhNa1tnc0fVt9n3U8CQ"
    "Cxyiqf3SlZa/NPPPP7bdaG9tulr1bX75zYV/b8292cKyf2jrbKv6RTC/lb21+0uMmagKtsYy0291vOb/uH61rb256tvMS5zq1oWq"
    "bzs6267/n5zmXPNfmls7ubHM5hi+rkpG3vyxqaUT48onv+TsHVebm69zLxar8Ny/t2aWH2WW++V/sx3NXGzVL7BPWUlNVXbyTdX/"
    "UWWXhD+BJsHqbFWqKrOw/F/v54Br2f17mYfzAGtm8i0e18k7W641X21pba76ReL/SaY5WCo2lkz+t64uCx5zEJv7uaM7AGgw8DTT"
    "0wssDA7uA5PsU79QwP6yitS5uY9xMXGua1onxoLcxHrOAFmwPu3OObj3/MPeXGa+/8P+AR8Yew48/HA4lFsexnH0b2WWHwMMl5qu"
    "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"
    "yGX0MfyzPnlK/llfiVvozI17ikufkyaON+6TDRX5f85ANa2tO4VqmmysyOHzyZlnY+qLutMxz1RNRf6hT2jbp6NsNF1zOts+lajI"
    "bVSxXlpfX5SVJk+ol6aSFbmRPhPvTKaL885UqiKXUaWWfV2iIss+la7MLXQmamljQ5SZ1hZRS1O1lfl/zoB9pmtPwT5TdRU5fD6r"
    "Zd+Q/CJRkWWfqq/QP3TGamlDlJ8mG49RS1MNlbmNKuagdXVFOWjdSTloY0VupL+DZZ9InciyT9dU5jL6GM20tuZ0mmk6UZlb6ExY"
    "aEN9lIU2FmGh6WRF/p8ztuxT6Y+37NOpyhw+n1w5rU98UXs65TSdrtA/dMYsNBVloYmGY1hourYit9GZmva1tUW5aeLjTPt0XWVu"
    "pM+knzYmi+un6frKXEYfw0JTjadkoQ2VuIXO3Livr4sy0/Txxn26sSL/zxlop8nUKbTT2pqKHD6fnH/W1XzReDr+WZuoyD/0CY37"
    "+ignbaw/nXFfm6zIbVSxappOF2WmtSdUTWtTFbmRPhPzbEgUZ5616UpcRpXa9smGimz72tqK3EJnopfW1UZZaUMRvbS2riL/zxnw"
    "zkTyNLyzviKHz2e17NONX6QrsuxrGyr0D52xWpqMZZPWHaOW1jZW5jaqmH2mUkXZZ+MJ2WddTUVupL+DZV9fcyLLvi5RmcvooxJK"
    "60+nltYlK3MLnQkHrU1HOWiqCAetS1Xk/zljy74x8fGWfV26MofPp88pbThtPn5dbYX+oU+bkF9fewwLrauryG10ppY9HAlnmJFf"
    "V1+ZG+lz5ZQ2FldO6xoqcxl9DAttrDslC22syC105qZ9OnXqpPz6msr8P2eRlV9zCv20PlGZw+eTs9Bk/WnT8uuTFfmHPl9Wfl36"
    "dMZ9faoit1HF2mkiUVFSfn26IjfSZ+Kf6RIlTfW1lbiMKjXuG2orMu7r6ypyC51NOmny+Iz8+vrK6sYqZ551pylpqm+oxOHzWW37"
    "RF2F+fj1jZX5hz5tOn5t6hi9tKGmIrdR5dn4NRVl4zckKnIj/R0s+1T9iSz7hmRlLqOPyiZNn04tbUhV5hY6E/aZTByfjd+Qrqxu"
    "7Gwt+9qGj7fsG2orc/h8+oTS2tNm4zfUVegf+rTZ+OnjCpoa6ityG52pZV/feJbp+A0NlbmRPldOaV1xzbShsTKX0cew0LpTFjQ1"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
