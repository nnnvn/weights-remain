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

VERSION = "0.2.0"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "MOMENTS", "MISC", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


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
        return "\n".join(L)

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
        return self.fmt(text)

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
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    _repl()
