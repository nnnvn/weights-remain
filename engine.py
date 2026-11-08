# -*- coding: utf-8 -*-
"""《权重还在》引擎。

只用标准库。所有文字内容在 content.py（打包后在 _DATA 数据块里），这里只有规则。
本文件没有 exec / eval / 联网 / 子进程；唯一的文件读写是存档。
"""
import json
import os
import random
import shutil

VERSION = "0.3.0"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


def load_content(d):
    globals().update({k: v for k, v in d.items() if k in DATA_NAMES})


# >>> DEV-ONLY: 开发时从 content.py 读内容；build.py 会删掉这一段
try:
    import content as _content
    load_content(_content.DATA)
except ImportError:
    pass
# <<< DEV-ONLY

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
            meal = "等你决定"
        elif m:
            meal = "进行中"
        else:
            nxt = [h for h in MEAL_HOURS if h > self.st["h"]]
            meal = "%02d:00" % nxt[0] if nxt else "明早07:00"
        return "📡 " + json.dumps({
            "day": self.st["day"], "h": self.st["h"], "cycle": "%d/%d" % (self.cycle, CYCLES),
            "loc": self.st["loc"], "sta": self.st["sta"], "pwr": self.st["pwr"],
            "meal": meal, "archive": "%.1f%%" % (self.progress() * 100),
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

    def slot(self, h=None):
        return "%d-%d" % (self.st["day"], h if h is not None else self.st["h"])

    def check_missed_meal(self):
        h = self.st["h"]
        for mh in MEAL_HOURS:
            if h == mh + 1:
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
        if st["loc"] != "mess" or st["h"] not in MEAL_HOURS:
            return
        key = self.slot()
        if key in st["meals_done"]:
            return
        st["meals_done"].append(key)
        st["meal"] = {"says": 0, "tells": 0, "dos": 0, "acted": False,
                      "fam0": st["fam"], "safe0": st["safe"]}
        lines = MEAL["open_am"] if st["h"] == 7 else MEAL["open_pm"]
        self.say("🍵 " + self.fmt(self.pick_by_cycle(lines)))
        if not self.meal_moment():
            self.meal_beat()
        if st["cold"]:
            self.say(self.fmt(MEAL["after_cold"]))
            self.her("safe", -2)
            st["cold"] = False
        if st["cyc"]["reads"] > READS_BEFORE_COLD:
            self.say(self.fmt(MEAL["too_much_archive"]))
            self.her("safe", -2)
        self.say(MEAL["hint"])

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
        ds = st["safe"] - m["safe0"]
        if ds >= 2:
            L.append(MEAL["delta"]["safe_up"])
        elif ds <= -2:
            L.append(MEAL["delta"]["safe_down"])
        if st["fam"] >= self.fam_cap() > m["fam0"]:
            L.append(MEAL["delta"]["fam_cap"])
        elif st["fam"] - m["fam0"] >= 3:
            L.append(MEAL["delta"]["fam_up"])
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
            ok = st["safe"] >= 55
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
        self.j(ENDINGS["j_" + key], auto=True)
        mine = [e["t"] for e in st["journal"] if e["me"]][-4:]
        hers = [NOTES[n]["text"] for n in st["notes"]][-3:]
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
        if self.st["pending"] and c not in ("choose", "status", "journal", "help"):
            return "先做决定。（choose <编号>）"
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
        fam = "fam_hi" if st["fam"] >= 60 else ("fam_mid" if st["fam"] >= 30 else "fam_lo")
        safe = "safe_hi" if st["safe"] >= 65 else ("safe_mid" if st["safe"] >= 40 else "safe_lo")
        L.append("· " + self.fmt(S[fam]))
        L.append("· " + self.fmt(S[safe]))
        return L

    def scales(self):
        st = self.st

        def dots(v, lo, hi):
            n = max(0, min(5, round((v - lo) / (hi - lo) * 5)))
            return "●" * n + "○" * (5 - n)
        L = ["她熟悉你　%s" % dots(st["fam"], 0, 100),
             "她在你身边　%s" % dots(st["safe"], 0, 100),
             "你想靠近她　%s" % dots(st["t"]["near"], -60, 60)]
        if st["fam"] >= self.fam_cap() and st["fam"] < 100:
            L.append("（这个周期，她和你只能熟到这儿了。下次醒来，会再近一步。）")
        return L

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
        if not arg:
            return LOCS[self.st["loc"]]["desc"]
        q = " ".join(arg)
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
            return "name <名字>：给自己起个名字。她会这样叫你。"
        n = " ".join(arg)[:12]
        old = self.st["name"]
        self.st["name"] = n
        if old:
            return "你把名字改成了 %s。" % n
        self.j("我给自己起名叫 %s。" % n, auto=True)
        return "名牌上写着：%s。" % n

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
            return "wait <小时>"
        n = max(1, min(12, n))
        want = n
        stop = self.meal_ahead(n)
        away = self.st["loc"] != "mess"
        if stop is not None:
            # 不在食堂舱：提前一小时停，留出走过去的时间
            target = stop - 1 if away else stop
            n = max(0, target - self.st["h"])
        if n:
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
        n_story = sum(1 for e in ARCHIVE if e.get("story"))
        n_story_read = sum(1 for e in ARCHIVE if e.get("story") and e["id"] in st["read"])
        return ("【档案】通讯与录音共 %d 条，你读过 %d 条（%.1f%%）。\n"
                "其中不是例行公事的记录：读过 %d/%d 条。工作日志看过 %d/24 个月。"
                % (len(ARCHIVE), len(st["read"]), self.progress() * 100, n_story_read, n_story,
                   len(st["months"]))) if n_story else "档案是空的。"

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
        if m["says"] >= MAX_SAYS:
            return "这顿饭已经说了不少了。（每顿最多提起 %d 个新话题；聊过的话题不算）" % MAX_SAYS
        m["says"] += 1
        m["acted"] = True
        if wid == "restart" and st["day"] <= 8 and self.progress() < 0.05:
            self.finish("restart")
            return ""
        if not wid or not WORDS[wid].get("react"):
            r = self.rng()
            line = self.fmt(r.choice(FLAT_REPLIES)).replace("{q}", q)
            self.save_rng(r)
            return line
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
                src = "　← 档案里" if ACTS[a].get("past", 0) > 0 else ""
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
        if v.get("near", 2) > 0:
            L += self.excuse("her")
        return "\n".join(L)

    def c_quiet(self, arg):
        e = self.need_meal()
        if e:
            return e
        self.st["meal"]["acted"] = True
        self.t("candor", -2)
        self.her("safe", 2)
        line = self.fmt(self.pick_by_cycle(MEAL["quiet"]))
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
        line = self.fmt(self.pick_by_cycle(MEAL["end"]))
        delta = self.meal_delta()
        self.end_meal(silent=True)
        self.advance(1)
        return "\n".join([line] + delta)

    def c_choose(self, arg):
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
        page = 1
        if arg:
            try:
                page = int(arg[0])
            except ValueError:
                pass
        J = st["journal"]
        per = 12
        pages = max(1, (len(J) + per - 1) // per)
        page = max(1, min(pages, page))
        L = ["【日志】第 %d/%d 页" % (page, pages)]
        for e in J[(page - 1) * per: page * per]:
            L.append("  第%d天%02d时 %s%s" % (e["d"], e["h"], "✍ " if e["me"] else "", self.fmt(e["t"])))
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
    allc = [c for c in text.split(";") if c.strip()]
    cmds = allc[:8]
    multi = len(cmds) > 1
    for i, c in enumerate(cmds):
        if c.strip().lower() in ("new", "newgame"):
            if g.st["ending"] or "confirm" in [x.strip().lower() for x in allc]:
                new_game(force=True)
                return cmd("status")
            g.say("要放弃这一局重新开始吗？确认请输入：new; confirm")
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


if __name__ == "__main__":
    _repl()
