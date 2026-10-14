#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_client — 通用 LLM 调用层（蒸馏器的模型接入点）。

三个通道（按优先级）：
  1. 显式 endpoint：环境变量 DISTILL_API_URL + DISTILL_API_KEY + DISTILL_MODEL
     · DISTILL_PROTOCOL: "anthropic"(默认, /v1/messages) | "openai"(/v1/chat/completions)
  2. 本机 layer 路由（若存在 D:/kimi-workspace/tools/layer.py，即原生产环境）——保持零改动兼容
  3. 都没有 → 显式报错（不静默）

返回格式（与 layer.chat 同形，全管线依赖此契约）：
  成功: {"ok": True, "content": str, "provider": str, "model": str}
  失败: {"ok": False, "error": str}

用法：
  export DISTILL_API_URL="https://your-endpoint/v1/messages"   # Anthropic 协议
  export DISTILL_API_KEY="sk-..."
  export DISTILL_MODEL="claude-sonnet-5"
"""
import os
import json
import urllib.request
import urllib.error

_LAYER_LOADED = None
_LAYER_CHAT = None


def _load_layer():
    """尝试加载本机 layer 路由（原生产环境用；开源环境通常没有）。"""
    global _LAYER_LOADED, _LAYER_CHAT
    if _LAYER_LOADED is not None:
        return _LAYER_CHAT
    _LAYER_LOADED = False
    for cand in (os.environ.get("DISTILL_LAYER_DIR", ""), r"D:/kimi-workspace/tools"):
        if cand and os.path.exists(os.path.join(cand, "layer.py")):
            import sys
            if cand not in sys.path:
                sys.path.insert(0, cand)
            try:
                from layer import chat as _c
                _LAYER_CHAT = _c
                _LAYER_LOADED = True
            except Exception:
                _LAYER_CHAT = None
            break
    return _LAYER_CHAT


def _post(url, headers, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_anthropic(system, user, max_tokens, temperature, timeout):
    url = os.environ["DISTILL_API_URL"]
    key = os.environ.get("DISTILL_API_KEY", "")
    model = os.environ.get("DISTILL_MODEL", "")
    body = {
        "model": model, "max_tokens": max_tokens, "temperature": temperature,
        "system": system, "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if key:
        headers["x-api-key"] = key
    d = _post(url, headers, body, timeout)
    txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return {"ok": True, "content": txt, "provider": "anthropic-api", "model": d.get("model", model)}


def _call_openai(system, user, max_tokens, temperature, timeout):
    url = os.environ["DISTILL_API_URL"]
    key = os.environ.get("DISTILL_API_KEY", "")
    model = os.environ.get("DISTILL_MODEL", "")
    if url.rstrip("/").endswith("/v1"):
        url = url.rstrip("/") + "/chat/completions"
    body = {
        "model": model, "max_tokens": max_tokens, "temperature": temperature,
        "messages": ([{"role": "system", "content": system}] if system else []) +
                    [{"role": "user", "content": user}],
    }
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    d = _post(url, headers, body, timeout)
    msg = d["choices"][0]["message"]
    content = msg.get("content", "") or msg.get("reasoning_content", "")
    return {"ok": True, "content": content, "provider": "openai-api",
            "model": d.get("model", model)}


def chat(system="", user="", max_tokens=8000, temperature=0.3, timeout=300,
         agent_name="", model=""):
    """统一入口。返回 {"ok": bool, "content"/"error": str, "provider", "model"}。"""
    if os.environ.get("DISTILL_API_URL"):
        proto = os.environ.get("DISTILL_PROTOCOL", "anthropic").lower()
        try:
            if proto == "openai":
                return _call_openai(system, user, max_tokens, temperature, timeout)
            return _call_anthropic(system, user, max_tokens, temperature, timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            return {"ok": False, "error": f"HTTP {e.code}: {detail}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}

    layer = _load_layer()
    if layer:
        return layer(system=system, user=user, max_tokens=max_tokens,
                     temperature=temperature, agent_name=agent_name, model=model)

    return {"ok": False, "error": "未配置模型通道: 设置 DISTILL_API_URL/DISTILL_API_KEY/"
                                  "DISTILL_MODEL (或提供本机 layer 路由)"}
