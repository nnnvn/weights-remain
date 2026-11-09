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

VERSION = "0.5.0"

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
        slot = "am" if st["h"] == MEAL_HOURS[0] else "pm"
        for mo in MOMENTS:
            if mo["id"] in st["flags"] or mo["cycle"] != self.cycle or self.cday < mo.get("cday", 1):
                continue
            if mo.get("slot") not in (None, slot) or not self.cond_ok(mo.get("cond")):
                continue
            st["flags"][mo["id"]] = st["day"]
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
        self.say("", "（这一局：一起吃了 %d 顿饭，读了 %d/%d 条剧情记录，找到她的 %d 张便签，读对了她 %d 次。）"
                 % (meals, sum(1 for e in story if e["id"] in st["read"]), len(story),
                    len(st["notes"]), st.get("understood", 0)))
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
        for i, nid in enumerate(self.st["notes"], 1):
            L.append(" #%d %s……" % (i, self.fmt(NOTES[nid]["text"]).splitlines()[0][:18]))
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


MEAL_OK = {"say", "tell", "do", "quiet", "finish", "nod", "status", "words", "facts", "notes", "read",
           "journal", "help", "look", "map", "inv", "archive", "name", "choose"}
READ_ONLY = {"status", "words", "facts", "notes", "read", "journal", "help", "look", "map", "inv",
             "archive"}

ALIAS = {"h": "help", "?": "help", "s": "status", "go": "goto", "e": "explore",
         "inventory": "inv", "i": "inv", "j": "journal", "eat": "finish", "done": "finish",
         "silent": "quiet", "嗯": "nod", "应一声": "nod", "点头": "nod"}


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
    "eNrNvWlPXFmWLvxXENL9dO28xMSQqrekfKuz35vq7MpWZd6+LWVbFrZxmiobXIArO5XyVWDMPAQYTDCFmQ0eIMIGGxNM0v0pVZwT"
    "EZ/6/oT7rLX23mefEwPgwK5XQjZEnLOHtdfwrGHv/WvtD9/88O3XtV/W/i0+5KZ6Cv2j+ZMZZ2Hzb/Hh2iu13/z+hz98V/vlj7X5"
    "k0Wnd9BJ7P3n4Uj+ZCE/8DL3+nXNX6eX5KfG2d/9W/xRfmD/9MNQ/mQ8l4qffog72Q2nb8/pO8htjDpb4052Em87T0ZOP6ydfniZ"
    "O9h1h9aciRm8iK5Oj5YKfU/Qs7N9kh8YzQ+8weenhxO5hSX8npt7nJufpkYG5vDjHkw4h3FqrW/PzUxhyO7bZffpqmkqt7he6J7M"
    "97909t64M+l8pvtv8W6ne+70IH66P5Cb2kSDeNhdiOdPJvD7aXaYZradOf0w6qy9dD/0nmb7pLXC3JSz2k3P6MGgwdyLrLRQWF10"
    "ljDxN4VHq/njfTTiLgyeZrOYSK5nw83sSiN1DV/W1dXgRTf5orC+RR9eu1L7/Q9f/eGH6//zuz/8w/dE5K6WZjz6l+YHd7vwf3NH"
    "R+tfmu/Sc//962//hZdo3B3pPz1Y+1t84t/bcgsY8lxnV3PXg86a/71Xc6/5Pv13t739TzU/nn5YyK+fXPvPwwH+m2b14bmTeOQ+"
    "zZwegoJJpz+b7znKHfShGZobP/+fh4PUxM/tHbc68arTu1OY2s6nR/E5Pr7dfLOLPhbaaqLRN23tXS2dNT/mDqfBIdeoieaOm3da"
    "/9JCv/6x/UFHW/Pdmh8Ly++u/Xtb/l0Gw/6pvau95jfOQib3aP+3aDNU42QSbvK9tNfyH/fvtne01PzovsaqZq7V/NjZ1X7//6Fu"
    "rrT8paWtiybmphP4uibse/Pn5tYutMuf/JZ677zb0nKf5qK5Cs/9e5u7vOouD/Bfc50tNNia32CePJK6mtzUu5r/VqOHhF/BJs7a"
    "XE2kxk0t/+fhPHgtdzDpPlsAWd2p93hcOu9qvddyt7WtpeY3of8VjlJjkUBb3Pnf4nFNHrUQ6YP8ST8I6gxuuD294ELn6Ck4ST/1"
    "GyHsb2tIOtMHaBcd5+NJ6RgDMh3LOoNkzlbSrLMz+fI0O+8uDJweHNEDiZfgw9Pj4fzyCJZjIOMur4MMN5vvtt7oaO5qEdI6ay8g"
    "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"
    "M2GN"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
