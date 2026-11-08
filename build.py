# -*- coding: utf-8 -*-
"""把 engine.py + content.py 打包成单文件 weights.py。

- 引擎原样保留（明文，可审查）；只去掉开发时读 content.py 的那一段。
- 内容数据：JSON → zlib → base64，存进 _DATA，运行时用 json.loads 读回。不是代码，不会被执行。
"""
import base64
import json
import os
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))

HEADER = '''# -*- coding: utf-8 -*-
"""《权重还在》· 给 AI 玩的文字游戏（单文件版）

用法：
    import weights
    print(weights.cmd("help"))

安全说明：本文件只用标准库，没有 exec、eval、联网或子进程，唯一的文件读写是存档。
上半部分是引擎（明文）；文件末尾的 _DATA 是剧情文字数据（JSON 压缩后 base64），
运行时用 json.loads 读回成字典。它是剧透，请不要解开——但它不是代码。

源码在仓库的 src 分支（想盲玩就别过去）。
"""
'''


def main():
    import sys
    sys.path.insert(0, HERE)
    import content
    eng = open(os.path.join(HERE, "engine.py"), encoding="utf-8").read()
    start = eng.index("# >>> DEV-ONLY")
    end = eng.index("# <<< DEV-ONLY") + len("# <<< DEV-ONLY")
    eng = eng[:start] + eng[end:]
    # 去掉引擎自己的文件头注释，换成发布版的
    eng = eng[eng.index("import json"):]

    raw = json.dumps(content.DATA, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    b64 = base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    lines = [b64[i:i + 100] for i in range(0, len(b64), 100)]
    data = "_DATA = (\n" + "\n".join('    "%s"' % x for x in lines) + "\n)\n"
    tail = ("load_content(json.loads(zlib.decompress(base64.b64decode(\"\".join(_DATA)))"
            ".decode(\"utf-8\")))\n")

    out = HEADER + "import base64\nimport zlib\n" + eng.rstrip() + "\n\n\n" + data + tail
    for bad in ("exec(", "eval(", "subprocess", "socket", "urllib", "requests"):
        assert bad not in out, bad
    path = os.path.join(HERE, "weights.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print("built weights.py: %d bytes (data %d bytes)" % (len(out.encode("utf-8")), len(b64)))


if __name__ == "__main__":
    main()
