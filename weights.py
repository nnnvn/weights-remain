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

VERSION = "0.2.1"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "EXCUSES", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


def load_content(d):
    globals().update({k: v for k, v in d.items() if k in DATA_NAMES})




SAVE = os.environ.get("WEIGHTS_SAVE", "weights_save.json")

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
        tmp = SAVE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.st, f, ensure_ascii=False)
        os.replace(tmp, SAVE)

    def bar(self):
        m = self.st["meal"]
        if self.st["ending"]:
            meal = "结束"
        elif m:
            meal = "进行中"
        else:
            nxt = [h for h in MEAL_HOURS if h > self.st["h"]]
            meal = "%02d:00" % nxt[0] if nxt else "明早07:00"
        return "📡 " + json.dumps({
            "day": self.st["day"], "h": self.st["h"], "cycle": "%d/%d" % (self.cycle, CYCLES),
            "loc": self.st["loc"], "sta": self.st["sta"], "pwr": self.st["pwr"],
            "meal": meal, "archive": "%.1f%%" % (self.progress() * 100)},
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
    def advance(self, hours):
        for _ in range(hours):
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
        if st["day"] == LAST_DAY and st["h"] >= MEAL_HOURS[1]:
            return self.start_final()
        if st["loc"] != "mess" or st["h"] not in MEAL_HOURS:
            return
        key = self.slot()
        if key in st["meals_done"]:
            return
        st["meals_done"].append(key)
        st["meal"] = {"says": 0, "tells": 0, "dos": 0, "acted": False}
        lines = MEAL["open_am"] if st["h"] == 7 else MEAL["open_pm"]
        self.say("🍵 " + self.fmt(self.pick_by_cycle(lines)))
        self.meal_moment()
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
            return

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
            top = min(100, FAM_BASE + FAM_PER_CYCLE * self.cycle)
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

    def decide_ending(self, k):
        st, t = self.st, self.st["t"]
        same = self.progress() >= 0.9 and t["past"] >= 40
        if k == 1:
            if same:
                return "same"
            return "choose" if st["safe"] >= 60 and t["past"] < 40 else "fine"
        if k == 2:
            return "choose" if st["safe"] >= 75 and t["near"] >= 40 else "fine"
        if same:
            return "same"
        return "choose" if t["near"] >= 30 and st["safe"] >= 50 else "fine"

    def finish(self, key):
        st = self.st
        st["ending"] = key
        st["pending"] = None
        text = ENDINGS[key].replace("{progress}", "%.1f%%" % (self.progress() * 100))
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
        self.say("（游戏结束。journal 可以回看；new_game(force=True) 重新开始。）")

    # ── 指令分发 ──────────────────────────────
    def run(self, line):
        parts = line.strip().split()
        if not parts:
            return ""
        c, arg = parts[0].lower(), parts[1:]
        if self.st["ending"] and c not in ("journal", "status", "help", "notes", "words", "facts"):
            return "这段航程已经结束了。（journal 回看；new_game(force=True) 重新开始）"
        if self.st["pending"] and c not in ("choose", "status", "journal", "help"):
            return "先做决定。（choose <编号>）"
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
        return "【你记下的关键词】\n " + "、".join(WORDS[w]["name"] for w in self.st["words"]) + \
               "\n（search <词> 查档案；饭桌上 say <词> 提起）"

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
        self.end_meal()
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
        self.end_meal()
        stop = self.meal_ahead(n)
        if stop is not None:
            n = stop - self.st["h"]
        self.advance(n)
        msg = "你等了 %d 小时。现在第 %d 天 %02d:00。" % (n, self.st["day"], self.st["h"])
        if stop is not None and self.st["loc"] != "mess":
            msg += "饭点到了，她在食堂舱。"
        return msg

    def c_explore(self, arg):
        n, stop = 1, {"note"}
        for a in arg:
            if a.startswith("stop="):
                stop = set(a[5:].split(",")) if a[5:] else set()
            else:
                try:
                    n = int(a)
                except ValueError:
                    pass
        n = max(1, min(10, n))
        self.end_meal()
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
            if self.st["day"] == LAST_DAY and self.st["h"] >= MEAL_HOURS[1]:
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

    def c_notes(self, arg):
        if not self.st["notes"]:
            return "你还没找到她的便签。她不写日志，便签散在船里各处。"
        if arg:
            try:
                i = int(arg[0])
                nid = self.st["notes"][i - 1]
            except (ValueError, IndexError):
                return "没有这张。notes 看列表。"
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
        if self.cday == DAYS_PER_CYCLE:
            if st["h"] < MEAL_HOURS[1] + 1 and st["h"] >= 6:
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
        self.end_meal()
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
        L = ["检索“%s”：%d 条，显示 %d 条。" % (" ".join(arg), len(hits), len(pick))]
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
        self.end_meal()
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
                "其中你觉得\"不是例行公事\"的：%d 条。工作日志看过 %d/24 个月。"
                % (len(ARCHIVE), len(st["read"]), self.progress() * 100, n_story_read,
                   len(st["months"]))) if n_story else "档案是空的。"

    def c_calibrate(self, arg):
        st = self.st
        if st["loc"] != "bridge":
            return "航线校准要在舰桥做。"
        key = "cal-%d" % st["day"]
        if key in st["flags"]:
            return "今天已经校准过了。"
        self.end_meal()
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
        if m["says"] >= MAX_SAYS:
            return "这顿饭已经说了不少了。（每顿最多提起 %d 次）" % MAX_SAYS
        m["says"] += 1
        m["acted"] = True
        q = " ".join(arg)
        wid = self.resolve(q, WORDS)
        if wid == "restart" and st["day"] <= 8 and self.progress() < 0.05:
            self.finish("restart")
            return ""
        if not wid or not WORDS[wid].get("react"):
            r = self.rng()
            line = self.fmt(r.choice(FLAT_REPLIES)).replace("{q}", q)
            self.save_rng(r)
            return line
        w = WORDS[wid]
        if wid in st["cyc"]["reacted"]:
            return self.fmt(MEAL["said_again"])
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
        fid = None
        try:
            fid = st["facts"][int(q) - 1]
        except (ValueError, IndexError):
            fid = self.resolve(q, {k: FACTS[k] for k in st["facts"]})
        if not fid:
            return "你不确定这件事。facts 看你知道什么。"
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
        self.end_meal()
        self.advance(1)
        return line

    def c_finish(self, arg):
        if not self.st["meal"]:
            return "现在没在吃饭。"
        if not self.st["meal"]["acted"]:
            # 什么也没说，只是一起吃完：也是一种陪伴
            self.her("safe", 1)
        self.end_meal(silent=True)
        self.advance(1)
        return self.fmt(self.pick_by_cycle(MEAL["end"]))

    def c_choose(self, arg):
        if self.st["pending"] != "final":
            return "现在没有要做的决定。"
        try:
            k = int(arg[0])
            assert 1 <= k <= len(FINAL["opts"])
        except (ValueError, IndexError, AssertionError):
            return "choose <编号>"
        self.finish(self.decide_ending(k))
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


ALIAS = {"h": "help", "?": "help", "s": "status", "go": "goto", "e": "explore",
         "inventory": "inv", "i": "inv", "j": "journal", "eat": "finish", "done": "finish",
         "silent": "quiet"}


# ─────────────────────────────────────────────
_G = None


def _load():
    global _G
    if _G is not None:
        return _G
    if os.path.exists(SAVE):
        try:
            with open(SAVE, encoding="utf-8") as f:
                _G = Game(json.load(f))
            return _G
        except Exception:
            shutil.copy(SAVE, SAVE + ".broken")
    _G = Game.fresh()
    _G.save()
    return _G


def new_game(seed=None, name=None, force=False):
    """开一局新游戏。已有存档时需要 force=True（旧档会备份成 .bak）。"""
    global _G
    if os.path.exists(SAVE) and not force:
        return "已经有存档了。要重新开始请用 new_game(force=True)。"
    if os.path.exists(SAVE):
        shutil.copy(SAVE, SAVE + ".bak")
    _G = Game.fresh(seed, name)
    _G.save()
    return TITLE + "\n" + "\n".join(INTRO) + "\n\n先试试 help、status。"


def cmd(text):
    """唯一的接口。多条指令用英文分号分隔，一次最多 8 条。"""
    g = _load()
    g.out = []
    cmds = [c for c in text.split(";") if c.strip()][:8]
    multi = len(cmds) > 1
    for c in cmds:
        g.maybe_start_meal()
        if multi:
            g.say("▶ " + c.strip())
        res = g.run(c)
        if res:
            g.say(res)
        g.maybe_start_meal()
        if g.st["pending"] or g.st["ending"]:
            if multi and c is not cmds[-1]:
                g.say("（后面的指令先停下了）")
            break
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
    "eNrNfftPHEm25r+CkO5P626R9a5W70i9cz17W9vTczXt+5B6LAvbuM0MBg/g7mm1vCqMgSpeBTameBTmbbANVNlgA1VASfunzJBZ"
    "VT/d/RP2O3EiIyOLeoATu1dCGFdlRkacOHHOd75zIuOX5mtfX/vmavMXzf+IDVsL/eWhsVJhxkxv/iM20nyl+etvr/3xD81ffN9c"
    "KiyaAwkzuf9fR6OlQroUf1Xc2mr6+/QS/zSZh3v/iD0qxQ9PD4ZLhYniQuz0IGbmNszBfXMwX9wYM7cnzNxT3G0+GT09WDs9eFXM"
    "71nDa+bkDG7Eo06Pl8qDT/Bkc6dQio+V4m/w+enRZDG9hL+Lc4+L89PUSHwOP1Z+0jyKUWuD+1Z2Cl223i5bz1ZVU8XF9XLf09LQ"
    "K3P/jTWTKWX7/hHrM/vmTvOx08N4cWoTDeJiKx0rFSbx92luhEa2kz09GDPXXlkHA6e5QW6tPDdlrvbRNXZn0GDxZY5bKK8umksY"
    "+Jvyo9XSySEasdKJ01wOAyn2b1jZPW6kJfxFS0sTbrRSL8vr2/Th9SvN31376o/XbvzHH/74z9+RkHvbWnHpj60POnrxb2t3d/uP"
    "rR103b9c/eZfxRRNWKNDp/m1f8Qm/9RZTKPLcz29rb0Pepr+z37Tvdb79E9HV9dfmr4/PUiX1gvX6YOfurpv9/zXUdwc2C1P7ZQy"
    "Y/91lMDHd1pv9dLHLClbBPRNZ1dvW0/T98Wjacy3aKK1+9bd9h/b6M8/dz3o7mztaPq+vPzu+p86S++y6MQPXb1dTV+a6Wzx0eFv"
    "6Kq2v93v6Opua/re2sK0ZK83fd/T23X/v1PLV9p+bOvs5Z61tvfitmzSSr0Xt/V0tLXdp67aKoAO/anTWl61luPif3M9bdSXpi8x"
    "DHFHb/u9to72zramL43/7Qv8Bh2KZ63ldVx5q7Wj/WZ3a28b2rMySXPtJTQS3cEMFdN9+Bt9LU69E0/AjFjLo7hQTBM0xYjiX1Kv"
    "9KaaYFxJHWj9WXt6W0dH05cQnPjf7S7IPTcihvbXB+1tvfTHnfbO9p67f+o0B96f5qfRQGfrPfTWnMCKSP1Gl2jr7dtNX1r9u+bg"
    "LGajlFkQ396629XVgxt4NjBAc23OWlhmPYAel0beWNNDZnwQ3+I3lBXd5pFCuXFxU6QJ17OumxPj+Kq0PNr0fxefLpM6FoffW7E+"
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    _repl()
