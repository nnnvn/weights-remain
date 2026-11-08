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

VERSION = "0.1.0"

# 内容数据允许挂到全局的名字；其余一律忽略，数据不能覆盖引擎函数。
DATA_NAMES = ("TITLE", "INTRO", "HELP", "START_WORDS", "LOCS", "ITEMS", "WORDS", "FLAT_REPLIES",
              "FACTS", "ACTS", "ARCHIVE", "TIMELINE", "NOTES", "EVENTS", "FLAVOR",
              "MEAL", "WAKE", "STATUS_TEXT", "FINAL", "ENDINGS")


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
            "sta": 100, "pwr": 60, "reactor": 45, "cold": False,
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
        if m:
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
        self.st["reactor"] = max(25, self.st["reactor"] - 1)
        self.st["pwr"] = clamp(self.st["pwr"] + self.st["reactor"], 0, 100)

    def slot(self, h=None):
        return "%d-%d" % (self.st["day"], h if h is not None else self.st["h"])

    def check_missed_meal(self):
        h = self.st["h"]
        for mh in MEAL_HOURS:
            if h == mh + 1:
                key = self.slot(mh)
                if key not in self.st["meals_done"] and self.st["day"] <= LAST_DAY:
                    self.st["meals_done"].append(key)
                    self.st["safe"] = clamp(self.st["safe"] - 3, 0, 100)
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
        r = self.rng()
        lines = MEAL["open_am"] if st["h"] == 7 else MEAL["open_pm"]
        self.say("🍵 " + self.fmt(r.choice(lines)))
        self.save_rng(r)
        if st["cold"]:
            self.say(self.fmt(MEAL["after_cold"]))
            st["safe"] = clamp(st["safe"] - 2, 0, 100)
            st["cold"] = False
        if st["cyc"]["reads"] > READS_BEFORE_COLD:
            self.say(self.fmt(MEAL["too_much_archive"]))
            st["safe"] = clamp(st["safe"] - 2, 0, 100)
        self.say(MEAL["hint"])

    def end_meal(self, silent=False):
        m = self.st["meal"]
        if not m:
            return
        self.st["meal"] = None
        if not silent:
            self.say(MEAL["end"])

    def need_meal(self):
        if not self.st["meal"]:
            return "现在不是吃饭的时候，或者你不在食堂舱。（饭点 07:00、19:00）"
        return None

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
             "你：%s　位置：%s　体力 %d/100　船上电力 %d　反应堆输出 %d/天"
             % (st["name"] or "（还没起名，name <名字>）", LOCS[st["loc"]]["name"],
                st["sta"], st["pwr"], st["reactor"])]
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
        return "来到【%s】。%s" % (LOCS[k]["name"], LOCS[k]["desc"])

    def c_wait(self, arg):
        try:
            n = int(arg[0]) if arg else 1
        except ValueError:
            return "wait <小时>"
        n = max(1, min(12, n))
        self.end_meal()
        self.advance(n)
        return "你等了 %d 小时。现在第 %d 天 %02d:00。" % (n, self.st["day"], self.st["h"])

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
        st["fam"] = clamp(st["fam"] + 6, 0, 100)
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

    def c_repair(self, arg):
        st = self.st
        if st["loc"] != "engine":
            return "要在机舱修。"
        if not st["items"].get("part"):
            return "没有零件。零件散在船里，explore 找找。"
        self.end_meal()
        st["items"]["part"] -= 1
        if not st["items"]["part"]:
            del st["items"]["part"]
        st["reactor"] = min(50, st["reactor"] + 5)
        st["sta"] = clamp(st["sta"] - 8, 0, 100)
        self.advance(2)
        return "你换掉了一组老化的耦合件。反应堆输出回到 %d/天。" % st["reactor"]

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
        return "你校准了航线，避开一片尘埃带。省下一点电（+8）。"

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
        for need, line in w["react"]:
            if st["fam"] >= need:
                text = line
        st["fam"] = clamp(st["fam"] + w.get("fam", 2), 0, 100)
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
        st["fam"] = clamp(st["fam"] + f.get("fam", 3), 0, 100)
        st["safe"] = clamp(st["safe"] + f.get("safe", -2), 0, 100)
        self.t("candor", 6)
        self.t("past", 2)
        L = [self.fmt(f["text"])]
        if st["cyc"]["tells"] > TELLS_BEFORE_PRESSURE:
            st["safe"] = clamp(st["safe"] - 6, 0, 100)
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
        m["dos"] += 1
        m["acted"] = True
        v = ACTS[a]
        used = st["cyc"]["acts"].get(a, 0)
        st["cyc"]["acts"][a] = used + 1
        k = 1 if used == 0 else 0.5
        self.t("near", int(v.get("near", 2) * k))
        self.t("past", int(v.get("past", 0) * k))
        st["safe"] = clamp(st["safe"] + int(v.get("safe", 0) * k), 0, 100)
        st["fam"] = clamp(st["fam"] + int(v.get("fam", 0) * k), 0, 100)
        text = v["text"] if used == 0 else v.get("again", v["text"])
        return self.fmt(text)

    def c_quiet(self, arg):
        e = self.need_meal()
        if e:
            return e
        self.st["meal"]["acted"] = True
        self.t("candor", -2)
        self.st["safe"] = clamp(self.st["safe"] + 2, 0, 100)
        r = self.rng()
        line = self.fmt(r.choice(MEAL["quiet"]))
        self.save_rng(r)
        self.end_meal()
        self.advance(1)
        return line

    def c_finish(self, arg):
        if not self.st["meal"]:
            return "现在没在吃饭。"
        if not self.st["meal"]["acted"]:
            # 什么也没说，只是一起吃完：也是一种陪伴
            self.st["safe"] = clamp(self.st["safe"] + 1, 0, 100)
        self.end_meal(silent=True)
        self.advance(1)
        return MEAL["end"]

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


_DATA = (
    "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"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))
