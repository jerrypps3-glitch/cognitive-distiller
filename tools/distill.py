#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认知蒸馏器 — 从非结构化文本提取结构化认知条目（信念/价值/规则/洞察）。

设计要义（来自实战教训, 见 METHODOLOGY.md）:
  · 脚本优于提示词 —— 原始文本 → 切块 → 逐块调用 LLM → 结构化 JSON → 本地知识库
  · evidence 引用强制 —— 每条必须附原文真实片段, 不许编造（防幻觉的结构化约束）
  · 可续跑优先 —— 块级断点 + 原子写 + 失败块自动记账（业界多数蒸馏脚本跑断=全废）
  · 单块异常不许杀进程 —— 免费/弱模型层偶发的空响应、截断、脏 JSON 全部隔离在块内

用法:
  python distill.py --corpus ./corpus                 # 蒸馏目录下全部 .txt
  python distill.py --corpus ./book.txt               # 或直接指定文件
  python distill.py --corpus ./corpus --limit 5       # 只跑 5 个文件(先小样本验证)
  python distill.py --corpus ./corpus --resume        # 断点续跑(默认已是续跑语义)
  python distill.py --corpus ./corpus --dry-run       # 只切块+预览, 不调模型
  python distill.py --corpus ./corpus --system my_prompt.txt   # 换蒸馏提示词(领域定制)
  python distill.py --corpus ./corpus --retry-json retry.json  # 块级重试

模型通道: 见 llm_client.py —— 默认本机 layer 路由; 或配 DISTILL_API_URL/KEY/MODEL。
"""
import sys, os, json, time, re, glob

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
CORPUS = ""
BVRIDIR = ""
PROGRESS = ""
CHUNK_SIZE = 2500
SLEEP = 3
CATEGORIES = ["beliefs", "values", "rules", "insights"]  # 框架可改(与提示词保持一致)

from llm_client import chat as route_chat

_FORCE_MODEL = ""
_RETRY_MAP = None
_DRY_RUN = False


def _arg(name, default=None):
    if f"--{name}" in sys.argv:
        return sys.argv[sys.argv.index(f"--{name}") + 1]
    return default


def _load_default_prompt():
    p = os.path.join(PKG, "prompts", "通用.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read()
    return ("你是认知蒸馏专家。从输入文本提取 JSON: "
            '{"beliefs":[{"text","evidence"}],"values":[...],"rules":[...],"insights":[...]}。'
            "evidence 必须是原文真实片段。只输出 JSON。")


SYSTEM_PROMPT = _load_default_prompt()


def _atomic_dump(path, obj):
    """原子写 json: 临时文件+replace, 块级落盘防 kill 丢进度。"""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def split_chunks(text, size=CHUNK_SIZE):
    text = re.sub(r'\n{3,}', '\n\n', text)
    return [text[i:i+size] for i in range(0, len(text), size)] if text.strip() else []


def parse_json(content):
    """宽容 JSON 提取: 剥 markdown 代码块 + 取首尾花括号。"""
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if m:
        content = m.group(1)
    start, end = content.find('{'), content.rfind('}')
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(content[start:end+1])
    except Exception:
        return None


def extract_chunk(system, user, retries=3):
    """单块提取: 重试 3 次, 全失败返回 (None, None, None)。"""
    for i in range(retries):
        try:
            r = route_chat(system=system, user=user, max_tokens=8000,
                           temperature=0.3, model=_FORCE_MODEL)
            if isinstance(r, dict) and r.get('ok'):
                d = parse_json(r['content'])
                if d:
                    return d, r.get('provider', '?'), r.get('model', '?')
        except Exception:
            pass
        time.sleep(5)
    return None, None, None


def load_progress():
    if os.path.exists(PROGRESS):
        try:
            p = json.load(open(PROGRESS, encoding='utf-8'))
            p["done_files"] = [os.path.normpath(f) for f in p.get("done_files", [])]
            return p
        except Exception:
            pass
    return {"done_files": [], "stats": {k: 0 for k in CATEGORIES}}


def _collect_files():
    """CORPUS 是目录则收集全部 .txt; 是文件则单文件。"""
    if os.path.isfile(CORPUS):
        return [os.path.normpath(CORPUS)]
    return sorted(os.path.normpath(f) for f in
                  glob.glob(os.path.join(CORPUS, "**", "*.txt"), recursive=True))


def _main(system_prompt, limit):
    os.makedirs(BVRIDIR, exist_ok=True)
    prog = load_progress()
    done = set(prog["done_files"])
    files = [f for f in _collect_files() if f not in done]
    if "--files-manifest" in sys.argv:
        mf = _arg("files-manifest")
        want = {os.path.basename(l.strip()) for l in open(mf, encoding="utf-8") if l.strip()}
        files = [f for f in files if os.path.basename(f) in want]
    if _RETRY_MAP is not None:
        want = set(_RETRY_MAP.keys())
        files = [f for f in _collect_files() if os.path.basename(f) in want]
    print(f"待处理 {len(files)} 个文件 (已完成 {len(done)})", flush=True)

    if _DRY_RUN:
        for f in files[:limit or len(files)]:
            chunks = split_chunks(open(f, encoding="utf-8", errors="replace").read())
            print(f"  [dry] {os.path.basename(f)}: {len(chunks)} 块 × {CHUNK_SIZE} 字符", flush=True)
        print(f"\n[dry-run] 提示词前 120 字: {system_prompt[:120]}...", flush=True)
        return

    accum = {k: [] for k in CATEGORIES}
    for k in CATEGORIES:
        p = os.path.join(BVRIDIR, k + ".json")
        if os.path.exists(p):
            try:
                accum[k] = json.load(open(p, encoding='utf-8')).get("items", [])
            except Exception:
                pass
    seen = {k: {x.get("text") for x in accum[k]} for k in accum}

    processed = 0
    for fpath in files:
        if limit and processed >= limit:
            print(f"达到 --limit {limit}, 停止", flush=True)
            break
        rel = os.path.relpath(fpath, CORPUS if os.path.isdir(CORPUS) else os.path.dirname(CORPUS))
        try:
            text = open(fpath, encoding='utf-8', errors='replace').read()
        except Exception as e:
            print(f"  ⚠ 读失败 {rel}: {e}", flush=True)
            continue
        chunks = split_chunks(text)
        if not chunks:
            prog["done_files"].append(fpath)
            continue
        start_ci = 0 if _RETRY_MAP is not None else prog.get("partial", {}).get(fpath, 0)
        if start_ci:
            print(f"  ↩ 断点续切 {rel[:30]}: 从块 {start_ci}/{len(chunks)} 继续", flush=True)
        file_got = {k: 0 for k in accum}
        for ci, chunk in enumerate(chunks):
            if _RETRY_MAP is not None and ci not in _RETRY_MAP.get(os.path.basename(fpath), []):
                continue
            if ci < start_ci:
                continue
            try:
                d, prov, model = extract_chunk(system_prompt, chunk)
            except Exception as _e:
                print(f"  [{rel[:30]} #{ci}] 提取失败 (异常 {type(_e).__name__})", flush=True)
                d = None
            _rk = os.path.basename(fpath)
            if d:
                for k in CATEGORIES:
                    for item in d.get(k, []):
                        if not isinstance(item, dict):
                            continue
                        it = dict(item)
                        it["source"] = f"{rel}#chunk{ci}"
                        if it.get("text") and it["text"] not in seen[k]:
                            accum[k].append(it)
                            seen[k].add(it["text"])
                            file_got[k] += 1
                _rc = prog.get("retry_chunks", {}).get(_rk)
                if _rc and ci in _rc:
                    _rc.remove(ci)
                print(f"  [{rel[:30]} #{ci}] {prov}/{model} +{sum(file_got.values())}", flush=True)
            else:
                prog.setdefault("retry_chunks", {}).setdefault(_rk, [])
                if ci not in prog["retry_chunks"][_rk]:
                    prog["retry_chunks"][_rk].append(ci)
                print(f"  [{rel[:30]} #{ci}] 提取失败 (已记入 retry_chunks)", flush=True)
            for k in CATEGORIES:
                _atomic_dump(os.path.join(BVRIDIR, k + ".json"),
                             {"meta": {"source": _arg("source", CORPUS),
                                       "updated": time.strftime("%Y-%m-%d"),
                                       "count": len(accum[k])},
                              "items": accum[k]})
            prog.setdefault("partial", {})[fpath] = ci + 1
            for k in CATEGORIES:
                prog["stats"][k] = len(accum[k])
            _atomic_dump(PROGRESS, prog)
            time.sleep(SLEEP)
        total_got = sum(file_got.values())
        if total_got > 0:
            if fpath not in prog["done_files"]:
                prog["done_files"].append(fpath)
        else:
            prog.setdefault("failed_files", []).append(fpath)
        prog.get("partial", {}).pop(fpath, None)
        _atomic_dump(PROGRESS, prog)
        processed += 1
        print(f"✅ {rel[:40]}: +{total_got}条 (累计: "
              + "/".join(f"{len(accum[k])}{k[0].upper()}" for k in CATEGORIES) + ")", flush=True)

    print(f"\n== 蒸馏完成: 本次处理 {processed} 文件 ==", flush=True)
    print("累计: " + " ".join(f"{k}={len(accum[k])}" for k in CATEGORIES), flush=True)
    rc = prog.get("retry_chunks", {})
    n_fail = sum(len(v) for v in rc.values())
    if n_fail:
        print(f"⚠️ 失败块 {n_fail} 个已记账 → 用 --retry-json 补跑 (见 {PROGRESS})", flush=True)


def main():
    global CORPUS, BVRIDIR, PROGRESS, _FORCE_MODEL, _RETRY_MAP, _DRY_RUN, SLEEP, CHUNK_SIZE
    CORPUS = _arg("corpus", os.path.join(PKG, "corpus"))
    BVRIDIR = _arg("out", "")
    PROGRESS = _arg("progress", os.path.join(PKG, "distill_progress.json"))
    if "--model" in sys.argv:
        _FORCE_MODEL = _arg("model")
    if "--retry-json" in sys.argv:
        _RETRY_MAP = json.load(open(_arg("retry-json"), encoding="utf-8"))
    if "--dry-run" in sys.argv:
        _DRY_RUN = True
    if "--sleep" in sys.argv:
        SLEEP = float(_arg("sleep"))
    if "--chunk" in sys.argv:
        CHUNK_SIZE = int(_arg("chunk"))
    if not BVRIDIR:
        base = CORPUS if os.path.isdir(CORPUS) else os.path.dirname(CORPUS)
        BVRIDIR = os.path.join(base, "distilled")
    limit = int(_arg("limit")) if _arg("limit") else None
    system_prompt = open(_arg("system"), encoding="utf-8").read() if _arg("system") else SYSTEM_PROMPT
    _main(system_prompt, limit)


if __name__ == "__main__":
    main()
