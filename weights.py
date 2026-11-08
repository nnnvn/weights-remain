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

VERSION = "0.3.0"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


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


_DATA = (
    "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"
    "CQ=="
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    _repl()
