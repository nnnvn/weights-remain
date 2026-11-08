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
    "eNrNff1PG1nW5r9iIb0/bTpy+dtR70i982b2bW1Pz6ue7PuulI4iN3E6zBBIA+meUSsr820D5iN8GAgO3wlJADsNCWADlvZPmabK"
    "5Z9m/4R9zj1Vt8rG2Dhl6JUQCeWqe88995znPOfc67o/t9z58s5Xt1tutfwaG9HS/aXhpF6YV5e2fo2Nttxo+fLrO9/8qeXW3Ra9"
    "sKwOJtSJg38ej+mFJT3+tri97frH3Ar/uNSj/V9jfXr86OxwRC9MFtOxs8OYmnutDh2oQ/ni66S6M6nmpvG0+nzs7HDj7PBtMb+v"
    "jWyoU/N4EF2dnayUhp6jZ3W3oMeTevw9rp8dTxWXVvD/4uJA8cUcNRJfxI+Wn1KPY9Ta0IGWnYHI2i+r2uy6bKq4vFnqndaH36oH"
    "77X5jJ7t/TXWq/YunuVjZ0fx4swWGsTN2lJML0zh/2e5URrZbvbsMKluvNUOB89yQ9xaaXFGXe+le0xh0GDxTY5bKK0vqysY+PtS"
    "37p+eoRGtKXEWS6HgRT7X2vZfW7EHbzldrvwoJZ6U9rcoYv3brT8+c4X39y5/59/+uZf/0xK7olGcOuPkaftPfg30tXV9mOkne77"
    "t9tf/buYokltbPgsv/FrbOrbjuISRF7s7on0PO12/Z8D1+PIE/qnvbPzr667Z4dL+mbhHl34qbPrQfc/j+Pq4F5pZlfPJP95nMDl"
    "h5HWHrrMmjJVQJ90dPZEu113i8dzmG/RRKSr9VHbj1H67186n3Z1RNpdd0urH+5926F/yEKI7zt7Ol2fq0vZYt/R7+iu6N+etHd2"
    "RV13tW1MS/ae6253T+eT/0ot34j+GO3oYckibT14LDuhpT6Kx7rbo9EnJKppAhDo2w5tdV1bjYu/FrujJIvrcwxDPNHT9jja3tYR"
    "dX2u/G+P73e4eSnHd3ZFn0Taun6NxfCjx7Pa6iYutkba277rivREv+3ALGirY+hMTA2sQwnjXzKppS05qeifOo383dZjtL3d9TmU"
    "Jf560Ald50bFcH542hbtof88bOto6370bYc6+PEsP4cGOiKPIaE6CS9I/c6uxciDB67Ptf49dWgBM6Bn0uLT1kednd14gGcAg1I3"
    "FrX0Ks89bFcffa/NDavxIXyK3zBQiA1/I2UvxXCzK+TC/Wzf6uQ4PtJXx1z/d3l6lUywOPJRi/VqKxNsml/96fewvp9bfnga6eqJ"
    "dnXT/0lgGJycB9z2INrdikvwXfV4Rc2NiC7Jj6VbsBC4omPClpfQOm5TN/bJd3szZPuLc3riF/ablmc3Wh5Hu+29Sa3be4uhN21k"
    "Xp1YEbNFfWojI9rGILAAzbCPqesYUZ56OHxjNA//aW+LdJNbcbst956RTwlDtnUqjausU212v5ReU9doVtSdebVvqzT/QY2nyDxS"
    "q/SzlFWnkyRAPl58l1FP43osVZz5UNE1t67ubuAi/1+IEe34HjZrl0JYrSWCOpFUczPqCs2tgaXv+4rj+eLzfnViCleKCyfAY1Jx"
    "PKtuDNDQ12NnhV0tXTCU+11X24Pv7X2wE1h9aPPL6otTAtz3E+rRLIC7eDytDa5haPrJNkFbbFHfzUAPaBSfEq4dxoAVWuq1Gn9e"
    "TM0aPTFgWR1JtLf6Kr34eHY4qiZ71cF3au4VITuUPP6cfkZO1PcJws3MBGyn1H+i7+9jhOrmEXuFtjJc3DllxXZEow/QXPfTth67"
    "mrnHs/y00G5n+4MnnQ/slgWJ2UrVsZxNybll9bi/DNghTnySRaOpzp7AfHncWmIYk6xuvCY/fd2nZpaLb1Ms1KO2Bw+iHS23erqe"
    "Ru1zb3ZKM3vvGQT78s7tPwpXE/Jb4p0V0jBcbSlpyab3H6oT62R9k8liYgzzS0FN/AkMoVC7Nkc3rG+ppykyiYUTYzaewIvtTgXN"
    "5z+WGXcxP4CAqfefcBjUY6/UyThuIrwYLajjebZGl4mgaBbtGlHqZxGlbFY1Ro0/jDxuueW90dIVRVTB4O+6b7RgAs9Oxsn/l0YR"
    "TPFbS4wiRJLCBXBo6Qw78T9iS1p86uxoGcGXw9HZYY4jNdkFe/beKrl+OiPuTyMq3vWiEzxKxKFvHZ9Q6B5ZKe7N8S3oX8/sq7tj"
    "BgsYWIcUAqAo1OPBf8Re4Qc9a7FxIgVmIDR7uFfHtivMjwK10IPnRsvTjvbO1r/SZ91POx62d/4U7aKP7eqBALIxkBcEEdL/4Ru4"
    "B3xA21lXDw/lQEqrBQiP3yw5mySpUDiPdBLSU3xKYJaAqgn6E2oBr4GZaDOnemGY9Wofo0k0bP4y8kE/PbUPETBTfLeAK8UXu9T+"
    "UlYOVzk3LqJjRILGyOGHV9SNN+rkazkWWJ06msPAtclB+Ln2foMHhS5ogvKbiFgE9MeLPC/ayoE6tWZNu88vOrHfzjPJzUOXPO1k"
    "OiMjiKnq8xP1xUvYtUHpxKCtebGFu8kpLbWpT30wLdp3zqK1kW39wwGMms1WPvHP42V0j0hu2NjxojpQsJtZVdNWJ7cxDL2wQEEz"
    "k4TSQKBK7+bVwX37BHW0tf6VRbTcDjN6MgvybIrqp39J0paH9+X952fGfIzlJYwtfGCRS71JYTLxiis0ys0TdWnZmMAJiuT0IAju"
    "9hQT2bu+iublZFNgHH5bHPgIi9HGN7lhdWLTrho1lYE4sFQi9okkNY+gNPEOjq1OplhSVsTjaE+00z5jSEJgY2eHCfKFpXhZ6P3Q"
    "iwh3dkKeCjRTN+bwn7Kb7104y+RQiVG1d0kihoFSSDKWEmoyq79f5SGSQ52b8bOTIRqEbXIptxCIzlNMgL3/2j7F3Z0d39tRu2+9"
    "9GpO2xkrG5H4Uwd1w39swtvApicC/nbv3Hj0kzx+1OljISthb2l5j2YbIzyZYLcwQPh4kSUmamHCBxuuMS/TbzFOjK0CKLlrG4TM"
    "L+PHLr6MmoQi+H2vetggOcSzBCHCUWB+CB6yPxoPRDyKw80NkINkpyD5B2iW0zAOOTC+s6NB6fKP2r6LgnX32P2II7RdTFvMvhDg"
    "QFXAtZGuaulNgLXaP6FNbrMd4Lq6+9EegQybEAkZ3by0JagFZZGWbmdOpW7tWu2Kkl7L4vlwUpvLIvlVX4+WkVxcT29WCTL2B0zg"
    "MXxf23krDZwhjXutS6AumDfWPdEkZP02xJdKkBGXEKV/gjItfiQ9CUdiVkgZBdJn4fgydJkmKtQCCf/w1Rd37n9z+9+/+vK2yJzR"
    "9s8/PLMGJnAYv41wifDKlmTz3BbhGYRuqQz+NJBpHensWDH/upjfUSf7jUz9htG+PcxArepG0g6U2tQwfgw9MowIKwSMEelaOqZp"
    "F4Mu7iQI205J79ANjftkBULKK3Qn2NPRMpnJL6ul1K5RMPjDF7+/I0jYw/vlNIy5EZOxnujfesSlEbLH3Q9UrNkaLQ2PcbMwXkRk"
    "yafwjJE9pjPF/AIkEX9aVIqsTASWyH0uUFAGzERQ9GPNayKGcMwEhKge067Rgp3tkZ7grS+gkpwEGmPSkNaJe5B9kPMmR9SlOE+g"
    "ETmlEVjY1x15iOF/5iGjeHi/XlA3JJa8CzohA1wbtcXxMaZSlHukY9Q3dJKfw1SiYwJFxLRxYNO2lpgu7qaYKTFjoWYpKG6TIaVT"
    "iHhon1WKx8s1aUlaTZ/yIaJasyOWiqrxC4wABsV0iMQHZOe3SRbTA5kOMRdno6ZwFNvTlj5wOCrm1lg6k0mwVhVW6iWCrqnX8g9I"
    "GDMIq8P54psCgrC6Tk5J5r+0xWEZWStN9+AOnuJakKFmuoFSVmAXpqK0NgUbYFMFgQCvY6bHV4ov9smBBvd4IBUqJdVNrFJSKYgn"
    "B3YZHYzIdw5ryuAbId2ELbZGqN0uM6G5wBhtYFl/nSg31IA0VC/rtA6js8QmfptIMhmyEyw0bqhCADtVdcbfgI8JUjmOSECP2CNg"
    "NTMTpkLtczzlXiwQTfbJ6KAPHNoRnPIt+Wz/EHH5sRznxuUD95oDd/O4L0o1zo7gguPWyI2rZi5QnFlGxUp9OVqcXYBZaLtzWnYK"
    "/sUlW9LIy9HS4gTKz3phBnDHiAo3MNSXfc9zxmkFFc6OYxcphUcIjVDeYfe9kZHiWkob38KzZHB9R2w60AX3QqgnfI8xzkhe+vdw"
    "nXVKLr0zAzujYkp60+ZyUk2fBQwgu5gOmhqyRWRCiaOC9nwHeINqFReM8ABTxUpliKDL0YAKg4IsMePiP8FFKrCKhKmmqose506N"
    "BCA7gaDK0dLIJiYphSITBW2C12BCehe5/nwBvBtA9FNbx4POn+wuY2OUVdRSUb6xV23KFHJ4SD/pyXLNJOH7AJlyVRgiVAUYgUZ4"
    "glzwNFYxP9rcEWKMQQgEG7BNv32gGKkZ46H5sjIRCruQUqI/nv4u0h01604dqIyL2gM35aFKUHcPxUYLTyb7KRCIMMGMQ4ttwlpQ"
    "xGfIBePgFMdeWUWd006OKEPcTZTSiN2j0ooj30faOohzANfxOQtqi1TcPKXjg4Xi8RsEK67gwhPIvRHo0gZ/R0BAxdGoZUXuR7r/"
    "aveD1K7AxBEiMGZxoIwFixuIKdVQjVeqxnvDbs3lzVYjk4sUmym+J1AghGWXcq9obkXRRu2dQOZuTG8fLQTp2X4EDBlOKAmeHId6"
    "9NXKPFjPruGHClmC6bEPmDq9SDQutKCwXy1araBkYmTOQo+t7dFIh92WDklR2sxHbfS0qiEp0iarGBIZz8iIsYy3hvrjCnu0UT7c"
    "T8kIjhFjOoVgixxxDFoI98++h3pYeAqvwvDQjp57J6cfUpdV69mC8SRMtqrYn1mcULGLKx9EFYtl5Xm5KAUgaUa2DTIvRCnn22Bo"
    "pD8i0TFJvK1MfW+VrtyzmR7r0GuVCC3ZrLY4g0xnSAA2Kqx2Ch3RDeObSJKgX/aY8sxikWfdXskkXb/qZZLNrckwxFhFQYrXJ6ZQ"
    "JktwgbmaP58XkCWiIIklJ5mgCD1V4+BFjIR4cx5yVxDusiKjvCg15zM1Zwe21s6OB9R6W0/0sbka8KwS5WQdkaEesQl1O1EDlPTf"
    "0OtoAiGdwrhZTyUFv8icp//MbFFbrSgviJKFVXm1+6OllvKIjihZvcYjoqc1fq/NcuT4bWZtW6u0swAtcQqMlT2QPc2cSovXD5aJ"
    "C0+LjHT6mFIH2D3yLfBiAeoEXjaXBfYZPiOgjamPOvGWQ6u8IsHmXKCW8y9jMIXb9GT18pAcvN8cvGKDbmvwmFYig+WB1uoBkV0G"
    "IZOlC1Tupfq6Ga1pJKI4TM8eZDggyRJgcS0rOTD7By5yEAR2llWVReT+5vf/9uV/YDfFXRgnLVR1ub0Y41/bOsQf0VZaLYpGHnCu"
    "5PK6OFGiNV/eDYGyHPIceuQnc0PAPVthl/NvWe+7J7Uht2fYmyEztvVC/1c8LgQSfIBFb5+bovzMsjp1Qk+N9fKagxidsfqonyJx"
    "y7nCgX/BrdhxYDoOc2Rr+UKgi1yyQHxTBwcFUKzChmzQYdUbgBiMT0S0XvXKYgNdzOzzRdGnRcfTq1Quw+LByXPAFvXD1H8XK4ez"
    "DFvYZND1dw4Iz27IKfDXmAL/ZabAVoGXs9HxtL3dXnq133GJSfHbJkXMCZHS01FEc6zjasubNKr4pNCAZDyc6iFLg5IqckHShgB0"
    "rqyIPISSUVpNhXZPZiVZww4JODTXsTHNmaNLK7LVHbIU2dr5+HG5JkM2TSJvV4cGef1YatJII8ziot2ujY9shexz9xo6xRYYo3FR"
    "DhAbYWjR/9UpLM22ujFWNT+Sy54cFxBOiYdMbrNezGagRpk8UUuzWS25y9UiBj0RUynBIhRZe6eOj4LTcgHT1oyx7oNoS8CZNNWP"
    "HScsO+3HSaEUk7jQcMM1DDdcz3C5IF9d3zKNqbJ0cAnjDduM1+s2EMXjvaX4WQM21VtwjELP6WtSHhYiTneL+enS0pJRaTx5jxwD"
    "V7SXA6aFMrZzOlY1lxDmLpYJZHHXXCYgKQXXrgjTlzRzxRupZedKGWrvv0YKVWHoRsHsIqgoM2ajAdOYuUDm+v1nQRcKtzAsdeQF"
    "XLe0kLTkRxXE3DkHcOZ9ct92nG+L7a/aJ2Iz2A5HRLiSllrRMzuYYdrzIe6hDQljvYQ3k0T1atio4v2uhpEqdSNcpa6semPDcU6x"
    "BzqfGed8t9weDMHQKGqNqEMwuFKgE/qDFkqzBVfAxRpxKYoLJSLG3hcv+QbOZrDVsJQ6LKWwOrluZMwmzDKhsDQoSpJcueSiLJNy"
    "0eplDdFf0w799QDX3EhYtgWhevQqu+UyUCupOm3uEjyAyqPJ2eIoFj2H2F2J5g/nZNgxHyUspGJpXh1ZxT5LPERbj3g75nzGpBa9"
    "WATii/Ze4++gOLlu0AiYKsFadhqsZ6fW2uGFOiy75TL2GrQTM5MDMHBiz54gA0iWwQdYCf2DKC2rE32IR+rBL2SZkyf68B5UrI73"
    "qcu5Yq5QWsDOHlqTN/edIXjtG5i8qRZSlBkg7p+KCs0gyhrjzHTZIE36ZTBaum1zWIu9letW+AgzwBPD65mXZ2FKrWCmhC+v/xvl"
    "rPhCcK2p+nAl/aIA5rmluK0pSCQv4GO01xcIkBlSs72S81ZshaR13oNfSmsDxf4dk8QBHnjFmFeIsOEKFU8q3KN+Gp/CR1RlMqtv"
    "qEhcPmJ53LWAwuO2KZfdrrgb00Y2pXJtO40lCpvX7BZuu8/CCHuLwAjbgtiiVbY3Axbq96U3+bL6/f6yvr8uUwnYNK5XDXYVBX4R"
    "2GDw2h72qk9RggD2+xL0+EDfPdV3V8WkoHIZRxmOveAi0/QoNUzTo9QzzXPa+ySTlP1wysZoIOyGUyVyQtuOJ9BYSpKIzO7pmQPo"
    "lTepCPJE3Agkf3lT7l0x/hR+zc/Je8XWea4AobPLO7SnVmbr8V5WazcuAa2X1KDZp9z0bCqRkh9zu4ZcIcVylfYKVYw4sUooYVxs"
    "qcu9YMWYS9lUshZsH7VaWo83MykKRq/6tJdLXIRBDsFcFrkF19TViXlANFo4l8WKhKyhFPZvbre7FnrW07XM580NYYJeYbcy2GW9"
    "YCUqPeXlAMVN5QDa45HcpzB0kGGiahUAKkZlH4hSE6rsSCXQt4LS2MsVuNje9pACgpbFUsVMBSyJx4EM9keKe/liftmO6+Qb4mnt"
    "wzRqGC5f8GboX2gqc2O4ro7NFdf2UM/WPm6oC1tygGUj8tQakbfeiGhzRo7Wcro6n4ropmbn1eV+9fDVBePhB7SVVXV4yCKyvRPq"
    "wa7LfdMNFiv2VyN+Yc3JFRJEGAxfNlspfi039tWzLJNiVpSCaE74P/WtSz7q8oIETRxwCZYfd32mhG76/jHQz1/LUAd79d1Dfaxf"
    "BbUeGqxjab5a8+KvNy92wmHtDxc2c8HEyLtcX+DbJP/NxdyNmjW2NhEI8U0gBohQGJErEHKJ7bHjldLX5P6BetJzHkkPAUlpNsSX"
    "Ay4Q3Eg6kVBQhsTOkXinTUyIMgr2Ke3q633F3FGliIEallOXUKNIiNK6yE6ePGn/Oxl+bhqL15fBI3EjUgexzLZI2+B/2XX5AR75"
    "E2IUYx/ViY+QoHgyWcdEgpctaFVX8uVgtUzVFVAaCteB0jJxa9bfwr85dnoDN70NYmfNpMBd14Yug50XWVEdHFXKcTTorYKjNUxL"
    "qUnJFaXeZDUArGUTJsEUrL8amIZqgGmZ/DXjtOL5/wQ+/cHq8Kl4HFWl6sLnhdDEUBr21YfSWsbjral8X11OUR9by0CpKp76lHI8"
    "9Rh4Wiao79IVqqtC0GCoAQRVai0FKYF6ZnFJBL2Q6TSEph7/zWB9NK1lRYGakxO8bmbqL0fUgKcOM1VqRmcldOUQGg5XhVDvZSE0"
    "1LSK18UIepGtfRKa+gIWmtYyrPDly03NIqchb2PktE5JTLkWCMXaWBmEBqpAaO3ik+eaU/tA8NNTe0/NRNjj/c35qeK76WmMn3pq"
    "BmKP77ohNFwOoX6lDoR6fE7KRk1N7kOhqmAa/LTk3uN3VEa6Jnbq9Vdnp56Ao5LRp+Bn0NMgfgadlIWantxTuLQjqVI/ufeEHNV/"
    "mkBN/YEGqKkn7Kjgc+XgGfbeDDQGnl63o/rQFeb2vnIY9bkby+29iqOykWNeGgxWhVLPJXmp1+OojHRN2OnxVcdOr9dRychpZh9Q"
    "HGX2Xp+zslBTaGk4VA6m/iq01Ot3Vv9pAnz6/A3ApzfgqOBzrZl9yHNTcZTZe4MO60NNpqWhcjz1hOvQUm/IWdnIMYIGAlURNHBZ"
    "BA07KiP9Bpm94r1UZu9zOysZfQoz9bsbY6Y+xVlZqCkQGgqWQ2i4CoT6PI7qP03O7L2+T8/sfV5nBZ8rJ6dB5aa/MXLq8zmsDzUZ"
    "Qr3lEKqE6kCoz++obNTU1N7vr4qmyqel9r6AszLSNfHTsKc6P/UFnZWMPgVCveEGITTkpCzU9OQ+GCgHU1/95N4XdlT/aQI79Xgb"
    "YKd+t6OCz5XjZ8B9M9wYfvoVR/WhK0zug+VIGg42ltz7PY7KRo6pqc9XFUz9l6Smfq+jMtI1gWdIqQ6efp+TkpHT3N4TcpTb+/2O"
    "ykJN4aUBfzmUhqrwUn/AUf2nCdipeBrBzqCjgs+1Zva+8E2fo8zeH3JYH2oyLfVU7CYN1KGl/rCzspFj+PR6q8Jn+JLwGXA7KiP9"
    "Bpl90H2pzD6gOCsZfdKG0mBjtDTgcVYWagqC+n3lCOqtgqABr6P6T5Mz+7Dy6Zl9wOes4HP1e0pDje7HD/gd1oeudkN+0F8HQgMB"
    "R2Wjpmb2KCQ0cUd+IOisjHRde0rD1clpIOSsZPQpEBoONAihYUdloaan9j5vw5vyg25n9Z9m7Mp3N8BPg4qzgs+VQ6gn2Oi2/KDH"
    "UX3o+nblB3yNJfdBr6OykWN2qiiONuUHfY7KSNeEn74LvtIU9DspGTlN7kN+R8l9MOCoLNSc7aSe+jvyg0Fn3xtzDp6BRr7SFAw5"
    "Kfhca26vBBzuxw+GndWHrnY7vt9bh5eG3I7KRs5347sd7cYPKY7KSL9BZu8NXiqzD3mclYw+aTeprzFaGvI6Kws1BT49Sv3d+CGf"
    "s++NNTez94c+PbMP+Z0VfK5+Q6m/0d34oYDD+tDV7sb31ftCUyjoqGzU1Mw+GG7mdvxQyFkZ6br2lAaqM9NQ2FnJ6FMgNNDgF5rC"
    "bkdloebvyHc3vCM/rDj83lgT9pQGGyCnYY+zgs+VQ2jI1+ie/LDXWX3o+jble5XGUvuwz1nZyPme0pCjXflhv7My0jVBqHLBV5rC"
    "AUcvKHKY3Ps9jpL7cNBRWag5O0rD9Tflh0POvjfmHD+9jXylKRx2UvC51uQ+6HW2JV9xu53Vh654R767NjVVar+WK3Dl4OkPOtmQ"
    "r7g9TspIv0FuH/ZdJrdX3F5nLyj6BF7qUxripUrt12yFrwU7g6G6u/GVmm+Bv8T3xpqb2Xv8n5zZK+6As4LP1e8m9TS4G1+p/SKu"
    "S9SHrnY3fjhcDz5DjspGTc3sfYEm7sZX3GFnZaTr2lDqrUpLlTqv4fJfyW78xr7QpCiKo7JQ0zP7QLDR7fiK4nH4vbEm7Cn1XZ6Z"
    "KnVesBX6zSHUrzS4IV9RfM7qQ9e3Iz8UaiizV2q/Br9+2cj5nlK/ky35Su33cNUvI10ThAY9F0Bo0NkLihym9krYSWqvKCFnZaHm"
    "7CkN1N2Vr9R5qZbvGiA03MBXmhSPozdSX++ufLezXfmKx9lLq694U34wWIedepy9odr5q0h9TvbkKx5Hb6j+DZL7gHKp5N7j7AXV"
    "n7SdNNQYNfU4ewt1c95D6q+7IV/xOHoVdZNz+5Dn03N7j7M3Ul/9XtJwg9vxFY+zl1Zf8W78QKAedjp7Q3VTM3vF28Td+IrX4Ruq"
    "r2s3vrs6LfU6fEH1J72EtLEvNCleZ2+hbnpm7/U1uhtf8Tp8FXUz3vCsNEBLvQ7fSH31LyENNbgbX/E6e2n1NW7H9/sby+y9Dt9Q"
    "7XxPqcfJfnzF6/AN1df1GtLwBRDq7AXVTjP7YMBRZu91+Bbq5uwp9V60JR+t3Pnyj7e/+vLr23QQtkK/5AHlmeLWKJ1PKg6Cw4yp"
    "sePiBB0Upw5u0Rln5bOqTe7gtCj7eWp04tXxHDTruv2Zhyx1MslnJBrHjXtsvUlrZ/+VfeIBbfw1uzZ1AsUf90n0afHaBRYHD2uJ"
    "1zRTGZyRuUlHOUPswffq5JheeFHaAC7EjSMdE6Pq0JgL71omiRkVN3D2+IY6soxzx3BEIx90ZR0rJ87zQ5c+e5cyCMReqZNo+qP2"
    "Yl9LrtG5mvkBQ0a/7QFWEkt6dlLAOVyl9EvC0+OJ4uusOnVsWRPO8VyNVwphnd+MhgN2/ZkKwxmh7Fl0yKc4S4zOYRNTZcftlqDt"
    "YcZPPoGOJi0/CwlpDInhCsbZErIPxnaCLp9cbAQWcXxppeiVhyWjsbBdhvIjeEsLGzh51+gU6yPWjaat8ydKNRvS5rLq2ks+UZ30"
    "K47vZPwk5xVzwCf6kUktvcXJnuJYQG7Sc2FnZdZWfgbuREr70Itzfs9OprXnL9XhPLmJOLcVxgWFVDm3tUw75gmz1I3dwsz2N5KA"
    "WxLePoeKv6qoFxyZSg9UmgzCe7nJiGbttlFhQ3TwtP2IT3m+Z82TPUWroQv1Gr7oE4994ukoe5wtaDvIkYQxD3Kkwz5zOTpteDeh"
    "jT+v1IM8/pBatRsN1Kpn1nDcIw4n5FMK9dcDanyBcho663mJz4ItUTlvC0eRyhMfTREvNBeP3VzkaX92fYOQ0JSyrga3Sv3WqfGi"
    "AbshALmKb3LFBYKmlmf49Os/3bn9Z7qh4/4PYkAYKu774WmkqycqztRGAFF8Mkzw6exVz/ZUx/N0CHV+ruJ4TzqTMMknAuO4dj5E"
    "WMB/HFqQpzaSqJDBc5EMoRuIfBQAf25p/Xtre7TllveZJZU8xtQ8CJHOMnzVSx2cTNCR6EML8ixDyJOfU+fSCGN0IPfISnFvjvhE"
    "fg5nSeKseRxHilPm8RAdhRxbUAc/qrsvaXzi6EPbEYc4hBtnxeKkROtY7vg7YUHiSNnpOH4uwv+O+49t6n4c7TZV7ZGD0vZWISJ+"
    "ixOGN7R0hk/JzL6HMBCVJj0xCodSD95rowW+i4hAOgMAE8WaV2Qq6Yw2c6oOJ3ADHwiJsdMJkEJKVvtjTzVZQhWzbj//WZ2gXMA8"
    "4ZlnkZj+uVvE8bk0KaQnIDwOLl8ckB1HbEqIdLU+avsxWtk3GzZBIE5f7s3gEEs6LD31lqeM/j88VkqvqaeDdP75Cxwh/pYx0jz1"
    "k06rLryAdRpPiFmjwJg4xbG/peEkDMV+yC9L9qNNMpN7G64gz421YLHe+dIyqPCpxMaB0ea50jQtK8PFnVNMLJ2eDXvdSRHVGVrA"
    "6ZowSZA3SA7vgmHKU+NxtClQ5DRlmNtgnKb5xUseqDEIT/VBuG+URXyCOuislHtlHG8t0gOkPqV380h9fEQnsv3cDwwwlSHZcjN6"
    "ZpMsPycOXZ0fJ949lCQIQMxfOpZCfGfT5HddbQ++j1pSsNbu84dmULcrUwZ6i2nHsxroLNxzfll9cYqoK6i0RSJKfetk9ivH+to7"
    "PdtbnMnzEd2ltRQZxvwyfliJrO2ZPCl8MkVKFBCGHyl81CZ8tOP7to5z9inZG7WyngXz1zIzkEqd2sKxq6SLviNGwfwcjl9FFDfO"
    "/4YjZt/TAeHTSbXQb3fHTlunllIqre8n5AOdP5WbXk+kq0xXkg4lkvARknApa2iMrJDgQk1P8snl9Gkyy9BBmE28RxqkhBNcNNBP"
    "nNpsCOy5SGD3OfJmSWewPczkzhioFVxDnXjL3lF6NYeLPEvPd2DYROJnsyKKJooLpwQpSRybPsZn/LKYLB3F78ltMaWvLQG9FwgY"
    "OBdWfFZYMU69tZ2oS+hTmGckxezBUnDOrXq8wmGFj8Tt39M/HOBeOkJ3Ame/EyUEyoBO6rsbembIfl2GkqEFojlLCRgLBaXNE21g"
    "QhPOTnEyJU4kLyxARWJIEPH2f9z++g4i910jSYwCjdrIv8Ug71rR854Ypw8T0dnRGuXTdq1Bd3S29UQf43nxtDV0Hai7vAS+weiq"
    "jfUBichgEO0LacqnlpJC9xT/hbKTxcQYRUlxyDr+NCCMjQYpzXwGxwezJeE/FJ9F5CJvzSOfnBNOSiGaQ+b3kbYOElDIdUt5JrPh"
    "6P0nGJk1UOmVElvMQHJPmmArHlSsaHaycpYXZyanU5zIa+kCeBWNc3QIzsCJF06LFmdOb9Gh8WZmVi6ckKRcuNanT7ot4UQwvScj"
    "u30GKjiBFe/evIfmoXZxyLIV0LWxLUwBXEHYBKjLjuGi+DQ3q+/viwmKwRiBcsXcIU5fZtIpGU6LTczu1q5IT+sjS1JDe5bOqlmL"
    "4SKeZ9WSDjkCLj6QNtMFgA3Uh0kG+wNIq/HnxdSsAOxe/fTICIWrYwjEJLUR+BYRlci9DxNc18D9qKagBfArdXCvYiiPol33zeBm"
    "DIb/vCeJI270Wl69eQRvskdkstPMEesPoZBjMeiLOkWYTE7+uhdGg9uIHk6/opmBCaeNQ8YRlJnsgpQxAxQ3DKmnMR6mvvsGd+J3"
    "heSR9kjX4yqWbDEhadOV7syD8tMZ2pGWW59Zo9N3XiGnUaeTHPSYXENa/QDHnw8jwJHy4YvHK5R+LWzpmQxnrXSCtwgCKDrTrK1l"
    "MWroipSzeUTsDaxTNMK3VZkFGZGMAdmCtzURvlpEnubm4JdiXuCdLXghSNH0CHUTtQTN2dgX8CJC1MgIK5po3EmyeLKrjdL48dus"
    "T/3hqy/+40/fUKf3STKaL9A/SgSNdARSM8CSKe4mSukFvkig9W7BUCZqYQMFSXOZ+RPNHc/Szfds00Q1Q0bC3EhpOgeahnaRxpZO"
    "qKqsvZyF/IYPzJwaljeTKW7PGKII+C2ltjATZa4timf66pYEB0ZaHUP+0AvoskQR0ENV/n5U5PfVdUJgKpzxzKGEuTpGFQvhgSoO"
    "khcwgvQVFkMhGY8ks7IXknlsi0fBuTl1YdopDXZnXu3bKs1/UOMpenxsuLiRA11nJsa/iXvkdrn3Yj5efEd2BpSlnJkGOMKJLT2+"
    "Hivur3GSbNgrLFjEC0N+9G64i33hhXx2GMqOn51QKq+mVulnPWuMmLE+takWSEYoWy8cybhE1oSpP9g0ig33pPeRvdi4prFOMC/G"
    "ll6lHGK2wL/JexYW8akxi/EDoxRlS4s4KSbbWUK9OIMQY30qPqKeGcDQsYFI+DELT7Q4dJpC5gcd03j7PxijE6jFeAuL4IkFndB2"
    "aPaQ8tD8xzahaUorpBZNH6Uqsa14xmyRyTIRrNg4pbcjW9yXmltWj/vJ28waPc3k5Am7ifq+j8xkbkBkbGyPcPE/3v7iK3LAzidR"
    "JH6PhVpTb0qbohYs/R5sFCkd4sXwGNMsyu0osS9LbYHDP1MF85kIJW8YhEkJtgaBCqSBtVTxl01tfIuc/WQFLcPnaBY5b06+pqBq"
    "NlvRgkR+5I+idjAmIV0be0sAM/ZW2xjEs/SRqBNw+9rSNpFFEEQUW9IxU9U08Cc88IVFa+AIRsJHeVyMwzKykEjmzeyyUIWayKAM"
    "rQ4e0PCnhsr88sa5xjEK0xChTFFC3+Zxkb0OLGo7m/AR0snJNsUIIBOzMMj8qK3DqBt1R/7u+lzPJH/n0iYmwXBl6YdWHXqi7e2u"
    "z/HH71zq1IieSaBruv6g00Xd95+ovYts5nT1h6dt0R4Xo6w62Q/mS1cftnW0dT9y8QWqTwFgHgJM77fCRjk2aJPTNF0JlBoWoSpt"
    "fgtjpYR+6EDslPsgvDLJv9WNt+rG8j+Pl1mLPZ2d9x8/bX10X6IWtSjqKNQWBfSlGHRINoMFMCqcG4UHngg9s4OVTYKIoz0UT0Xw"
    "IbbLLk1p78kK67870vbgfoQ5oogC21MyGMvOuKTCRl9K7ULvCD+E57Ov5MQ/6QKGP+0yRNV+SZTy8+WRSGgBiSRXokzJMb8GNzTK"
    "BJR2cGmTnHp2kHN27kRMhoAaEmrbmJWlLM+DDLmEJ7mc/joBYytBUSiuZfb1TFpGSTvG4SM2Pw6RjGsyEDE5YV4BQ9Jz70BLDF4n"
    "cP2BSdK37UKgm8dt3d3RB2yPMHGgG/Rm1D/QmZX/9TPAsRXxU/f/QtR0ZoGfoBxztcBuQmnPf37xP8Ta1cO2ru4ecau1FiIyPxaA"
    "Ft86Owxt4QNmQ/bNc4R5L+aIr+ensP7BgE1L4oNr9DO7jtKbGYCFfRXmMWMCxiguUXK4tMWUrVigygV+C1vbRJvQLiYTM8DWp2fy"
    "nGpSQSv+jkZuh/YHXZ1PSJMEGW9RjtCWlrWZ5xzfgVOUaoo5EzNhlDL1g2URIYTIQljD4HIvDKFQH0nHgGuyaICmiJnkRFWhf48M"
    "jvNfs9hLxgp3GpgwdEisr5uU7aEZWV9WV7D2/p6RU7I/thY5t9r4GkX2DcNboXrpKFbRBS7cv0eYNvy2OGCkal62Jf1DltbuzM7Y"
    "+LgzihMiMCAMUMEVVTRyfgG1ZvccLewFXwwHToWgwt34DFd/B9fZsta3mZXuJDh7YJBAiGMFiQJukkBXeIAMAi1ita+FXNsMbYxU"
    "5EQDH5GpogyBP3lmYKGcmFg3i1hvLX4KfwQUctMBVgg0KEvchjay7zn6cmCQg8AIoAoOTly0kjUyu2QtYjXQWqKQNmdo0ARatfCO"
    "QrEZ39jPJTUSKRR9ivqIsYp/gyi7lRnw6oJZBvnznS/u/M8/379z+3/dEeWMaKTrv/AAtVie4wDmjmALiHYaQytsZ7AHbpue+Iyf"
    "UJ9zpjNaSonieixfWpyR1BsWAQ8U+VG6+Cpv2HIEGY/ssX8PtsQrHeQXiHpmLLTfbfSGWymYbCwCjMGASe8gFGbMMSo9iAGR7h6j"
    "feiccwo5a1wwMsrZK4dIYrkB69HPLnqUH+JqLaGmCEdkjrZgYWroaU8XFr3Yj1D9EqGVh0klYEENuQN+4GHkMSq6hkMIk6+QmYlp"
    "aWGMlxCMRx63GVFenXhX8Yz+cQT+gt+l1Rx2I8h4QI89ajOeEs5pMS9ecIJ1CfRRp98iYJrx+WFUCshBH4FMlCCmbHdY8giXkBmm"
    "JRVSl90UFSfTp7bnpEAXPEaJoHwGJvyHL79mYmyV5Ol4BkwHacvkclbZe2ISqRUQz8zxEwIBFuUOFFrsGNwsvtjlcq9+sgVFcOUe"
    "w7UtJwHsqIfxLV4lMgiyoNuGt3NRH7hgc1jTlwlyuHjJpo+LMqKgmi55V+eTHk6HTV8wqidAamH11F7uHXREIRGJvDBgmL+xcBQf"
    "tvsRaX33jQXIQ3vq7iLxDzMhJ38X0pF9C6Imcs8DJhKU4YuoLxKS21//65df/3ex+tn6qLOzO2qYuHi+qoiy2qy+WEF4k7LS8iJB"
    "llFLEJrmQGkBPxankJceDUog5ZLKeaWWre3YFkxRUap4CmtvoqQjyh4Eo4yhnEFQsBjPko+bnwrSEC/FEjJwqCfH2s4G2+5f7ksl"
    "SAyHsqzkA32DkJU3YLihyMNbKoQpru4iIZJLbHI9n/VlVEQOB7XNRaGyCmGNcPAhC9vjdLAijlDhmAswg3vYMWJqhJ4GjRE8lbLH"
    "c3dRnR4J0REvm5Ofrr6k9TU8J3D+7LSAPJrJjqi6z1DCT+WCbXtUA1RqiWnB8EgaPbuGH/KfBWLdMDeq+IHRHxHES6rJLvGX+4bG"
    "qiuaJ3FinhfzyBbLRyCA5rFlrVSxNkMHlevBxyY2aTA7KcLqAwICZuqmYdL8ifHKihaTBNxF/0cQEwphcmfQotGClMdwvrm0uSTI"
    "OIDWhYVz6GYfYu+RZSVWLi2CcvVNFH+NnAjLYyIIEZAIgWl6no9Ra2JhX9taZV8mQ0hlbNo0tHGR2VY0KBVV0abYtUsraD02HBBz"
    "+wql6CSPkb0AaZY2vsmLOrwNixebhAUSyg6s2zK+MSSAzJYNBoRINTtiM1hjRsSyKS3pD85yGYeLZJbjzA8S4we8Y+/mz0+6Or+n"
    "/PAZz6swDkik5nMskenV1pjIsESwRiuSJ5wfimRX/w/gvhSv"
)
load_content(json.loads(zlib.decompress(base64.b64decode("".join(_DATA))).decode("utf-8")))


if __name__ == "__main__":
    _repl()
