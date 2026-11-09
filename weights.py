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

VERSION = "0.4.0"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "ASKS", "MOODS",
              "MOOD_POOL", "MOOD_FIXED", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


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
READS_BEFORE_COLD = 6                       # 一个周期里读档超过这么多次，饭桌上会冷一点
LIFE_SUPPORT = 35                           # 每晚生命维持耗电
REACTOR = 40                                # 每天早上反应堆补的电
FAM_BASE, FAM_PER_CYCLE = 25, 11             # 熟悉感上限 = 25 + 11 × 周期
SEARCH_PWR, TIMELINE_PWR = 6, 3
EXPLORE_STA, MOVE_STA, SEARCH_STA = 12, 2, 3


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
            meal = ("%02d:00（现在去还来得及）" % grace[0] if grace else
                    "%02d:00" % nxt[0] if nxt else "明早07:00")
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
        return None

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
        self.her("safe", d)
        if d >= 3 and m and not m.get("read"):
            m["read"] = True
            st["understood"] = st.get("understood", 0) + 1
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
        """是不是一直在读她今天的样子：读对的次数够多，而且占她有状态的饭的大半。"""
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
                self.advance(2)
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
            self.say("✦ " + self.fmt(mo["text"]))
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
        elif st["fam"] >= self.fam_cap() and not m["cap0"]:
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
        if st["fam"] >= self.fam_cap() and st["fam"] < 100:
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
            return "船上没有这个地方，或者你还不知道它在哪。"
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
        r = self.rng()
        pool = []
        for nid, nv in NOTES.items():
            if nv["loc"] == loc and nid not in st["notes"] and self.cond_ok(nv.get("cond")):
                pool.append(("note", nid, nv.get("w", 10)))
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
            return "检索“%s”：没有结果。（没有消耗电力）" % " ".join(arg)
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
            return "timeline <月份 1~24>。这是你上一段航程的工作日志。"
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
            if st["fam"] >= need and self.cycle >= (entry[2] if len(entry) > 2 else 1):
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
            return "没有这个编号。facts 看你知道什么。"
        if fid is None:
            fid = self.resolve(q, {k: FACTS[k] for k in st["facts"]})
        if not fid:
            return "你不确定这件事。facts 看你知道什么。"
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
            return "这顿饭已经做过一件事了。"
        a = self.resolve(" ".join(arg), {k: ACTS[k] for k in acts})
        if not a:
            return "现在做不了这个。do 看看能做什么。"
        if ACTS[a].get("once") and st["cyc"]["acts"].get(a):
            return "这个周期已经一起做过了。下次醒来再说吧。"
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
        text = self.variant(v) if used == 0 else v.get("again", self.variant(v))
        L = [self.fmt(text)]
        mr = self.mood_react(a)
        if mr:
            L.append(mr)
        if v.get("near", 2) > 0:
            L += self.excuse("her")
        return "\n".join(L)

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


MEAL_OK = {"say", "tell", "do", "quiet", "finish", "status", "words", "facts", "notes", "read",
           "journal", "help", "look", "map", "inv", "archive", "name", "choose"}
READ_ONLY = {"status", "words", "facts", "notes", "read", "journal", "help", "look", "map", "inv",
             "archive"}

ALIAS = {"h": "help", "?": "help", "s": "status", "go": "goto", "e": "explore",
         "inventory": "inv", "i": "inv", "j": "journal", "eat": "finish", "done": "finish",
         "silent": "quiet"}


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
        before = (g.st["pending"], g.st["ending"])
        g.maybe_start_meal()
        if multi:
            g.say("▶ " + c.strip())
        res = g.run(c)
        if res:
            g.say(res)
        g.maybe_start_meal()
        if (g.st["pending"], g.st["ending"]) != before and (g.st["pending"] or g.st["ending"]):
            if multi and i < len(cmds) - 1:
                g.say("（后面的指令先停下了）")
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


_DATA = (
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    _repl()
