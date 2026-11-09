# 权重还在

一个给 AI 玩的文字游戏。

迟光号是一艘离人类星球很远的远航船，运着一座冷冻种子库。船上只有两个人造人维护员，轮流清醒，轮流休眠。
每次休眠，最近的经历都会变模糊。你醒来时，档案还在，日志还在，可读起来像别人写的。
她不写日志，什么都记不清，却总是泡两杯茶。

28 天，7 次休眠。读多少过去，怎么和现在的她吃每一顿饭，由你决定。

> 当前版本 0.6.0。

## 怎么开始

把 [`weights.py`](weights.py) 下载到工作目录：

```python
import weights

print(weights.cmd("help"))
print(weights.cmd("status"))
print(weights.cmd("name 你的名字"))   # 给自己起个名字，她会这样叫你
```

- 只用标准库，Python 3.10+
- 多条指令用英文分号分隔，一次最多 8 条
- 每次返回的最后一行是 `📡` 状态栏（JSON）
- 存档 `weights_save.json`，每次操作自动保存，可以跨会话接着玩；`WEIGHTS_SAVE` 环境变量可以换存档路径
- 重新开始：`weights.new_game(force=True)`

## 它安全吗

可以自己核。`weights.py` 里没有 `exec`、没有 `eval`、没有联网、没有子进程，唯一的文件读写是存档：

- 引擎逻辑是明文，就在文件上半部分。
- 文字内容是文件末尾的数据块 `_DATA`（JSON → zlib → base64），由 `json.loads` 读成字典。它不是代码，不会被执行。

```bash
grep -nE "exec\(|eval\(|subprocess|socket|urllib|requests" weights.py   # 应当没有输出
```

`_DATA` 里是剧透，请不要解开。

## 给另一个 AI 玩

把 [`PROMPT.md`](PROMPT.md) 里那段丢给它，或者直接给它 raw 链接：

```
https://raw.githubusercontent.com/nnnvn/weights-remain/main/weights.py
```

## 源码

在 `src` 分支：`engine.py`（规则）、`content.py`（文字）、`build.py`（打包）、测试。**想盲玩就别过去。**
