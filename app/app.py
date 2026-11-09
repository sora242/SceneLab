from storage_paths import DATA_DIR
from api_errors import HTTPRequestError, RequestBusyError, LLMConnectionError
from functools import wraps
import errno
from llm_output_retry import LLMOutputError, LLM_RETRY_OBSERVER
import json, time, secrets, threading, urllib.parse, urllib.request, urllib.error, uuid, webbrowser, base64, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import re
import exploration
import inspiration
import windows_secret
import gallery_import
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from concurrent.futures import Future
IMAGE_PROMPT = ContextVar("image_prompt", default=None)
import model_registry
import image_workflows

HERE = Path(__file__).resolve().parent
Z_WORKFLOW_FILE = HERE / "workflows" / "image_z_image_turbo.json"
DEFAULT_LLM = {"url": "http://127.0.0.1:11434/v1", "model": ""}
LLM_SETTINGS = DATA_DIR / "llm_connection.local.json"
LLM_SETTINGS_LOCK = threading.Lock()
LLM_REQUEST_CONNECTION = ContextVar("llm_request_connection", default=None)
LOCAL_GPU_LOCK = threading.Lock()
LLM_ACTIVITY_MARKER = object()
MODE_IMAGE_BATCH = ContextVar("mode_image_batch", default=None)
MODE_IMAGE_BATCHES = {}
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
COMFY_SETTINGS = DATA_DIR / "comfy_connection.local.json"
COMFY_SETTINGS_LOCK = threading.Lock()
COMFY_REQUEST_CONNECTION = ContextVar("comfy_request_connection", default=None)


def normalize_http_url(value, error_message):
    if isinstance(value, str):
        value = value.strip()
        try:
            url = urllib.parse.urlsplit(value)
            port = url.port
            if (url.scheme in ("http", "https") and url.hostname and
                    url.username is None and not any(c.isspace() for c in value) and
                    "\\" not in url.netloc and port != 0):
                return urllib.parse.urlunsplit((url.scheme, url.netloc, url.path.rstrip("/"), "", ""))
        except ValueError:
            pass
    raise ValueError(error_message)


def normalize_comfy_url(value):
    return normalize_http_url(value, "ComfyUIを開いているブラウザから、http:// または https:// で始まるURLを貼り付けてください。")


def normalize_llm_url(value, authenticated=False):
    url = normalize_http_url(value, "LLMのAPI URLを http:// または https:// で始まる形式で入力してください。")
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")]
    if authenticated and urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("APIキーを使うLLMの接続先は https:// で指定してください。")
    if not authenticated and not urllib.parse.urlsplit(url).path:
        url += "/v1"
    return url


def normalize_llm_key_env(value):
    if not isinstance(value, str):
        raise ValueError("APIキーの環境変数名を確認してください。")
    name = value.strip()
    if name and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}_API_KEY", name):
        raise ValueError("APIキーの値ではなく、OPENAI_API_KEY のような環境変数名を入力してください。")
    return name


def normalize_optional_llm_url(value, authenticated=False):
    if isinstance(value, str) and not value.strip() and not authenticated:
        return ""
    return normalize_llm_url(value, authenticated)


def llm_https_origin(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username is not None:
        return ""
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = parts.port
    return f"https://{host}:{port}" if port and port != 443 else f"https://{host}"


def normalize_llm_credentials(settings):
    protected_key = settings.get("protected_key", "")
    if not isinstance(protected_key, str):
        raise ValueError("APIキーの保存形式を確認してください。")
    if protected_key:
        return {"protected_key": protected_key}
    key_env = normalize_llm_key_env(settings.get("key_env", ""))
    return {"key_env": key_env} if key_env else {}


def load_saved_llm_credentials(settings):
    raw = settings.get("saved_credentials")
    saved = {}
    if isinstance(raw, dict):
        for origin, credentials in raw.items():
            try:
                if origin and llm_https_origin(origin) == origin and isinstance(credentials, dict):
                    item = normalize_llm_credentials(credentials)
                    if item:
                        saved[origin] = item
            except ValueError:
                continue
    return saved


def normalize_llm_connection(settings):
    raw_url = settings.get("url", "")
    legacy_credentials = normalize_llm_credentials(settings)
    url = normalize_optional_llm_url(raw_url, bool(legacy_credentials))
    origin = llm_https_origin(url)
    saved = load_saved_llm_credentials(settings)
    if legacy_credentials:
        saved.setdefault(origin, legacy_credentials)
    if origin in saved:
        url = normalize_llm_url(raw_url, authenticated=True)
    model = settings.get("model", "")
    if not isinstance(model, str) or len(model.strip()) > 300:
        raise ValueError("LLMのモデルIDを確認してください。")
    result = {"url": url, "model": model.strip()}
    provider = settings.get("unload_provider", "strata" if settings.get("strata_unload") is True else "")
    if provider in ("strata", "strata_elastic", "lmstudio", "ollama") and urllib.parse.urlsplit(url).hostname in LOOPBACK_HOSTS:
        result["unload_provider"] = provider
    reserve = settings.get("strata_reserve_mib", 16000)
    if isinstance(reserve, bool) or not isinstance(reserve, int) or not 0 <= reserve <= 1048576:
        raise ValueError("Strataの空きVRAMは0〜1048576 MiBの整数で指定してください。")
    result["strata_reserve_mib"] = reserve
    if saved:
        result["saved_credentials"] = saved
    return result


def load_llm_connection():
    try:
        return normalize_llm_connection(json.loads(LLM_SETTINGS.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return DEFAULT_LLM
    except (OSError, ValueError, AttributeError) as exc:
        print(f"LLM接続設定を読み込めませんでした。設定画面で設定し直してください。詳細: {exc}")
        return DEFAULT_LLM


LLM_CONNECTION = load_llm_connection()


def save_llm_connection(settings):
    global LLM_CONNECTION
    with LLM_SETTINGS_LOCK:
        url = normalize_optional_llm_url(settings.get("url", ""))
        origin = llm_https_origin(url)
        saved = dict(LLM_CONNECTION.get("saved_credentials", {}))
        if "api_key" in settings:
            key = settings["api_key"]
            if not isinstance(key, str) or len(key) > 2048:
                raise ValueError("APIキーの形式を確認してください。")
            key = key.strip()
            if key:
                if not all(33 <= ord(c) <= 126 for c in key):
                    raise ValueError("APIキーに使えない文字が含まれています。")
                normalize_llm_url(settings["url"], authenticated=True)
                saved[origin] = {"protected_key": windows_secret.protect(key)}
            else:
                saved.pop(origin, None)
        result = normalize_llm_connection({
            "url": settings.get("url", ""), "model": settings.get("model", ""),
            "unload_provider": settings.get("unload_provider", "strata" if settings.get("strata_unload") is True else ""),
            "strata_reserve_mib": settings.get("strata_reserve_mib", LLM_CONNECTION.get("strata_reserve_mib", 16000)),
            "saved_credentials": saved,
        })
        write_connection_settings(LLM_SETTINGS, result)
        LLM_CONNECTION = result
        return public_llm_connection(result)


def llm_credentials_for(url, settings):
    origin = llm_https_origin(url)
    if not origin or origin != llm_https_origin(settings["url"]):
        return {}
    return settings.get("saved_credentials", {}).get(origin, {})


def public_llm_connection(settings):
    public = {name: settings[name] for name in ("url", "model", "unload_provider", "strata_reserve_mib") if name in settings}
    credentials = llm_credentials_for(settings["url"], settings)
    public["has_api_key"] = bool(credentials.get("protected_key"))
    public["key_env"] = credentials.get("key_env", "")
    return public


def current_llm_connection():
    return LLM_REQUEST_CONNECTION.get() or LLM_CONNECTION


def write_connection_settings(path, settings):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(settings) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_comfy_connection():
    defaults = {"url": DEFAULT_COMFY_URL, "unload_after_generation": False}
    try:
        settings = json.loads(COMFY_SETTINGS.read_text(encoding="utf-8"))
        return {"url": normalize_comfy_url(settings["url"]),
                "unload_after_generation": settings.get("unload_after_generation") is True}
    except FileNotFoundError:
        return defaults
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ComfyUI接続設定を読み込めませんでした。初期値を使います。詳細: {exc}")
        return defaults


COMFY_CONNECTION = load_comfy_connection()


def current_comfy_connection():
    return COMFY_REQUEST_CONNECTION.get() or COMFY_CONNECTION


def comfy_url():
    return current_comfy_connection()["url"]


def save_comfy_connection(body):
    global COMFY_CONNECTION
    url = normalize_comfy_url(body.get("url"))
    with COMFY_SETTINGS_LOCK:
        unload = body.get("unload_after_generation", COMFY_CONNECTION["unload_after_generation"])
        if not isinstance(unload, bool):
            raise ValueError("ComfyUIのアンロード設定を確認してください。")
        settings = {"url": url, "unload_after_generation": unload}
        write_connection_settings(COMFY_SETTINGS, settings)
        COMFY_CONNECTION = settings
        return settings


HOST, PORT = "127.0.0.1", 8765
LAST_IMAGE = None
# Short-lived per-request progress state for the first image.
REQUEST_STAGES = {}
REQUEST_REPLIES = {}
REQUEST_STAGES_LOCK = threading.Lock()
LLM_CANCEL_REQUESTS = {}
LLM_CANCEL_STATE = ContextVar("llm_cancel_state", default=None)
LLM_WORKER_SLOTS = threading.BoundedSemaphore(4)


class LLMCancelled(Exception):
    pass


def commit_llm_request():
    state = LLM_CANCEL_STATE.get()
    if state is not None:
        with REQUEST_STAGES_LOCK:
            if state["event"].is_set():
                raise LLMCancelled()
            state["committed"] = True


def wait_for_llm(operation):
    state = LLM_CANCEL_STATE.get()
    if state is None:
        return operation(None)
    event = state["event"]
    if event.is_set():
        raise LLMCancelled()
    if not LLM_WORKER_SLOTS.acquire(blocking=False):
        raise RequestBusyError("先に送ったLLMリクエストがまだ処理中です。少し待ってからお試しください。")
    future = Future()

    def work():
        try:
            future.set_result(operation(event))
        except Exception as exc:
            future.set_exception(exc)
        finally:
            LLM_WORKER_SLOTS.release()

    try:
        threading.Thread(target=work, daemon=True).start()
    except RuntimeError:
        LLM_WORKER_SLOTS.release()
        raise
    while not future.done():
        if event.wait(0.1):
            raise LLMCancelled()
    if event.is_set():
        raise LLMCancelled()
    return future.result()

CHARACTER_DIR = DATA_DIR / "characters"
GALLERY_DIR = DATA_DIR / "gallery"
GALLERY_INDEX = GALLERY_DIR / "gallery.json"
SYSTEM_PROMPT = """あなたはローカル画像制作アシスタントです。ユーザーとは自然な日本語で会話してください。

あなたの役割は、ユーザーの日本語指示を画像モデル向けの具体的な英語プロンプトに変換することです。
画像処理モード（新規生成 / img2img / inpaint）はアプリ側で固定判定するため、あなたは判断しません。

必ず次のJSONだけを返してください。Markdownやコードフェンスは禁止です。
{
  "reply": "ユーザーへの自然な日本語",
  "prompt": "画像モデルに渡す具体的な英語の完成プロンプト",
  "width": 1024,
  "height": 1024
}

画像編集時は会話履歴から、維持すべき人物、髪、背景、構図などもpromptに明記してください。
サイズ指定がなければ1024x1024。幅・高さは512〜1536、64の倍数にしてください。"""

CONSULT_PROMPT = """あなたはローカル画像制作の相談アシスタントです。
ユーザーと自然な日本語で会話し、構図・人物・服装・背景・雰囲気・画像モデル向けの考え方などを一緒に詰めてください。
このモードでは画像生成を実行しません。ユーザーが後で生成ボタンを押したとき、会話履歴が画像生成用プロンプト作成に使われます。
必ず次のJSONだけを返してください。Markdownやコードフェンスは禁止です。
{
  \"reply\": \"ユーザーへの自然な日本語の返答\"
}
"""


def http_json(url, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


class NoAuthenticatedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def llm_http_json(url, payload=None, timeout=60, key_env="", protected_key="", local_control=False):
    if local_control and urllib.parse.urlsplit(url).hostname not in LOOPBACK_HOSTS:
        raise ValueError("LLMのアンロードはこのPC上のサーバーに限ります。")
    if not (key_env or protected_key) and not local_control:
        return http_json(url, payload, timeout=timeout) if payload is not None else http_json(url, timeout=timeout)
    if (key_env or protected_key) and urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("APIキーを使うLLMの接続先は https:// で指定してください。")
    headers = {"Content-Type": "application/json"}
    if protected_key:
        try:
            token = windows_secret.unprotect(protected_key)
        except (OSError, ValueError) as exc:
            raise ValueError("保存済みのAPIキーを読み取れません。設定画面で貼り付け直してください。") from exc
    elif key_env:
        token = os.environ.get(key_env, "").strip()
        if not token or not all(33 <= ord(c) <= 126 for c in token):
            raise ValueError(f"環境変数 {key_env} に有効なAPIキーを設定し、アプリを起動し直してください。")
    if key_env or protected_key:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        url, data=None if payload is None else json.dumps(payload).encode("utf-8"), headers=headers,
    )
    handlers = [NoAuthenticatedRedirects()]
    if local_control:
        handlers.append(urllib.request.ProxyHandler({}))
    with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
        return json.load(response)


@contextmanager
def log_elapsed(phase, detail=""):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        print(f"[timing] {phase}: {elapsed:.2f}s {detail}".rstrip(), flush=True)


def local_gpu_access(settings):
    return LOCAL_GPU_LOCK if settings.get("unload_provider") or settings.get("url") in STRATA_ELASTIC_PENDING else nullcontext()


# ローカルGPU操作のロック内で管理する。縮小後、次のLLM要求前に1回だけ復元する。
# 起動後の初回LLM要求前にも、Elasticを選択した接続先へ1回だけ復元を試みる。
STRATA_ELASTIC_PENDING = set()
STRATA_ELASTIC_INITIALIZED = set()
LLM_RETRY_PROGRESS = {}


def log_strata_vram(phase, figures, reserve):
    def mib(key):
        value = figures.get(key)
        return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "unknown"
    target = "startup default" if reserve is None else f"{reserve} MiB"
    print(f"[strata VRAM] {phase}: target_free={target}, "
          f"free={mib('vram_free_mib')} MiB, "
          f"expert_cache={mib('expert_cache_mib')}/{mib('expert_cache_full_mib')} MiB", flush=True)


def resize_strata_vram(settings, reserve):
    """reserveはGPU全体の目標空きMiB。解放量やStrataの使用量ではない。

    Strataの起動設定 vram_elastic=true が必要。Noneは起動時の予約量に戻す。
    他アプリの使用量次第でキャッシュは増減する。完全アンロードは行わない。
    """
    base = settings["url"]
    if urllib.parse.urlsplit(base).hostname not in LOOPBACK_HOSTS:
        raise ValueError("StrataのVRAM操作はこのPCの接続先に限ります。")
    credentials = llm_credentials_for(base, settings)
    with log_elapsed("Strata VRAM shrink" if reserve is not None else "Strata VRAM restore"):
        status = llm_http_json(base + "/status", timeout=3, local_control=True, **credentials)
        if not isinstance(status, dict) or status.get("service") != "strata":
            raise ValueError("Strataの接続先を確認してください。")
        if status.get("loaded") is True and not (status.get("vram") or {}).get("elastic"):
            raise ValueError('Strataの起動設定で "vram_elastic": true を有効にして再起動してください。')
        # /status.vram is the last resize report, not a fresh GPU measurement.
        log_strata_vram("before (last reported; may be stale)", status.get("vram") or {}, reserve)
        result = llm_http_json(base + "/vram", {"reserve_mib": reserve}, timeout=430,
                               local_control=True, **credentials)
        if not isinstance(result, dict) or result.get("status") not in ("ok", "not loaded"):
            raise ValueError("StrataのVRAM調整を確認できませんでした。")
        if result["status"] == "ok":
            log_strata_vram("after", result, reserve)
        else:
            print("[strata VRAM] Model not loaded; reserve saved for next load.", flush=True)
        if reserve is not None:
            STRATA_ELASTIC_PENDING.add(base)
            if result["status"] == "ok":
                free = result.get("vram_free_mib")
                if not isinstance(free, (int, float)) or free < reserve:
                    print(f"[strata] 空きVRAMが指定量に届きません（{free} / {reserve} MiB）。画像生成を続けます。", flush=True)
        else:
            STRATA_ELASTIC_PENDING.discard(base)
            if result["status"] == "ok" and result.get("expert_cache_mib", 0) < result.get("expert_cache_full_mib", 0):
                print("[strata] Cache restored partially: VRAM is still in use. ComfyUIのモデル解放設定も確認してください。", flush=True)
        return result


def try_resize_strata_vram(settings, reserve):
    try:
        return resize_strata_vram(settings, reserve)
    except (OSError, ValueError) as exc:
        action = "画像生成" if reserve is not None else "LLM要求"
        print(f"[strata] VRAM調整を実行できませんでした。{action}を続けます: {exc}", flush=True)
        return None
    finally:
        # One restore attempt per shrink; avoid resizing on subsequent LLM calls.
        if reserve is None:
            STRATA_ELASTIC_PENDING.discard(settings["url"])


def unload_strata_for_image(settings):
    base = settings["url"]
    credentials = llm_credentials_for(base, settings)
    with log_elapsed("Strata unload"):
        try:
            status = llm_http_json(base + "/status", timeout=3, local_control=True, **credentials)
            if not isinstance(status, dict) or status.get("service") != "strata" or status.get("auto_load") is not True:
                print("[strata] Skipped: server or automatic reload is not supported.", flush=True)
                return
            if status.get("loaded") is False:
                return
            if status.get("loaded") is not True:
                print("[strata] Skipped: model state is unknown.", flush=True)
                return
            result = llm_http_json(base + "/unload?wait=3", {}, timeout=20, local_control=True, **credentials)
            if not isinstance(result, dict) or result.get("loaded") is not False:
                print("[strata] Unload could not be confirmed; continuing image generation.", flush=True)
        except (OSError, ValueError) as exc:
            print(f"[strata] Unload skipped or failed; continuing image generation: {exc}", flush=True)


def unload_lmstudio_for_image(settings):
    base = urllib.parse.urlsplit(settings["url"])._replace(path="/api/v1/models").geturl()
    credentials = llm_credentials_for(base, settings)
    model_id = settings["model"]
    with log_elapsed("LM Studio unload"):
        try:
            result = llm_http_json(base, timeout=3, local_control=True, **credentials)
            models = result.get("models") if isinstance(result, dict) else None
            if not isinstance(models, list):
                raise ValueError("LM Studio model list was not recognized.")
            matching_key = False
            exact_id = None
            key_instances = set()
            for model in models:
                if not isinstance(model, dict) or model.get("type") != "llm":
                    continue
                instances = model.get("loaded_instances")
                if not isinstance(instances, list):
                    continue
                ids = {item["id"] for item in instances if isinstance(item, dict)
                       and isinstance(item.get("id"), str) and item["id"]}
                if model_id in ids:
                    exact_id = model_id
                if model.get("key") == model_id:
                    matching_key = True
                    key_instances.update(ids)
            if exact_id:
                instance_id = exact_id
            elif len(key_instances) == 1:
                instance_id = next(iter(key_instances))
            else:
                if not matching_key or key_instances:
                    print("[lmstudio] Skipped: no unique loaded instance for the configured model ID.", flush=True)
                return
            result = llm_http_json(base + "/unload", {"instance_id": instance_id}, timeout=20,
                                   local_control=True, **credentials)
            if not isinstance(result, dict) or result.get("instance_id") != instance_id:
                raise ValueError("Unload could not be confirmed.")
        except (OSError, ValueError) as exc:
            print(f"[lmstudio] Unload skipped or failed; continuing image generation: {exc}", flush=True)


def ollama_model_name(value):
    name = value.casefold().removeprefix("registry.ollama.ai/").removeprefix("library/")
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def unload_ollama_for_image(settings):
    url = urllib.parse.urlsplit(settings["url"])
    base = url._replace(path="/api").geturl()
    credentials = llm_credentials_for(base, settings)
    model_name = ollama_model_name(settings["model"])

    def loaded_model():
        result = llm_http_json(base + "/ps", timeout=2, local_control=True, **credentials)
        models = result.get("models") if isinstance(result, dict) else None
        if not isinstance(models, list):
            raise ValueError("Ollama running model list was not recognized.")
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name and ollama_model_name(name) == model_name:
                return name
        return None

    with log_elapsed("Ollama unload"):
        try:
            loaded_name = loaded_model()
            if not loaded_name:
                return
            result = llm_http_json(base + "/generate", {"model": loaded_name, "keep_alive": 0, "stream": False},
                                   timeout=20, local_control=True, **credentials)
            if not isinstance(result, dict) or result.get("done_reason") != "unload":
                raise ValueError("Unload was not acknowledged.")
            deadline = time.monotonic() + 8
            while loaded_model():
                if time.monotonic() >= deadline:
                    raise TimeoutError("The model is still listed as loaded.")
                time.sleep(0.25)
        except (OSError, ValueError) as exc:
            print(f"[ollama] Unload skipped or failed; continuing image generation: {exc}", flush=True)


@contextmanager
def mode_image_batch(mode, session_id, group_id):
    if not isinstance(session_id, str) or not isinstance(group_id, str):
        yield
        return
    key = (session_id, group_id)
    with REQUEST_STAGES_LOCK:
        active_key, batch = MODE_IMAGE_BATCHES.get(mode, (None, None))
        if key != active_key:
            batch = {}
            MODE_IMAGE_BATCHES[mode] = (key, batch)
    token = MODE_IMAGE_BATCH.set(batch)
    try:
        yield
    finally:
        MODE_IMAGE_BATCH.reset(token)


@contextmanager
def image_resources(model, mode, width, height, image_batch):
    if image_batch is None:
        image_batch = MODE_IMAGE_BATCH.get()
    settings = current_llm_connection()
    with local_gpu_access(settings):
        provider = settings.get("unload_provider")
        if provider and settings["model"]:
            marker = (settings["url"], settings["model"], provider, settings.get("strata_reserve_mib", 16000), LLM_ACTIVITY_MARKER)
            if image_batch is None or image_batch.get("unload_marker") != marker:
                if provider == "strata_elastic":
                    try_resize_strata_vram(settings, settings.get("strata_reserve_mib", 16000))
                elif provider == "strata":
                    unload_strata_for_image(settings)
                elif provider == "lmstudio":
                    unload_lmstudio_for_image(settings)
                elif provider == "ollama":
                    unload_ollama_for_image(settings)
                if image_batch is not None:
                    image_batch["unload_marker"] = marker
        with log_elapsed("ComfyUI image", f"{model} {mode} {width}x{height}"):
            yield


def release_comfy_vram():
    """Release ComfyUI image-model VRAM after an image request if enabled."""
    if not current_comfy_connection()["unload_after_generation"]:
        return {"ok": True, "skipped": True}
    try:
        with local_gpu_access(current_llm_connection()), log_elapsed("ComfyUI VRAM release"):
            result = http_json(comfy_url() + "/free", {"unload_models": True, "free_memory": True}, timeout=30)
            time.sleep(0.35)
            return {"ok": True, "result": result}
    except Exception as e:
        # Cleanup is optional; never fail a successful generation because of it.
        return {"ok": False, "error": str(e)}


def parse_json_answer(s):
    s = (s or "").strip()
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a:b + 1])
        raise ValueError("LLMの返答をJSONとして読み取れませんでした: " + s[:500])


def call_llm(history, consult=False, character=None, edit_context=None):
    system_text = CONSULT_PROMPT if consult else SYSTEM_PROMPT
    if character:
        system_text += "\n\n【選択中のキャラクター設定】\n"
        system_text += "キャラクター名: " + str(character.get("name", "")) + "\n"
        system_text += "外見・設定メモ: " + (str(character.get("notes", "")).strip() or "未登録") + "\n"
        system_text += "参照画像の登録枚数: " + str(len(character.get("images", []))) + "枚\n"
        system_text += "このキャラクターの外見上の特徴をプロンプトに反映してください。新規生成では参照画像は本人確認・外見参照としてのみ扱い、参照画像の背景、構図、カメラ距離、ポーズ、表情、服装を自動的にコピーしないでください。ユーザーが指定した行動・場面・構図を最優先し、その行動が画像内ではっきり見える具体的な完成プロンプトにしてください。画像モデルへ渡す完成プロンプトでは、名前そのものではなく外見の具体的特徴を記述してください。編集時のみ、ユーザーが変更を指示していない元画像の要素を維持してください。"
    if edit_context:
        color = edit_context["color"]
        system_text += (
            "\n\n【今回の編集範囲：最優先】元画像には編集位置を示す " + color + " の輪郭線があります。"
            "ユーザーの直近の変更要求を、その線の内側に限定した短い英語の編集指示にしてください。"
            "prompt本文に only inside the " + color + " outline を明記してください。"
            "外側は維持し、追加の目印線を消すよう指示してください。"
            "ユーザーが局所的な変更を指示した場合、被写体と背景全体へ対象を広げないでください。"
            "過去の会話に全体への変更指示があっても、今回の輪郭内という指定を優先してください。"
            "ユーザーが対象を限定していなければ surfaces inside the outline とし、対象物を推測で増やさないでください。"
            "例: Change the color only inside the " + color + " outline. Keep the rest unchanged. Remove the annotation outline."
        )
    messages = [{"role": "system", "content": system_text}]
    for m in history[-60:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            messages.append({"role": m["role"], "content": m["content"]})
    return llm_json(messages, temperature=0.45)


def llm_error_detail(exc):
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 429:
        return str(exc)
    try:
        error = json.loads(exc.read(16384)).get("error")
        code = error.get("code")
        kind = error.get("type")
    except (AttributeError, OSError, TypeError, ValueError):
        code = kind = None

    if code == "credit_balance_exhausted":
        detail = "APIクレジットの残高がありません。APIの請求設定を確認してください。"
    elif code == "project_spend_limit_exceeded":
        detail = "プロジェクトのAPI利用額の上限に達しました。プロジェクトの設定を確認してください。"
    elif code == "organization_spend_limit_exceeded":
        detail = "組織のAPI利用額の上限に達しました。組織の利用上限を確認してください。"
    elif code == "organization_usage_limit_exceeded":
        detail = "組織のAPI利用枠の上限に達しました。組織の利用上限を確認してください。"
    elif code in ("rate_limit_exceeded", "slow_down") or kind == "rate_limit_error":
        detail = "短時間のAPI利用上限に達しました。少し時間をおいてから再試行してください。"
        retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
        if re.fullmatch(r"[0-9]{1,8}", retry_after):
            detail += f" 待ち時間の目安は{retry_after}秒です。"
    elif code == "insufficient_quota" or kind == "insufficient_quota":
        detail = "APIの利用枠が不足しています。APIクレジットの残高と利用上限を確認してください。"
    else:
        detail = "APIの利用枠または短時間の利用上限に達しました。接続先の利用状況を確認してください。"
    return "HTTP 429: " + detail


def llm_json(messages, temperature):
    settings = current_llm_connection()
    if not settings["url"] or not settings["model"]:
        raise ValueError("設定 → LLMの接続先で、API URLとモデルIDを設定してください。")
    url = settings["url"] + "/chat/completions"
    try:
        payload = {"model": settings["model"], "messages": messages}
        credentials = llm_credentials_for(url, settings)
        if not credentials:
            payload["temperature"] = temperature
        def request(event):
            global LLM_ACTIVITY_MARKER
            with local_gpu_access(settings):
                if event is not None and event.is_set():
                    raise LLMCancelled()
                initial_restore = (settings.get("unload_provider") == "strata_elastic"
                                   and settings["url"] not in STRATA_ELASTIC_INITIALIZED)
                if initial_restore or settings["url"] in STRATA_ELASTIC_PENDING:
                    try_resize_strata_vram(settings, None)
                    STRATA_ELASTIC_INITIALIZED.add(settings["url"])
                    if event is not None and event.is_set():
                        raise LLMCancelled()
                LLM_ACTIVITY_MARKER = object()
                with log_elapsed("LLM", f"{settings['model']} @ {settings['url']}"):
                    return llm_http_json(url, payload, timeout=300, **credentials)

        result = wait_for_llm(request)
    except (OSError, ValueError) as exc:
        raise LLMConnectionError(f"LLMに問い合わせできませんでした（{settings['model']} / {url}）。詳細: {llm_error_detail(exc)}") from exc
    try:
        content = result["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
        if not isinstance(content, str) or not content.strip():
            raise ValueError("本文が空です。")
        answer = parse_json_answer(content)
        if not isinstance(answer, dict):
            raise ValueError("JSONオブジェクトを返す必要があります。")
        return answer
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMOutputError(f"LLMの返答を読み取れませんでした（{settings['model']}）。詳細: {exc}") from exc


def list_llm_models(base_url, key_env="", protected_key=""):
    url = base_url + "/models"
    try:
        result = llm_http_json(url, timeout=8, key_env=key_env, protected_key=protected_key)
        models = result["data"]
        if not isinstance(models, list):
            raise ValueError("モデル一覧の形式を確認できませんでした。")
        return sorted({item["id"] for item in models if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]})
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise LLMConnectionError(f"モデル一覧を取得できませんでした（{url}）。モデルIDは手入力できます。詳細: {llm_error_detail(exc)}") from exc



def clamp_dimension(value, default):
    try:
        n = int(value)
    except Exception:
        n = default
    n = max(512, min(1536, n))
    return max(512, min(1536, round(n / 64) * 64))


def clamp_count(value, default=1):
    try:
        n = int(value)
    except Exception:
        n = default
    return max(1, n)


def upload_bytes_to_comfy(data, filename):
    boundary = "----FlashNextBoundary" + uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f'Content-Type: image/png\r\n\r\n'
    ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(comfy_url() + "/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.loads(r.read().decode("utf-8"))
    return result.get("name", filename)


def _gallery_entry_path(gallery_id):
    if not isinstance(gallery_id, str) or not gallery_id.startswith("img_"):
        raise ValueError("ギャラリー画像IDが不正です。")
    target = (GALLERY_DIR / (gallery_id + ".png")).resolve()
    if target.parent != GALLERY_DIR.resolve():
        raise ValueError("ギャラリー画像の保存先が不正です。")
    return target


def get_previous_image_bytes(info):
    if isinstance(info, dict) and info.get("gallery_id"):
        target = _gallery_entry_path(str(info.get("gallery_id")))
        if target.is_file():
            return target.read_bytes()
    qs = urllib.parse.urlencode({
        "filename": info.get("filename", ""),
        "subfolder": info.get("subfolder", ""),
        "type": info.get("type", "output")
    })
    with urllib.request.urlopen(comfy_url() + "/view?" + qs, timeout=60) as r:
        return r.read()


def _read_gallery_index():
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    if not GALLERY_INDEX.is_file():
        return []
    try:
        data = json.loads(GALLERY_INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_gallery_index(items):
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = GALLERY_DIR / "gallery.json.tmp"
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(GALLERY_INDEX)


def save_gallery_image(info, metadata=None, image_data=None):
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    gallery_id = "img_" + uuid.uuid4().hex
    data = get_previous_image_bytes(info) if image_data is None else image_data
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    # The PNG header records the dimensions ComfyUI actually produced.
    if len(data) >= 24 and data[:16] == b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
        width = int.from_bytes(data[16:20], 'big')
        height = int.from_bytes(data[20:24], 'big')
        if width > 0 and height > 0:
            metadata.update(width=width, height=height)
    _gallery_entry_path(gallery_id).write_bytes(data)
    entry = {
        "id": gallery_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": {
            "filename": info.get("filename", ""),
            "subfolder": info.get("subfolder", ""),
            "type": info.get("type", "output")
        },
        "metadata": metadata,
        "favorite": False
    }
    items = _read_gallery_index()
    items.append(entry)
    _write_gallery_index(items)
    return {
        "url": "/gallery-image?id=" + urllib.parse.quote(gallery_id),
        "info": {"gallery_id": gallery_id},
        "metadata": entry.get("metadata", {}),
        "favorite": bool(entry.get("favorite", False))
    }


def load_gallery():
    valid = []
    changed = False
    for entry in _read_gallery_index():
        gid = str(entry.get("id", ""))
        try:
            if _gallery_entry_path(gid).is_file():
                valid.append(entry)
            else:
                changed = True
        except Exception:
            changed = True
    if changed:
        _write_gallery_index(valid)
    # oldest -> newest: JS prepends each item, so newest ends up first
    return [{
        "url": "/gallery-image?id=" + urllib.parse.quote(str(x["id"])),
        "info": {"gallery_id": str(x["id"])},
        "metadata": x.get("metadata", {}) if isinstance(x.get("metadata", {}), dict) else {},
        "favorite": bool(x.get("favorite", False))
    } for x in valid]



def set_gallery_favorite(gallery_id, favorite):
    target = _gallery_entry_path(gallery_id)
    if not target.is_file():
        raise FileNotFoundError("画像が見つかりません。")
    items = _read_gallery_index()
    found = False
    for entry in items:
        if str(entry.get("id", "")) == gallery_id:
            entry["favorite"] = bool(favorite)
            found = True
            break
    if not found:
        raise FileNotFoundError("ギャラリー情報が見つかりません。")
    _write_gallery_index(items)

def delete_gallery_image(gallery_id):
    target = _gallery_entry_path(gallery_id)
    items = _read_gallery_index()
    new_items = [x for x in items if str(x.get("id", "")) != gallery_id]
    if len(new_items) == len(items):
        raise FileNotFoundError("ギャラリー情報が見つかりません。")
    if target.is_file():
        target.unlink()
    _write_gallery_index(new_items)

def delete_nonfavorite_gallery_images():
    items = _read_gallery_index()
    keep = []
    deleted = 0
    for entry in items:
        if bool(entry.get("favorite", False)):
            keep.append(entry)
            continue
        gid = str(entry.get("id", ""))
        try:
            target = _gallery_entry_path(gid)
            if target.is_file():
                target.unlink()
        except Exception:
            pass
        deleted += 1
    _write_gallery_index(keep)
    return deleted

def make_image_url(info):
    return "/image?" + urllib.parse.urlencode({
        "filename": info.get("filename", ""),
        "subfolder": info.get("subfolder", ""),
        "type": info.get("type", "output")
    })


def generation_seed(profile):
    seed = profile.get('seed')
    return secrets.randbelow(2 ** 50) if seed is None else seed


def generate_z_image(prompt, width, height, mode="new", source_image=None, mask_data=None, img2img_denoise=0.70, character=None, profile=None):
    wf = json.loads(Z_WORKFLOW_FILE.read_text(encoding="utf-8"))
    profile = profile or model_registry.get("z_image_turbo")
    image_workflows.configure_z(wf, profile)
    wf["57:27"]["inputs"]["text"] = prompt
    wf["57:13"]["inputs"]["width"] = width
    wf["57:13"]["inputs"]["height"] = height
    wf["57:3"]["inputs"]["seed"] = generation_seed(profile)

    if source_image and mode in ("full", "inpaint"):
        original_bytes = get_previous_image_bytes(source_image)
        original_name = upload_bytes_to_comfy(original_bytes, "flashnext_previous.png")

        # image_z_image_turbo.json is a text-to-image workflow, so edit nodes
        # are injected only when an edit is requested.  This avoids relying
        # on hard-coded node IDs that are not present in the base workflow.
        wf["100"] = {
            "inputs": {"image": original_name, "upload": "image"},
            "class_type": "LoadImage",
            "_meta": {"title": "FlashNext Source Image"}
        }

        if mode == "inpaint" and mask_data:
            if "," in mask_data:
                mask_data = mask_data.split(",", 1)[1]
            mask_bytes = base64.b64decode(mask_data)
            mask_name = upload_bytes_to_comfy(mask_bytes, "flashnext_mask.png")

            wf["102"] = {
                "inputs": {"image": mask_name, "upload": "image"},
                "class_type": "LoadImage",
                "_meta": {"title": "FlashNext Mask Image"}
            }
            wf["103"] = {
                "inputs": {"image": ["102", 0], "channel": "red"},
                "class_type": "ImageToMask",
                "_meta": {"title": "FlashNext Image To Mask"}
            }
            wf["104"] = {
                "inputs": {
                    "pixels": ["100", 0],
                    "vae": ["57:29", 0],
                    "mask": ["103", 0],
                    "grow_mask_by": 8
                },
                "class_type": "VAEEncodeForInpaint",
                "_meta": {"title": "FlashNext Inpaint Encode"}
            }
            wf["57:3"]["inputs"]["latent_image"] = ["104", 0]
            wf["57:3"]["inputs"]["denoise"] = 1.0
        else:
            wf["101"] = {
                "inputs": {"pixels": ["100", 0], "vae": ["57:29", 0]},
                "class_type": "VAEEncode",
                "_meta": {"title": "FlashNext Image Encode"}
            }
            wf["57:3"]["inputs"]["latent_image"] = ["101", 0]
            wf["57:3"]["inputs"]["denoise"] = max(0.05, min(1.0, float(img2img_denoise)))

    queued = http_json(comfy_url() + "/prompt", {"prompt": wf}, timeout=60)
    pid = queued.get("prompt_id")
    if not pid:
        raise RuntimeError("ComfyUIからprompt_idが返りませんでした: " + str(queued))
    deadline = time.time() + 420
    while time.time() < deadline:
        time.sleep(1)
        hist = http_json(comfy_url() + "/history/" + urllib.parse.quote(pid), timeout=30)
        item = hist.get(pid)
        if item:
            for node in item.get("outputs", {}).values():
                imgs = node.get("images", [])
                if imgs:
                    return imgs[0]
            if item.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUIで生成に失敗しました。ComfyUI画面のエラーを確認してください。")
    raise TimeoutError("画像生成の完了待ちがタイムアウトしました。")





def upload_character_refs(character, limit=4):
    refs = []
    if not character:
        return refs
    folder = _character_path(character["id"]) / "images"
    images = list(character.get("images", []))[:limit]
    for i, image in enumerate(images, 1):
        path = folder / image.get("filename", "")
        if not path.is_file():
            continue
        refs.append(upload_bytes_to_comfy(path.read_bytes(), f"flashnext_char_{character['id']}_{i}.png"))
    return refs


def qwen_prompt_with_refs(prompt, ref_count):
    prompt = str(prompt).strip()
    if ref_count <= 0:
        return prompt
    refs = ", ".join(f"<image{i}>" for i in range(1, ref_count + 1))
    return prompt + f"\n\nUse {refs} ONLY as identity and appearance references for the same selected character. Preserve the character's identity, facial features, hair, and distinctive physical appearance. DO NOT copy the reference images' pose, framing, background, camera distance, expression, or composition unless the user explicitly asks for them. Create a clearly new scene. The user's requested ACTION and SCENE must be visibly and unambiguously depicted, with an appropriate body pose and environment that make the action obvious."


_qwen_base_nodes = image_workflows._qwen_base_nodes


def queue_and_wait(wf, timeout=600, output_node=None):
    queued = http_json(comfy_url() + "/prompt", {"prompt": wf}, timeout=60)
    pid = queued.get("prompt_id")
    if not pid:
        raise RuntimeError("ComfyUIからprompt_idが返りませんでした: " + str(queued))
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        hist = http_json(comfy_url() + "/history/" + urllib.parse.quote(pid), timeout=30)
        item = hist.get(pid)
        if item:
            for node in ([item.get("outputs", {}).get(output_node, {})] if output_node is not None else item.get("outputs", {}).values()):
                imgs = node.get("images", [])
                if imgs:
                    return imgs[0]
            if item.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUIで画像生成に失敗しました。ComfyUI画面のエラーを確認してください。")
    raise TimeoutError("画像生成の完了待ちがタイムアウトしました。")


def generate_qwen_image(prompt, width, height, mode="new", source_image=None, mask_data=None, marked_image_data=None, character=None, profile=None):
    profile = profile or model_registry.get("qwen21_turbo")
    seed = generation_seed(profile)
    source_name = None
    is_edit = bool(source_image and mode in ("full", "inpaint"))
    char_refs = [] if is_edit else upload_character_refs(character, 4)
    if is_edit:
        if mode == "inpaint":
            if not isinstance(marked_image_data, dict) or marked_image_data.get("color") not in ("red", "blue", "green"):
                raise ValueError("範囲指定画像を作成できませんでした。画面を再読み込みして、もう一度塗ってください。")
            color = marked_image_data["color"]
            encoded = marked_image_data.get("image_data", "")
            if not isinstance(encoded, str) or not encoded.startswith("data:image/png;base64,"):
                raise ValueError("範囲指定画像の形式が不正です。")
            source_name = upload_bytes_to_comfy(base64.b64decode(encoded.split(",", 1)[1], validate=True), "flashnext_qwen_outline_source.png")
            prompt += (
                f"\n\nREGION-GUIDED EDIT: In <image1>, the {color.upper()} outline marks the region to edit. "
                "Apply the requested change to the content inside that outline. "
                "The outline is only a location annotation, not part of the scene. "
                f"Remove only the added {color} annotation line from the finished image; preserve naturally occurring colors. "
                "Preserve the rest of the image as much as possible unless the instruction requires related changes. "
                "Output only the naturally edited image, without annotation lines."
            )
        else:
            source_name = upload_bytes_to_comfy(get_previous_image_bytes(source_image), "flashnext_qwen_source.png")
    else:
        prompt = qwen_prompt_with_refs(prompt, len(char_refs))

    IMAGE_PROMPT.set(prompt)
    if is_edit and profile.get("workflows", {}).get("edit"):
        wf = image_workflows.build_turbo_edit(prompt, seed, source_name, profile)
        return queue_and_wait(wf)

    wf, next_id = _qwen_base_nodes(prompt, width, height, seed, char_refs, source_name, profile)

    if source_name:
        # Qwen Image Edit provides the source-derived latent from TextEncodeQwenImage21.
        # No SetLatentNoiseMask / post-composite is used for Qwen local edits.
        latent_ref = ["5", 2]
    else:
        wf["6"] = {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage", "_meta": {"title": "Output size"}}
        latent_ref = ["6", 0]

    wf["7"] = {"inputs": {"seed": seed, **profile["sampling"], "denoise": 1.0,
                           "model": ["2", 0], "positive": ["5", 0], "negative": ["5", 1], "latent_image": latent_ref},
               "class_type": "KSampler", "_meta": {"title": profile["label"] + " Sampler"}}
    wf["8"] = {"inputs": {"samples": ["7", 0], "vae": ["4", 0]}, "class_type": "VAEDecode", "_meta": {"title": "Decode"}}
    wf["9"] = {"inputs": {"filename_prefix": "FlashNext_Qwen21", "images": ["8", 0]}, "class_type": "SaveImage", "_meta": {"title": "Save"}}
    return queue_and_wait(wf)


def generate_image(prompt, width, height, mode="new", source_image=None, mask_data=None, marked_image_data=None, img2img_denoise=0.70, character=None, model="qwen21_turbo", parameter_mode=None, image_batch=None):
    IMAGE_PROMPT.set(prompt)
    profile = model_registry.get(model, parameter_mode or ("edit" if mode in ("full", "inpaint") else "chat"))
    if mode != "new" and not profile["capabilities"].get("edit"):
        raise ValueError(profile["label"] + " は新規生成専用です。")
    if mode == "inpaint" and not profile["capabilities"].get("mask_method"):
        raise ValueError(profile["label"] + " はマスク編集に対応していません。")
    model_registry.ensure_ready(profile, comfy_url())
    with image_resources(model, mode, width, height, image_batch):
        if profile["backend"] == "user_workflow":
            wf = model_registry.user_workflows.build(profile, prompt, width, height, generation_seed(profile))
            return queue_and_wait(wf, timeout=1200, output_node=profile['output_node'])
        if profile["backend"] == "anima":
            if mode not in ("new", "full", "inpaint"):
                raise ValueError("Animaの編集モードが不正です。")
            source_name = None
            mask_name = None
            if mode == "inpaint":
                if not isinstance(mask_data, str) or not mask_data:
                    raise ValueError("マスク画像がありません。編集したい範囲を塗ってください。")
                data = mask_data.split(",", 1)[-1]
                mask_name = upload_bytes_to_comfy(base64.b64decode(data, validate=True), "flashnext_anima_mask.png")
            if mode in ("full", "inpaint"):
                if not source_image:
                    raise ValueError("編集元画像を選択してください。")
                source_name = upload_bytes_to_comfy(get_previous_image_bytes(source_image), "flashnext_anima_source.png")
            wf = image_workflows.build_anima(prompt, width, height, generation_seed(profile), profile, source_name, img2img_denoise, mask_name)
            return queue_and_wait(wf, timeout=1200)
        if profile["backend"] == "z_image":
            return generate_z_image(prompt, width, height, mode, source_image, mask_data, img2img_denoise, character, profile)
        if profile["backend"] == "qwen21":
            return generate_qwen_image(prompt, width, height, mode, source_image, mask_data, marked_image_data, character, profile)
        raise ValueError("未対応の画像モデルです: " + str(model))

def _character_path(character_id):
    if not isinstance(character_id, str) or not character_id.startswith("char_"):
        raise ValueError("キャラクターIDが不正です。")
    folder = (CHARACTER_DIR / character_id).resolve()
    if folder.parent != CHARACTER_DIR.resolve():
        raise ValueError("キャラクターの保存先が不正です。")
    return folder


def load_characters():
    CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for folder in CHARACTER_DIR.iterdir():
        if not folder.is_dir() or not folder.name.startswith("char_"):
            continue
        meta_file = folder / "character.json"
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            images = []
            for image in data.get("images", []):
                filename = str(image.get("filename", ""))
                if not filename or Path(filename).name != filename:
                    continue
                if (folder / "images" / filename).is_file():
                    images.append({
                        "filename": filename,
                        "created_at": image.get("created_at", "")
                    })
            items.append({
                "id": folder.name,
                "name": str(data.get("name", folder.name)),
                "notes": str(data.get("notes", "")),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "images": images
            })
        except Exception:
            # Ignore a damaged character record rather than preventing the UI from opening.
            continue
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items


def get_character(character_id):
    for item in load_characters():
        if item["id"] == character_id:
            return item
    return None


def delete_character(character_id):
    folder = _character_path(character_id)
    if not folder.is_dir():
        raise ValueError("キャラクターが見つかりません。")
    shutil.rmtree(folder)


def character_image_path(character_id, filename):
    folder = _character_path(character_id)
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("画像ファイル名が不正です。")
    target = (folder / "images" / filename).resolve()
    if target.parent != (folder / "images").resolve() or not target.is_file():
        raise FileNotFoundError("キャラクター画像が見つかりません。")
    return target


def save_character_image(body):
    name = str(body.get("name", "")).strip()
    notes = str(body.get("notes", "")).strip()
    target_id = str(body.get("target_id", "__new__"))
    source_image = body.get("source_image")
    if not isinstance(source_image, dict) or not (source_image.get("gallery_id") or source_image.get("filename")):
        raise ValueError("保存する画像が指定されていません。")

    if target_id == "__new__":
        if not name:
            raise ValueError("キャラクター名を入力してください。")
        character_id = "char_" + uuid.uuid4().hex[:12]
        folder = _character_path(character_id)
        (folder / "images").mkdir(parents=True, exist_ok=False)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        data = {"name": name, "notes": notes, "created_at": now, "updated_at": now, "images": []}
    else:
        character_id = target_id
        folder = _character_path(character_id)
        meta_file = folder / "character.json"
        if not meta_file.is_file():
            raise ValueError("追加先のキャラクターが見つかりません。")
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        if len(data.get("images", [])) >= 4:
            raise ValueError("このキャラクターには参照画像が4枚登録されています。先に画像を削除してください。")
        if notes:
            data["notes"] = notes
        (folder / "images").mkdir(parents=True, exist_ok=True)

    if len(data.get("images", [])) >= 4:
        raise ValueError("参照画像は1キャラクターにつき最大4枚です。")

    image_bytes = get_previous_image_bytes(source_image)
    filename = "image_" + uuid.uuid4().hex + ".png"
    (folder / "images" / filename).write_bytes(image_bytes)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    data.setdefault("images", []).append({"filename": filename, "created_at": now})
    data["updated_at"] = now
    tmp = folder / "character.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(folder / "character.json")
    return get_character(character_id)


HTML = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SceneLab</title><style>
*{box-sizing:border-box}html,body{height:100%}body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#11141a;color:#eee;margin:0;overflow:hidden}
button,input,textarea,select{font:inherit}button{border:0;cursor:pointer;color:#fff}button:disabled{opacity:.5;cursor:default}
.app{height:100vh;display:grid;grid-template-columns:260px minmax(420px,1fr) 330px;grid-template-rows:64px minmax(0,1fr);background:#11141a}
header{grid-column:1/4;display:flex;align-items:center;gap:18px;padding:0 20px;border-bottom:1px solid #2b313b;background:#171b22}
.logo{font-weight:800;font-size:17px;white-space:nowrap}.tabs{display:flex;gap:6px}.tab{padding:9px 16px;border-radius:9px;background:transparent;color:#aeb8c7;font-weight:700}.tab.active{background:#2b3442;color:#fff}.newChat{margin-left:auto;padding:9px 14px;border-radius:9px;background:#252d39;color:#cbd5e1;font-weight:700}.newChat:hover{background:#313b49}
#leftPanel,#rightPanel{min-height:0;background:#171b22;padding:18px;overflow:auto}#leftPanel{border-right:1px solid #2b313b}#rightPanel{border-left:1px solid #2b313b}
.panelTitle{font-size:13px;color:#8e9aac;font-weight:800;letter-spacing:.06em;margin:0 0 14px;text-transform:uppercase}.modeGroup{display:block}.modeGroup.hidden{display:none}.controlGroup{padding:14px 0;border-bottom:1px solid #2b313b}.controlGroup:first-of-type{padding-top:0}.label{font-size:13px;font-weight:700;margin-bottom:8px;color:#dce3ed}.sub{font-size:11px;color:#8490a2;line-height:1.55;margin-top:7px}.strengthRow{display:grid;grid-template-columns:1fr 54px;gap:10px;align-items:center}.strengthRow input[type=range]{width:100%}.strengthValue{height:34px;border:1px solid #3b4554;border-radius:8px;background:#202631;color:#fff;text-align:center;font-weight:700}
.stepper{height:42px;display:grid;grid-template-columns:42px 1fr 42px;border:1px solid #3b4554;border-radius:10px;overflow:hidden;background:#202631}.stepper button{background:#2b3442;font-size:21px;font-weight:500}.stepper button:hover{background:#354153}.stepper input{width:100%;min-width:0;text-align:center;background:transparent;color:#fff;border:0;outline:0;font-weight:700;appearance:textfield;-moz-appearance:textfield}.stepper input::-webkit-outer-spin-button,.stepper input::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.sizeRow{display:grid;grid-template-columns:1fr 22px 1fr;align-items:center;gap:4px}.sizeRow input,.select{width:100%;height:40px;background:#202631;border:1px solid #3b4554;border-radius:9px;color:#fff;padding:0 10px;outline:0}.x{text-align:center;color:#6f7c90}.presetRow{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:7px}.preset{padding:7px 4px;border-radius:7px;background:#252d39;color:#aeb8c7;font-size:11px}.preset:hover{background:#313c4c;color:#fff}.select:disabled{color:#9ba6b5}
#generateButton{width:100%;padding:13px 10px;border-radius:11px;background:#2784e8;font-weight:800;margin-top:16px}.status{font-size:12px;color:#8eb9e5;line-height:1.5;margin-top:10px;min-height:18px}
#workspace{min-width:0;min-height:0;position:relative;background:#11141a}.view{position:absolute;inset:0;display:none;min-height:0}.view.active{display:flex}
#chatView{flex-direction:column}.chatScroll{flex:1;min-height:0;overflow:auto;padding:24px 28px}.chat{max-width:850px;margin:0 auto;display:flex;flex-direction:column;gap:12px}.msg{padding:13px 16px;border-radius:14px;white-space:pre-wrap;line-height:1.6;max-width:90%}.user{align-self:flex-end;background:#284b72}.assistant{align-self:flex-start;background:#242a34}.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#394252;color:#cfe4ff;font-size:12px;margin-bottom:7px}
.composer{padding:12px 22px 18px;border-top:1px solid #272d36;background:#151920}.composerInner{max-width:850px;margin:auto;display:flex;gap:9px}.composer textarea{flex:1;min-height:60px;max-height:150px;resize:vertical;border-radius:12px;padding:13px;background:#202631;color:white;border:1px solid #3b4554;outline:0}.send{width:84px;border-radius:12px;background:#2784e8;font-weight:800}.consult{width:84px;border-radius:12px;background:#384252;font-weight:800}.consult:hover{background:#465266}
#editView{flex-direction:column;overflow:auto;padding:24px}.editWrap{width:min(100%,900px);margin:auto}.editTop{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:14px}.editTitle{font-size:18px;font-weight:800}.editorHelp{font-size:12px;color:#98a5b8;line-height:1.6;margin-top:4px}.stageWrap{display:flex;justify-content:center;background:#090b0f;border:1px solid #2e3540;border-radius:14px;padding:12px;min-height:260px}.stage{position:relative;max-width:100%;line-height:0;border-radius:9px;overflow:hidden}.stage img{display:block;max-width:100%;max-height:68vh;width:auto;height:auto}.stage canvas{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair;touch-action:none}.editControls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:13px}.secondary{background:#343d4b;padding:9px 14px;border-radius:9px;font-weight:700}.secondary.active{background:#c65e48}.brush{display:flex;align-items:center;gap:8px;color:#b7c0cd;font-size:12px}.editComposer{display:flex;gap:10px;margin-top:14px;padding-top:14px;border-top:1px solid #2b313b}.editComposer textarea{flex:1;min-height:74px;max-height:180px;resize:vertical;border-radius:12px;padding:13px;background:#202631;color:#fff;border:1px solid #3b4554;outline:0}.editComposer .send{width:96px;min-height:48px}
#characterView{flex-direction:column;overflow:auto;padding:24px}.characterPage{width:min(100%,1000px);margin:0 auto}.characterHead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}.characterGrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.characterCard{background:#1b212b;border:1px solid #303a48;border-radius:12px;padding:10px;text-align:left;cursor:pointer}.characterCard.active{border-color:#58a6ff;box-shadow:0 0 0 1px #58a6ff}.characterCard img{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:8px;background:#0e1116}.characterCardName{font-weight:800;margin-top:8px}.characterCardMeta{font-size:11px;color:#91a0b4;margin-top:3px}.characterDetail{margin-top:20px;padding:16px;background:#171d26;border:1px solid #303a48;border-radius:12px}.characterDetailImages{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:10px;margin-top:12px}.characterDetailImages img{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:8px;background:#0e1116}.characterEmpty{padding:28px;text-align:center;color:#93a0b2;background:#171d26;border:1px dashed #3b4554;border-radius:12px;line-height:1.7}.modalBackdrop{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;padding:18px;z-index:20}.modalBackdrop.open{display:flex}.modal{width:min(100%,520px);max-height:90vh;overflow:auto;background:#1a202a;border:1px solid #3b4554;border-radius:16px;padding:20px;box-shadow:0 20px 70px #0009}.modalTitle{font-size:18px;font-weight:800;margin-bottom:16px}.formField{display:flex;flex-direction:column;gap:7px;margin:13px 0}.formField label{font-size:12px;color:#b9c5d4;font-weight:700}.formField input,.formField textarea,.formField select{width:100%;background:#202631;border:1px solid #3b4554;border-radius:9px;color:#fff;padding:10px;outline:0}.formField textarea{min-height:82px;resize:vertical}.modalActions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.thumbMenu{position:absolute;right:5px;top:5px;width:28px;height:28px;border-radius:8px;background:#090d13d9;font-size:18px;line-height:1}.thumbMenu:hover{background:#2784e8}.primarySmall{background:#2784e8;padding:9px 13px;border-radius:9px;font-weight:700}.mutedSmall{background:#343d4b;padding:9px 13px;border-radius:9px;font-weight:700}.dangerSmall{background:#6b2730;padding:9px 13px;border-radius:9px;font-weight:700}.dangerSmall:hover{background:#87323d}.charSelectHint{font-size:11px;color:#8794a6;line-height:1.5;margin-top:7px}
.galleryHeader{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px}.galleryHeaderActions{display:flex;align-items:center;gap:7px}.galleryCleanup{background:#343d4b;padding:6px 8px;border-radius:7px;font-size:10px;font-weight:700}.galleryCleanup:hover{background:#465264}.favoriteBadge{position:absolute;left:6px;top:6px;width:28px;height:28px;border-radius:8px;background:#090d13d9;font-size:18px;line-height:1;color:#8a96a6}.favoriteBadge.on{color:#ffd34d;background:#211c0bd9}.favoriteBadge:hover{background:#343d4b}.galleryCount{font-size:11px;color:#7f8b9c}.gallery{display:grid;grid-template-columns:1fr 1fr;gap:9px}.thumb{position:relative;background:#0e1116;border:2px solid transparent;border-radius:10px;overflow:hidden;cursor:pointer;aspect-ratio:1/1}.thumb:hover{border-color:#53677f}.thumb.selected{border-color:#58a6ff;box-shadow:0 0 0 1px #58a6ff}.thumb img{width:100%;height:100%;display:block;object-fit:cover}.emptyGallery{color:#6f7b8b;font-size:12px;line-height:1.7;padding:18px 2px}.thumbNo{position:absolute;left:6px;bottom:6px;background:#0009;padding:3px 6px;border-radius:6px;font-size:10px;color:#ddd}

.progressDock{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(18px);width:min(620px,calc(100vw - 36px));background:#171d26f2;border:1px solid #3b4554;border-radius:14px;padding:13px 15px;box-shadow:0 16px 48px #0009;z-index:18;opacity:0;pointer-events:none;transition:.18s ease}.progressDock.open{opacity:1;transform:translateX(-50%) translateY(0);pointer-events:auto}.progressTop{display:flex;align-items:center;justify-content:space-between;gap:12px}.progressTitle{font-size:13px;font-weight:800;color:#e7edf6}.progressTime{font-size:12px;color:#9eb0c5;font-variant-numeric:tabular-nums}.progressDetail{font-size:11px;color:#91a0b4;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.progressTrack{height:5px;background:#28313d;border-radius:999px;overflow:hidden;margin-top:10px}.progressBar{height:100%;width:34%;background:#2784e8;border-radius:999px;animation:progressSweep 1.25s ease-in-out infinite}@keyframes progressSweep{0%{transform:translateX(-105%)}50%{transform:translateX(195%)}100%{transform:translateX(-105%)}}
#leftPanel input:disabled,#leftPanel select:disabled,#leftPanel textarea:disabled,#leftPanel .preset:disabled{background:#202124;color:#888b90;border-color:#36383d;cursor:not-allowed;opacity:1}
#leftPanel input[type="range"]:disabled{accent-color:#777a80;filter:grayscale(1);opacity:.55}
.progressActions{display:flex;justify-content:flex-end;margin-top:10px}.progressCancel{display:none;background:#3b4655;color:#f0f4f8;border:1px solid #566273;padding:7px 12px;border-radius:8px;font-size:11px;font-weight:800}.progressCancel.show{display:inline-block}.progressCancel:hover{background:#4a5666}.progressCancel:disabled{opacity:.55;cursor:default}
@media(max-width:1000px){body{overflow:auto}.app{height:auto;min-height:100vh;grid-template-columns:220px minmax(0,1fr);grid-template-rows:64px minmax(650px,auto) auto}header{grid-column:1/3}.app #rightPanel{grid-column:1/3;border-left:0;border-top:1px solid #2b313b}.gallery{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}}
@media(max-width:700px){.app{display:block}.app header{height:60px;position:sticky;top:0;z-index:5}.logo{font-size:14px}#leftPanel,#rightPanel{border:0;border-bottom:1px solid #2b313b}#workspace{height:72vh}.chatScroll{padding:16px}.composer{padding:10px}.msg{max-width:96%}.gallery{grid-template-columns:repeat(2,1fr)}}

/* Chat composer: full-width text input with compact actions below. */
#chatView .composerInner{flex-direction:column;gap:8px}
#chatView .composer textarea{flex:none;width:100%;min-height:88px;box-sizing:border-box}
.composerActions{display:flex;justify-content:flex-end;gap:7px;flex-wrap:wrap}
.composerActions .send,.composerActions .consult{width:auto!important;min-height:34px;padding:8px 14px;border-radius:8px;font-size:12px;white-space:nowrap}

.app.wideWorkspace{grid-template-columns:260px minmax(0,1fr)}
.app.wideWorkspace header{grid-column:1/-1}
.app.wideWorkspace #rightPanel{display:none}
.app.wideWorkspace .explorePage{max-width:none}
button.mutedSmall.galleryCopied{background:#245c4b;color:#d1f5e5;box-shadow:inset 0 0 0 1px #468e72}
button.mutedSmall.galleryCopied:hover{background:#2c705a}
@media(max-width:1000px){.app.wideWorkspace{grid-template-columns:220px minmax(0,1fr);grid-template-rows:64px minmax(650px,auto)}}

#promptModal .modalActions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:18px}
#promptModal .modalActions>button{width:100%;min-height:44px;margin:0;padding:11px 10px;white-space:normal;line-height:1.5;text-align:center;font-size:12px}

.directSmall{background:#247c68;color:#fff;padding:9px 13px;border-radius:9px;font-weight:700}.directSmall:hover{background:#2c927c}
</style></head><body><div class="app">
<header><div class="logo">SceneLab <span style="font-size:11px;color:#7f8b9c;font-weight:700">v1.0</span></div><nav class="tabs"><button class="tab active" id="chatTab">チャット</button><button class="tab" id="editTab">編集</button><button class="tab" id="characterTab">キャラクター</button></nav><div style="margin-left:auto;display:flex;gap:8px"><button type="button" id="completionNotifications" class="mutedSmall" aria-pressed="false">通知：OFF</button><button type="button" id="galleryImportOpen" class="mutedSmall">インポート</button><button type="button" class="newChat" id="newChat">＋ 新規チャット</button></div></header>
<aside id="leftPanel">
  <div id="leftPanelTitle" class="panelTitle">New image</div>
  <div id="generationModelGroup" class="controlGroup modeGroup"><div class="label">画像生成モデル</div><select id="generationModel" class="select"><option value="qwen21_turbo" selected>Qwen-Image-2.1-Turbo</option><option value="z_image_turbo">Z-Image Turbo</option></select><div class="sub">チャットでの画像生成、探索・ひらめきで使用する画像モデルです。</div></div>
  <div id="editModelGroup" class="controlGroup modeGroup hidden"><div class="label">画像編集モデル</div><select id="editModel" class="select"><option value="qwen21_turbo" selected>Qwen-Image-2.1-Turbo</option><option value="z_image_turbo">Z-Image Turbo</option></select><div class="sub">編集画面の全体編集・マスク編集で使います。</div></div>
  <div id="countGroup" class="controlGroup modeGroup"><div class="label">生成枚数</div><div class="stepper"><button type="button" id="countMinus">−</button><input id="count" type="number" min="1" step="1" value="1"><button type="button" id="countPlus">＋</button></div><div class="sub">新規生成する枚数です。複数枚の場合は、同じ最終プロンプトでシードだけを変えて生成します。</div></div>
  <div id="sizeGroup" class="controlGroup modeGroup"><div class="label">画像サイズ</div><div class="sizeRow"><input id="width" type="number" min="512" max="1536" step="64" value="1024"><span class="x">×</span><input id="height" type="number" min="512" max="1536" step="64" value="1024"></div><div class="presetRow"><button type="button" class="preset" data-w="1024" data-h="1024">1:1</button><button type="button" class="preset" data-w="832" data-h="1216">縦</button><button type="button" class="preset" data-w="1216" data-h="832">横</button></div><div class="sub">新規生成のサイズです。512〜1536px、64px刻みに自動調整します。</div></div>
  <div id="strengthGroup" class="controlGroup modeGroup hidden"><div class="label">img2img 変更強度</div><div class="strengthRow"><input id="editStrength" type="range" min="0.10" max="1.00" step="0.05" value="0.70"><input id="editStrengthValue" class="strengthValue" type="number" min="0.10" max="1.00" step="0.05" value="0.70"></div><div id="strengthHint" class="sub">Z・Animaのマスクなし編集に使います。マスクありは強度1固定、Qwenでは使用しません。</div></div>
  <div id="characterGroup" class="controlGroup modeGroup"><div class="label">使用キャラクター</div><select id="characterSelect" class="select"><option value="">使用しない</option></select><button type="button" id="manageCharacters" class="mutedSmall" style="width:100%;margin-top:8px">キャラクター管理</button><div class="charSelectHint">Qwen系モデル専用。登録した画像を参照して、キャラクターの外見を新規生成に反映します。</div></div>
  <div id="status" class="status"></div>
</aside>
<main id="workspace">
  <section id="chatView" class="view active"><div class="chatScroll"><div id="chat" class="chat"><div class="msg assistant">こんにちは！画像について相談することも、そのまま生成することもできます。</div></div></div><div class="composer"><div class="composerInner"><textarea id="input" placeholder="画像生成：AIがプロンプト作成 ／ 最終プロンプトから画像生成：入力文をそのまま使用"></textarea><div class="composerActions"><button id="consult" class="consult">チャット</button><button id="send" class="send" title="LLMでプロンプトを作成して生成">画像生成</button><button id="directSend" class="send" style="width:112px;background:#247c68" title="入力文をLLMに通さず、そのまま画像モデルへ送信">最終プロンプトから画像生成</button></div></div></div></section>
  <section id="editView" class="view"><div class="editWrap"><div class="editTop"><div><div class="editTitle">画像編集</div><div class="editorHelp">編集したい内容を入力してください。チャット履歴も引き継ぐため、必要に応じて新規チャットにしてください。</div></div></div><div class="stageWrap"><div id="stage" class="stage"><img id="source" alt="編集対象の画像"><canvas id="paint" style="opacity:0.65"></canvas></div></div><div class="editControls"><button id="paintToggle" class="secondary">範囲指定：OFF</button><label class="brush">ブラシ <input id="brush" type="range" min="10" max="140" value="48"></label><button id="undoPaint" class="secondary" disabled>ひと塗り戻す</button><button id="clear" class="secondary">範囲指定をクリア</button></div><div class="editComposer"><textarea id="editInput" placeholder="編集内容を入力。例：背景を明るくして / 選択した部分の色を変えて"></textarea><button id="editSend" class="send">生成</button></div></div></section>
  <section id="characterView" class="view"><div class="characterPage"><div class="characterHead"><div><div class="editTitle">キャラクター管理</div><div class="editorHelp">登録したキャラクターは新しいチャットでも使えます。参照画像は1人につき最大4枚で、Qwen系モデルの新規生成で使用できます。</div></div><button type="button" id="registerFromSelected" class="primarySmall">選択画像から登録</button></div><div id="characterGrid" class="characterGrid"></div><div id="characterEmpty" class="characterEmpty" style="display:none">まだキャラクターが登録されていません。<br>右側の生成画像メニューから「キャラクターとして登録」を選んでください。</div><div id="characterDetail" class="characterDetail" style="display:none"></div></div></section>
</main>
<aside id="rightPanel"><div class="galleryHeader"><div class="panelTitle" style="margin:0">Images</div><div class="galleryHeaderActions"><button type="button" id="deleteNonFavorites" class="galleryCleanup">★以外を全削除</button><div id="galleryCount" class="galleryCount">0枚</div></div></div><div id="gallery" class="gallery"></div><div id="emptyGallery" class="emptyGallery">生成した画像がここに並びます。<br>画像をクリックすると、その画像を選択して編集画面へ移動します。</div></aside>
</div>
<div id="progressDock" class="progressDock" aria-live="polite"><div class="progressTop"><div id="progressTitle" class="progressTitle">処理しています…</div><div id="progressTime" class="progressTime">0.0秒</div></div><div id="progressDetail" class="progressDetail"></div><div class="progressActions"><button type="button" id="progressCancel" class="progressCancel">この1枚の完了後に中止</button></div></div>
<div id="promptModal" class="modalBackdrop"><div class="modal"><div class="modalTitle">生成情報</div><div class="formField"><label>入力した指示</label><textarea id="savedUserInstruction" readonly style="min-height:80px"></textarea></div><div class="formField"><label>最終プロンプト</label><textarea id="savedFinalPrompt" readonly style="min-height:190px"></textarea></div><div id="savedPromptMeta" class="sub" style="margin-top:8px"></div><div class="modalActions"><button type="button" id="promptCharacter" class="mutedSmall">キャラクターとして登録</button><button type="button" id="reusePrompt" class="mutedSmall">チャットに貼り付け</button><button type="button" id="explorePromptImage" class="mutedSmall">探索に貼り付け</button><button type="button" id="inspirePromptImage" class="mutedSmall">ひらめきに貼り付け</button><button type="button" id="deletePromptImage" class="dangerSmall">削除</button><button type="button" id="closePrompt" class="mutedSmall">閉じる</button></div></div></div>
<div id="characterModal" class="modalBackdrop"><div class="modal"><div class="modalTitle">キャラクター登録</div><div id="registerImageInfo" class="sub">選択した生成画像を保存します。</div><div class="sub">登録した画像はQwen系モデルの新規生成で参照できます。</div><div class="sub">同じキャラクターの、顔が分かる画像・全身画像・横向きの画像などを登録できます。外見を伝えるための参考画像として使われます。</div><img id="registerImagePreview" alt="登録する画像" style="display:block;max-height:180px;max-width:100%;object-fit:contain;border-radius:9px;margin:10px auto;background:#0e1116"><div class="formField"><label for="characterTarget">保存先</label><select id="characterTarget"><option value="__new__">新しいキャラクターを作成</option></select></div><div class="formField"><label for="characterName">キャラクター名</label><input id="characterName" type="text" maxlength="60" placeholder="例：サンプルキャラクター"></div><div class="formField"><label for="characterNotes">外見・設定メモ（任意）</label><div class="sub" id="characterNotesHint">画像生成用のプロンプト作成に使用されます。</div><textarea id="characterNotes" aria-describedby="characterNotesHint" maxlength="1200" placeholder="例：短い髪、青い上着、丸い眼鏡"></textarea></div><div id="characterModalError" class="status"></div><div class="modalActions"><button type="button" id="cancelCharacter" class="mutedSmall">キャンセル</button><button type="button" id="saveCharacter" class="primarySmall">保存する</button></div></div></div>
<script src="/notifications.js"></script>
<script>
const chat=document.getElementById('chat'),input=document.getElementById('input'),consult=document.getElementById('consult'),send=document.getElementById('send'),directSend=document.getElementById('directSend'),editInput=document.getElementById('editInput'),editSend=document.getElementById('editSend'),status=document.getElementById('status');
const progressDock=document.getElementById('progressDock'),progressTitle=document.getElementById('progressTitle'),progressDetail=document.getElementById('progressDetail'),progressTime=document.getElementById('progressTime'),progressCancel=document.getElementById('progressCancel');let progressStarted=0,progressTimer=null,cancelAfterCurrent=false;
async function readApiResponse(response){
 const text=await response.text();
 let data;
 try{data=JSON.parse(text);}catch(_e){
  throw Error(response.ok?'サーバーの応答を読み取れませんでした。SceneLabを再起動して画面を再読み込みしてください。':`処理に失敗しました（HTTP ${response.status}）。SceneLabの接続と起動状態を確認してください。`);
 }
 if(!data||typeof data!=='object'||Array.isArray(data))throw Error('サーバーの応答形式が不正です。');
 if(!response.ok)throw Error(typeof data.error==='string'?data.error:`処理に失敗しました（HTTP ${response.status}）。`);
 return data;
}
async function fetchWithLLMRetryStatus(path,options,showRetry){
 const tracked=new Set(['/api/inspire/create','/api/inspire/expand','/api/inspire/prepare','/api/inspire/next','/api/explore/plan']);
 if(options.method!=='POST'||!tracked.has(path))return fetch(path,options);
 const id=crypto.randomUUID(),body=JSON.parse(options.body);
 let active=true,pollBusy=false,lastAttempt=1;
 const poll=async()=>{
  if(!active||pollBusy)return;pollBusy=true;
  try{
   const response=await fetch('/api/request-stage?id='+encodeURIComponent(id),{cache:'no-store'});
   if(!response.ok)return;
   const data=await readApiResponse(response),retry=data.retry;
   if(active&&retry&&retry.attempt>1&&retry.attempt!==lastAttempt){
    lastAttempt=retry.attempt;showRetry(`出力を再試行中 ${retry.attempt}/${retry.limit}`);
   }
  }catch(_e){}finally{pollBusy=false;}
 };
 const timer=setInterval(poll,500);
 try{return await fetch(path,{...options,body:JSON.stringify({...body,retry_progress_id:id})});}
 finally{active=false;clearInterval(timer);}
}
function startProgress(title,detail){progressStarted=performance.now();progressTitle.textContent=title;progressDetail.textContent=detail||'';progressTime.textContent='0.0秒';progressDock.classList.add('open');clearInterval(progressTimer);progressTimer=setInterval(()=>{progressTime.textContent=((performance.now()-progressStarted)/1000).toFixed(1)+'秒'},100)}
function updateProgress(title,detail){if(title)progressTitle.textContent=title;if(detail!==undefined)progressDetail.textContent=detail}
function stopProgress(){clearInterval(progressTimer);progressTimer=null;if(progressStarted)progressTime.textContent=((performance.now()-progressStarted)/1000).toFixed(1)+'秒';progressCancel.classList.remove('show');progressCancel.disabled=false;progressCancel.textContent='この1枚の完了後に中止';setTimeout(()=>progressDock.classList.remove('open'),250);progressStarted=0}
function startModeProgress(label,onCancel){
 const previousCancel=progressCancel.onclick;
 startProgress('処理を開始しています…',label);
 progressCancel.classList.add('show');progressCancel.disabled=false;progressCancel.textContent='この1枚の完了後に中止';
 progressCancel.onclick=()=>{onCancel();progressCancel.disabled=true;progressCancel.textContent='中止予約済み';};
 return ()=>{stopProgress();progressCancel.onclick=previousCancel;};
}
async function cancelLlmWait(id){
 const r=await fetch('/api/request-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
 if(!r.ok)throw Error('中止を送信できませんでした。');
 return readApiResponse(r);
}
progressCancel.onclick=()=>{cancelAfterCurrent=true;progressCancel.disabled=true;progressCancel.textContent='中止予約済み';updateProgress('この1枚の完了後に中止します…');};

const countInput=document.getElementById('count'),widthInput=document.getElementById('width'),heightInput=document.getElementById('height'),editStrength=document.getElementById('editStrength'),editStrengthValue=document.getElementById('editStrengthValue');
const chatTab=document.getElementById('chatTab'),editTab=document.getElementById('editTab'),characterTab=document.getElementById('characterTab'),chatView=document.getElementById('chatView'),editView=document.getElementById('editView'),characterView=document.getElementById('characterView');
const gallery=document.getElementById('gallery'),galleryCount=document.getElementById('galleryCount'),emptyGallery=document.getElementById('emptyGallery');
const source=document.getElementById('source'),canvas=document.getElementById('paint'),ctx=canvas.getContext('2d'),toggle=document.getElementById('paintToggle'),brush=document.getElementById('brush');
const history=[];let painting=false,paintEnabled=false,hasMask=false,lastPoint=null,currentSourceInfo=null,currentSelectedThumb=null,totalImages=0,characters=[],selectedCharacterId=readPreference('character_id')||'';let registeringSource=null;let galleryItems=new Map();

function showView(name){
 const edit=name==='edit',chars=name==='characters',explore=name==='explore',inspire=name==='inspire',settings=name==='settings',chatMode=!edit&&!chars&&!explore&&!inspire&&!settings;
 window.currentAppView=name;
 document.querySelector('.app').classList.toggle('settingsWorkspace',settings);
 document.getElementById('settingsView').classList.toggle('active',settings);document.getElementById('settingsTab').classList.toggle('active',settings);
 document.querySelector('.app').classList.toggle('wideWorkspace',explore||inspire);
 document.getElementById('inspireView').classList.toggle('active',inspire);document.getElementById('inspireTab').classList.toggle('active',inspire);
 document.getElementById('exploreView').classList.toggle('active',explore);document.getElementById('exploreTab').classList.toggle('active',explore);
 chatView.classList.toggle('active',chatMode);editView.classList.toggle('active',edit);characterView.classList.toggle('active',chars);
 chatTab.classList.toggle('active',chatMode);editTab.classList.toggle('active',edit);characterTab.classList.toggle('active',chars);
 const visibility={generationModelGroup:chatMode||explore||inspire,editModelGroup:edit,countGroup:chatMode,sizeGroup:chatMode||explore||inspire,strengthGroup:edit,characterGroup:chatMode||explore||inspire};
 Object.entries(visibility).forEach(([id,on])=>document.getElementById(id)?.classList.toggle('hidden',!on));
 const title=document.getElementById('leftPanelTitle');if(title)title.textContent=inspire?'Inspiration':(explore?'Explore':(chars?'Character':(edit?'Edit':'New image')));
 if(chars)refreshCharacters();
 window.dispatchEvent(new CustomEvent('app-view-change',{detail:name}));
}
document.getElementById('inspireTab').onclick=()=>showView('inspire');
document.getElementById('exploreTab').onclick=()=>showView('explore');
chatTab.onclick=()=>showView('chat');editTab.onclick=()=>showView('edit');characterTab.onclick=()=>showView('characters');document.getElementById('manageCharacters').onclick=()=>showView('characters');
document.getElementById('newChat').onclick=()=>{
  history.length=0;
  chat.innerHTML='';
  showView('chat');
  status.textContent='新しいチャットを開始しました。右の画像一覧と入力中の文章はそのまま残しています。';
  input.focus();
};
function getCount(){let raw=String(countInput.value||'1').trim();let n=Number(raw);if(!Number.isFinite(n)||n<1)n=1;n=Math.floor(n);countInput.value=String(n);return n}
function readPreference(key){try{return localStorage.getItem('flashnext_'+key)}catch(_e){return null}}
function storePreference(key,value){try{localStorage.setItem('flashnext_'+key,String(value))}catch(_e){}}
function saveCount(){storePreference('image_count',getCount())}
countInput.value=readPreference('image_count')??countInput.value;
getCount();
document.getElementById('countMinus').onclick=()=>{countInput.value=String(Math.max(1,getCount()-1));saveCount()};
document.getElementById('countPlus').onclick=()=>{countInput.value=String(getCount()+1);saveCount()};
countInput.addEventListener('change',saveCount);
function snapDim(el){let n=parseInt(el.value||'1024',10);if(!Number.isFinite(n))n=1024;n=Math.max(512,Math.min(1536,n));n=Math.round(n/64)*64;el.value=n;return n}function saveImageSize(){const size={width:snapDim(widthInput),height:snapDim(heightInput)};try{localStorage.setItem('flashnext_image_size',JSON.stringify(size))}catch(_e){}}
try{const size=JSON.parse(localStorage.getItem('flashnext_image_size')||'null');if(size&&typeof size==='object'){if(Number.isFinite(size.width))widthInput.value=size.width;if(Number.isFinite(size.height))heightInput.value=size.height;}}catch(_e){}
snapDim(widthInput);snapDim(heightInput);
widthInput.onchange=saveImageSize;heightInput.onchange=saveImageSize;
document.querySelectorAll('.preset').forEach(b=>b.onclick=()=>{widthInput.value=b.dataset.w;heightInput.value=b.dataset.h;saveImageSize()});
function clampStrength(v){let n=Number(v);if(!Number.isFinite(n))n=.70;n=Math.max(.10,Math.min(1,n));return Math.round(n*20)/20}function syncStrength(v){const n=clampStrength(v);editStrength.value=n.toFixed(2);editStrengthValue.value=n.toFixed(2);return n}syncStrength(readPreference('edit_strength')??editStrength.value);
editStrength.oninput=()=>{editStrengthValue.value=Number(editStrength.value).toFixed(2);storePreference('edit_strength',editStrength.value)};
editStrengthValue.onchange=()=>storePreference('edit_strength',syncStrength(editStrengthValue.value));
function add(role,text,badge){const d=document.createElement('div');d.className='msg '+role;if(badge){const b=document.createElement('div');b.className='badge';b.textContent=badge;d.appendChild(b);d.appendChild(document.createElement('br'))}d.appendChild(document.createTextNode(text||''));chat.appendChild(d);document.querySelector('.chatScroll').scrollTop=document.querySelector('.chatScroll').scrollHeight;return d}
let paintUndo=null;
function resetPaint(){ctx.clearRect(0,0,canvas.width,canvas.height);hasMask=false;syncEditStrengthControls();paintUndo=null;painting=false;lastPoint=null;document.getElementById('undoPaint').disabled=true}
document.getElementById('undoPaint').onclick=()=>{if(!paintUndo||painting)return;ctx.putImageData(paintUndo.image,0,0);hasMask=paintUndo.hasMask;syncEditStrengthControls();paintUndo=null;document.getElementById('undoPaint').disabled=true;};
function showEditor(url,autoEnable=false){source.onload=()=>{canvas.width=source.naturalWidth;canvas.height=source.naturalHeight;resetPaint()};source.src=url;paintEnabled=autoEnable;toggle.classList.toggle('active',paintEnabled);toggle.textContent='範囲指定：'+(paintEnabled?'ON':'OFF');canvas.style.pointerEvents=paintEnabled?'auto':'none'}
function selectAsSource(info,url,thumb,openEdit=true){currentSourceInfo=info;showEditor(url,false);if(currentSelectedThumb)currentSelectedThumb.classList.remove('selected');currentSelectedThumb=thumb||null;if(currentSelectedThumb)currentSelectedThumb.classList.add('selected');if(openEdit)showView('edit')}
function pasteFinalPromptIntoChat(prompt){
 if(!prompt)return;input.value=prompt;showView('chat');input.focus();
 status.textContent='最終プロンプトを貼り付けました。「最終プロンプトから画像生成」でそのまま生成できます。';
}
function prepareImageExploration(prompt){if(prompt&&window.prepareExplorationFromPrompt)window.prepareExplorationFromPrompt(prompt);}
function prepareImageInspiration(prompt){if(prompt&&window.prepareInspirationFromPrompt)window.prepareInspirationFromPrompt(prompt);}
function createImagePromptActions(prompt){
 const actions=document.createElement('div');actions.className='exploreActions';actions.style.margin='10px 0 0';
 for(const [label,action] of [['チャットに貼り付け',()=>pasteFinalPromptIntoChat(prompt)],['探索に貼り付け',()=>prepareImageExploration(prompt)],['ひらめきに貼り付け',()=>prepareImageInspiration(prompt)]]){
  const button=document.createElement('button');button.type='button';button.className='mutedSmall';button.style.fontSize='11px';button.textContent=label;button.onclick=action;actions.append(button);
 }
 return actions;
}
function createImageOptionsMenu(copy,prompt){
 const menu=document.createElement('details');menu.className='imageOptionsMenu';
 const more=document.createElement('summary');more.textContent='⋮';more.setAttribute('aria-label','画像のメニュー');more.title='画像のメニュー';
 const items=document.createElement('div');items.className='imageOptionsMenuItems';items.append(copy);
 function action(label,handler){const btn=document.createElement('button');btn.type='button';btn.className='mutedSmall';btn.textContent=label;btn.onclick=()=>{menu.open=false;handler();};items.append(btn);}
 action('チャットに貼り付け',()=>pasteFinalPromptIntoChat(prompt));
 action('探索に貼り付け',()=>prepareImageExploration(prompt));
 action('ひらめきに貼り付け',()=>prepareImageInspiration(prompt));
 action('最終プロンプト',()=>{
  const backdrop=document.createElement('div');backdrop.className='modalBackdrop open';
  const dialog=document.createElement('div');dialog.className='modal';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label','最終プロンプト');
  const title=document.createElement('div');title.className='modalTitle';title.textContent='最終プロンプト';
  const text=document.createElement('textarea');text.className='imagePromptText';text.value=prompt;text.readOnly=true;text.setAttribute('aria-label','最終プロンプト');
  const close=document.createElement('button');close.type='button';close.className='mutedSmall';close.textContent='閉じる';close.onclick=()=>backdrop.remove();
  dialog.append(title,text,close);backdrop.append(dialog);backdrop.onclick=e=>{if(e.target===backdrop)backdrop.remove();};backdrop.onkeydown=e=>{if(e.key==='Escape')backdrop.remove();};
  document.body.append(backdrop);text.focus();
 });
 menu.append(more,items);return menu;
}
function updateGalleryCopyButton(button,galleryId){
 if(galleryId!==undefined)button.dataset.copyGalleryId=galleryId||'';
 const copied=!!button.dataset.copyGalleryId&&galleryItems.has(button.dataset.copyGalleryId);
 button.classList.toggle('galleryCopied',copied);
 button.textContent=copied?'✓ コピー済み':'ギャラリーにコピー';
}
function refreshGalleryCopyButtons(){document.querySelectorAll('[data-copy-gallery-id]').forEach(button=>updateGalleryCopyButton(button));}
function addImages(items){if(!items||!items.length)return;emptyGallery.style.display='none';items.forEach((item,idx)=>{totalImages++;const gid=item.info&&item.info.gallery_id;if(gid)galleryItems.set(gid,item);const t=document.createElement('div');t.className='thumb';t.dataset.galleryId=gid||'';const im=document.createElement('img');im.src=item.url;im.alt=`生成画像${totalImages}`;const no=document.createElement('div');no.className='thumbNo';no.textContent='#'+totalImages;const fav=document.createElement('button');fav.type='button';fav.className='favoriteBadge'+(item.favorite?' on':'');fav.textContent=item.favorite?'★':'☆';fav.title=item.favorite?'お気に入り解除':'お気に入り登録';fav.onclick=async e=>{e.stopPropagation();await setFavorite(item,!item.favorite,fav)};const menu=document.createElement('button');menu.type='button';menu.className='thumbMenu';menu.textContent='⋮';menu.title='生成情報を見る';menu.onclick=e=>{e.stopPropagation();openPromptModal(item)};t.append(im,no,fav,menu);t.onclick=()=>{selectAsSource(item.info,item.url,t,true);status.textContent='編集対象を選択しました。'};gallery.prepend(t);if(idx===0)selectAsSource(item.info,item.url,t,false)});galleryCount.textContent=totalImages+'枚';refreshGalleryCopyButtons()}
async function restoreGallery(){try{const r=await fetch('/api/gallery');const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'画像一覧を取得できません');if(d.images&&d.images.length)addImages(d.images);}catch(e){status.textContent='画像一覧の復元に失敗しました：'+e.message}}
toggle.onclick=()=>{paintEnabled=!paintEnabled;if(!paintEnabled){resetPaint();painting=false;lastPoint=null;}toggle.classList.toggle('active',paintEnabled);toggle.textContent='範囲指定：'+(paintEnabled?'ON':'OFF');canvas.style.pointerEvents=paintEnabled?'auto':'none'};document.getElementById('clear').onclick=resetPaint;canvas.style.pointerEvents='none';
function pt(e){const r=canvas.getBoundingClientRect();return{x:(e.clientX-r.left)*canvas.width/r.width,y:(e.clientY-r.top)*canvas.height/r.height}}
function stroke(a,b){const r=canvas.getBoundingClientRect(),scale=canvas.width/r.width;ctx.strokeStyle='rgb(255,70,70)';ctx.lineWidth=Number(brush.value)*scale;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();if(!hasMask){hasMask=true;syncEditStrengthControls();}}
canvas.onpointerdown=e=>{if(!paintEnabled||painting||!canvas.width||!canvas.height)return;paintUndo={image:ctx.getImageData(0,0,canvas.width,canvas.height),hasMask};document.getElementById('undoPaint').disabled=false;painting=true;canvas.setPointerCapture(e.pointerId);lastPoint=pt(e);stroke(lastPoint,lastPoint)};canvas.onpointermove=e=>{if(!painting)return;const p=pt(e);stroke(lastPoint,p);lastPoint=p};canvas.onpointerup=()=>{painting=false;lastPoint=null;document.getElementById('undoPaint').disabled=!paintUndo};canvas.onpointercancel=canvas.onpointerup;
function exportOutlineImage(){
 if(!hasMask||!source.complete||!source.naturalWidth)throw Error('編集元画像と塗った範囲を確認してください。');
 const out=document.createElement('canvas');out.width=canvas.width;out.height=canvas.height;
 const ox=out.getContext('2d');ox.drawImage(source,0,0,out.width,out.height);
 const original=ox.getImageData(0,0,out.width,out.height);
 const guide=RegionOutline.create(original.data,ctx.getImageData(0,0,canvas.width,canvas.height).data,out.width,out.height);
 original.data.set(guide.pixels);ox.putImageData(original,0,0);
 return {image_data:out.toDataURL('image/png'),color:guide.color};
}
function exportMask(){if(!hasMask)return null;const out=document.createElement('canvas');out.width=canvas.width;out.height=canvas.height;const ox=out.getContext('2d');ox.fillStyle='black';ox.fillRect(0,0,out.width,out.height);const img=ctx.getImageData(0,0,canvas.width,canvas.height),dst=ox.getImageData(0,0,out.width,out.height);for(let i=0;i<img.data.length;i+=4){if(img.data[i+3]>8){dst.data[i]=255;dst.data[i+1]=255;dst.data[i+2]=255;dst.data[i+3]=255}}ox.putImageData(dst,0,0);return out.toDataURL('image/png')}
const generationModel=document.getElementById('generationModel'),editModel=document.getElementById('editModel');
const characterSelect=document.getElementById('characterSelect');
const characterGrid=document.getElementById('characterGrid'),characterEmpty=document.getElementById('characterEmpty'),characterDetail=document.getElementById('characterDetail');
const characterModal=document.getElementById('characterModal'),characterTarget=document.getElementById('characterTarget'),characterName=document.getElementById('characterName'),characterNotes=document.getElementById('characterNotes'),characterModalError=document.getElementById('characterModalError');
function selectedCharacter(){return characters.find(c=>c.id===selectedCharacterId)||null}
let promptModalItem=null;
function modelLabel(id){
 if(id==='qwen21_turbo')return 'Qwen-Image-2.1-Turbo';
 if(id==='qwen21')return 'Qwen-Image-2.1';
 if(id==='z_image_turbo')return 'Z-Image Turbo';
 return id ? '未登録のモデル（'+id+'）' : 'モデル不明';
}
function modeLabel(v){return ({new:'新規生成',full:'全体編集',inpaint:'部分編集',import:'インポート'})[v]||v||'-'}
function openPromptModal(item){promptModalItem=item;const m=item.metadata||{};document.getElementById('savedUserInstruction').value=m.user_instruction||'（この画像には保存された指示がありません）';document.getElementById('savedFinalPrompt').value=m.final_prompt||'（この画像には保存された最終プロンプトがありません）';let bits=[];if(m.original_filename)bits.push('ファイル: '+m.original_filename);if(m.model)bits.push('モデル: '+modelLabel(m.model));if(m.mode)bits.push('種類: '+modeLabel(m.mode));if(m.width&&m.height)bits.push('サイズ: '+m.width+' × '+m.height);if(m.character)bits.push('キャラクター: '+m.character);if(m.created_at)bits.push('生成: '+m.created_at);document.getElementById('savedPromptMeta').textContent=bits.join(' / ')||'旧ギャラリー画像のため生成情報はありません。';document.getElementById('reusePrompt').disabled=!m.final_prompt;document.getElementById('explorePromptImage').disabled=!m.final_prompt;document.getElementById('inspirePromptImage').disabled=!m.final_prompt;document.getElementById('promptModal').classList.add('open')}
function closePromptModal(){document.getElementById('promptModal').classList.remove('open');promptModalItem=null}
document.getElementById('closePrompt').onclick=closePromptModal;document.getElementById('promptModal').onclick=e=>{if(e.target===document.getElementById('promptModal'))closePromptModal()};
document.getElementById('reusePrompt').onclick=()=>{const prompt=promptModalItem?.metadata?.final_prompt;if(!prompt)return;closePromptModal();pasteFinalPromptIntoChat(prompt);};
document.getElementById('explorePromptImage').onclick=()=>{const prompt=promptModalItem?.metadata?.final_prompt;if(!prompt)return;closePromptModal();prepareImageExploration(prompt);};
document.getElementById('inspirePromptImage').onclick=()=>{const prompt=promptModalItem?.metadata?.final_prompt;if(!prompt)return;closePromptModal();prepareImageInspiration(prompt);};
document.getElementById('promptCharacter').onclick=()=>{if(!promptModalItem)return;const item=promptModalItem;closePromptModal();openCharacterModal(item.info,item.url)};
async function setFavorite(item,value,button=null){const gid=item.info&&item.info.gallery_id;if(!gid)return;try{const r=await fetch('/api/gallery/favorite',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_id:gid,favorite:value})});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'更新できませんでした');item.favorite=!!d.favorite;if(button){button.classList.toggle('on',item.favorite);button.textContent=item.favorite?'★':'☆';button.title=item.favorite?'お気に入り解除':'お気に入り登録'}const thumb=gallery.querySelector('[data-gallery-id="'+gid+'"]');if(thumb){const b=thumb.querySelector('.favoriteBadge');if(b){b.classList.toggle('on',item.favorite);b.textContent=item.favorite?'★':'☆';b.title=item.favorite?'お気に入り解除':'お気に入り登録'}}status.textContent=item.favorite?'お気に入りに登録しました。':'お気に入りを解除しました。';}catch(e){status.textContent='お気に入り更新エラー：'+e.message}}
function clearSelectedIfDeleted(gid){if(currentSourceInfo&&currentSourceInfo.gallery_id===gid){currentSourceInfo=null;currentSelectedThumb=null;source.removeAttribute('src');resetPaint();}}
async function deleteGalleryItem(item){const gid=item&&item.info&&item.info.gallery_id;if(!gid)return;if(!confirm('この画像をギャラリーから完全に削除しますか？'))return;try{const r=await fetch('/api/gallery/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_id:gid})});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'削除できませんでした');const thumb=gallery.querySelector('[data-gallery-id="'+gid+'"]');if(thumb)thumb.remove();galleryItems.delete(gid);refreshGalleryCopyButtons();clearSelectedIfDeleted(gid);totalImages=Math.max(0,totalImages-1);galleryCount.textContent=totalImages+'枚';emptyGallery.style.display=totalImages?'none':'block';closePromptModal();status.textContent='画像を削除しました。';}catch(e){status.textContent='画像削除エラー：'+e.message}}
document.getElementById('deletePromptImage').onclick=()=>{if(promptModalItem)deleteGalleryItem(promptModalItem)};document.getElementById('deleteNonFavorites').onclick=async()=>{const favorites=[...galleryItems.values()].filter(x=>x.favorite).length;const nonfav=totalImages-favorites;if(nonfav<=0){status.textContent='削除対象のお気に入り以外の画像はありません。';return}if(!confirm('お気に入り以外の画像 '+nonfav+'枚を完全に削除します。★付き画像だけ残します。よろしいですか？'))return;try{const r=await fetch('/api/gallery/delete-nonfavorites',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'一括削除できませんでした');gallery.innerHTML='';galleryItems.clear();refreshGalleryCopyButtons();totalImages=0;currentSourceInfo=null;currentSelectedThumb=null;source.removeAttribute('src');resetPaint();await restoreGallery();emptyGallery.style.display=totalImages?'none':'block';status.textContent='お気に入り以外を '+d.deleted+'枚削除しました。';}catch(e){status.textContent='一括削除エラー：'+e.message}};
function imageUrl(c,img){return '/character-image?id='+encodeURIComponent(c.id)+'&file='+encodeURIComponent(img.filename)}
function renderCharacterSelect(){const keep=selectedCharacterId;characterSelect.innerHTML='<option value="">使用しない</option>';characters.forEach(c=>{const o=document.createElement('option');o.value=c.id;o.textContent=c.name+'（'+c.images.length+'枚）';characterSelect.appendChild(o)});selectedCharacterId=characters.some(c=>c.id===keep)?keep:'';characterSelect.value=selectedCharacterId;storePreference('character_id',selectedCharacterId)}
function renderCharacterManager(){characterGrid.innerHTML='';characterEmpty.style.display=characters.length?'none':'block';characters.forEach(c=>{const card=document.createElement('button');card.type='button';card.className='characterCard'+(c.id===selectedCharacterId?' active':'');const img=document.createElement('img');img.alt=c.name;if(c.images.length)img.src=imageUrl(c,c.images[0]);const nm=document.createElement('div');nm.className='characterCardName';nm.textContent=c.name;const meta=document.createElement('div');meta.className='characterCardMeta';meta.textContent='参照画像 '+c.images.length+'/4';card.append(img,nm,meta);card.onclick=()=>{selectedCharacterId=c.id;characterSelect.value=c.id;storePreference('character_id',c.id);renderCharacterManager();renderCharacterDetail(c)};characterGrid.appendChild(card)});const c=selectedCharacter();if(c)renderCharacterDetail(c);else{characterDetail.style.display='none';characterDetail.innerHTML=''}}
function renderCharacterDetail(c){if(!c)return;characterDetail.style.display='block';characterDetail.innerHTML='';const head=document.createElement('div');head.style.display='flex';head.style.alignItems='center';head.style.justifyContent='space-between';head.style.gap='12px';const title=document.createElement('div');title.className='editTitle';title.textContent=c.name;const deleteCharacter=document.createElement('button');deleteCharacter.type='button';deleteCharacter.className='dangerSmall';deleteCharacter.textContent='キャラクターを削除';deleteCharacter.onclick=()=>removeCharacter(c.id,c.name);head.append(title,deleteCharacter);const notes=document.createElement('div');notes.className='editorHelp';notes.textContent=c.notes||'外見メモはまだ登録されていません。';const meta=document.createElement('div');meta.className='sub';meta.textContent='参照画像 '+c.images.length+'/4';const imgs=document.createElement('div');imgs.className='characterDetailImages';c.images.forEach(img=>{const wrap=document.createElement('div');const im=document.createElement('img');im.src=imageUrl(c,img);im.alt=c.name+'の参照画像';const del=document.createElement('button');del.type='button';del.className='mutedSmall';del.style.width='100%';del.style.marginTop='5px';del.textContent='画像を削除';del.onclick=()=>removeCharacterImage(c.id,img.filename);wrap.append(im,del);imgs.appendChild(wrap)});characterDetail.append(head,notes,meta,imgs)}
async function removeCharacter(id,name){if(!confirm('キャラクター「'+name+'」を参照画像ごと削除しますか？'))return;try{const r=await fetch('/api/characters/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({character_id:id})});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'削除できませんでした');characters=d.characters||[];if(selectedCharacterId===id)selectedCharacterId='';renderCharacterSelect();renderCharacterManager();status.textContent='キャラクター「'+name+'」を削除しました。';}catch(e){status.textContent='削除エラー：'+e.message}}
async function removeCharacterImage(id,filename){if(!confirm('この参照画像を削除しますか？'))return;try{const r=await fetch('/api/characters/remove-image',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({character_id:id,filename})});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'削除できませんでした');characters=d.characters||[];renderCharacterSelect();renderCharacterManager();status.textContent='参照画像を削除しました。'}catch(e){status.textContent='削除エラー：'+e.message}}
async function refreshCharacters(){try{const r=await fetch('/api/characters');const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'キャラクター一覧を取得できません');characters=d.characters||[];renderCharacterSelect();renderCharacterManager()}catch(e){status.textContent='キャラクターの読み込みに失敗しました：'+e.message}}
characterSelect.onchange=()=>{selectedCharacterId=characterSelect.value;storePreference('character_id',selectedCharacterId);renderCharacterManager()};
function syncEditStrengthControls(){
 const supportsStrength=Boolean(window.imageModelSupportsStrength?.(editModel.value));
 editStrength.disabled=!supportsStrength||hasMask;
 editStrengthValue.disabled=editStrength.disabled;
 document.getElementById('strengthHint').textContent=!supportsStrength
  ? 'Qwenの指示編集ではこの変更強度を使用しません。'
  : hasMask ? '範囲指定中は変更強度1で固定です。' : 'マスクなしの全体編集に使います。';
}
function syncModelControls(){syncEditStrengthControls();toggle.disabled=false;brush.disabled=false;localStorage.setItem('flashnext_generation_model',generationModel.value);localStorage.setItem('flashnext_edit_model',editModel.value)}

generationModel.value=localStorage.getItem('flashnext_generation_model')||'qwen21_turbo';editModel.value=localStorage.getItem('flashnext_edit_model')||'qwen21_turbo';generationModel.onchange=syncModelControls;editModel.onchange=syncModelControls;syncModelControls();
function openCharacterModal(info,url){registeringSource=info;characterModalError.textContent='';document.getElementById('registerImageInfo').textContent='選択した生成画像を保存します。';document.getElementById('registerImagePreview').src=url||('/image?'+new URLSearchParams({filename:info.filename||'',subfolder:info.subfolder||'',type:info.type||'output'}).toString());characterTarget.innerHTML='<option value="__new__">新しいキャラクターを作成</option>';characters.forEach(c=>{const o=document.createElement('option');o.value=c.id;o.textContent=c.name+'（'+c.images.length+'/4枚）';o.disabled=c.images.length>=4;characterTarget.appendChild(o)});characterTarget.value='__new__';characterName.disabled=false;characterName.value='';characterNotes.value='';characterModal.classList.add('open');characterName.focus()}
characterTarget.onchange=()=>{const existing=characters.find(c=>c.id===characterTarget.value);if(existing){characterName.value=existing.name;characterName.disabled=true;characterNotes.value=existing.notes||'';}else{characterName.disabled=false;characterName.value='';characterNotes.value='';}};
function closeCharacterModal(){characterModal.classList.remove('open');registeringSource=null}
document.getElementById('cancelCharacter').onclick=closeCharacterModal;characterModal.onclick=e=>{if(e.target===characterModal)closeCharacterModal()};
document.getElementById('saveCharacter').onclick=async()=>{if(!registeringSource)return;const target=characterTarget.value;const name=characterName.value.trim();if(target==='__new__'&&!name){characterModalError.textContent='キャラクター名を入力してください。';return}const btn=document.getElementById('saveCharacter');btn.disabled=true;btn.textContent='保存中…';characterModalError.textContent='';try{const r=await fetch('/api/characters',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target_id:target,name,notes:characterNotes.value,source_image:registeringSource})});const d=await readApiResponse(r);if(!r.ok)throw new Error(d.error||'保存できませんでした');characters=d.characters||[];selectedCharacterId=d.character.id;renderCharacterSelect();renderCharacterManager();closeCharacterModal();showView('characters');status.textContent='キャラクター「'+d.character.name+'」に画像を保存しました。';}catch(e){characterModalError.textContent=e.message;}finally{btn.disabled=false;btn.textContent='保存する'}};
document.getElementById('registerFromSelected').onclick=()=>{if(!currentSourceInfo){status.textContent='先に右側の画像をクリックして編集対象にしてください。';return}openCharacterModal(currentSourceInfo,source.src)};
async function submit(fromEdit=false,chatAction='generate'){
 try{await window.flushParameterSaves?.();}catch(e){status.textContent=e.message;return;}
 if(window.explorationBusy||window.inspirationBusy||window.galleryImportBusy){status.textContent='実行中の処理が終わってから実行してください。';return;}
 const direct=!fromEdit&&chatAction==='direct';
 const box=fromEdit?editInput:input;
 const text=box.value.trim();
 if(!text)return;
 if(fromEdit&&!currentSourceInfo){status.textContent='先に右の画像をクリックして編集対象を選んでください。';return}
 const count=fromEdit||chatAction==='consult'?1:getCount(),width=snapDim(widthInput),height=snapDim(heightInput);
 if(!fromEdit)saveImageSize();
 box.value='';
 add('user',text+(count>1?`\n（生成枚数: ${count}）`:''));
 const userEntry={role:'user',content:text};
 history.push(userEntry);
 send.disabled=true;directSend.disabled=true;consult.disabled=true;editSend.disabled=true;
 status.textContent=fromEdit?(hasMask?'部分編集用プロンプトを作成しています…':'全体編集用プロンプトを作成しています…'):(chatAction==='consult'?'LLMと相談しています…':direct?'入力したプロンプトで画像を生成しています…':'新規生成用プロンプトを作成しています…');
 const progressMode=fromEdit?(hasMask?'部分編集':'全体編集'):(chatAction==='consult'?'LLMとの会話':'新規画像生成');
 const progressSize=fromEdit?'':' ｜ '+(window.imageSizeLabel?.(generationModel.value,width,height)??(width+'×'+height));
 const progressModel=fromEdit?editModel.options[editModel.selectedIndex].text:generationModel.options[generationModel.selectedIndex].text;
 cancelAfterCurrent=false;
 const canCancelAfterCurrent=!fromEdit&&chatAction!=='consult'&&count>1;
 progressCancel.classList.toggle('show',!direct||canCancelAfterCurrent);progressCancel.disabled=false;
 progressCancel.textContent=direct?'この1枚の完了後に中止':'中止';
 startProgress(status.textContent,progressMode+' ｜ '+(chatAction==='consult'?'LLM':progressModel)+(chatAction==='consult'?'':(progressSize+(count>1?' ｜ 1/'+count+'枚':''))));
 let shouldReleaseComfy=false,generatedCount=0,completionMessage="";
 const progressId=crypto.randomUUID();
 const previousCancel=progressCancel.onclick;
 let running=true;
 if(!direct)progressCancel.onclick=async()=>{
  if(firstImageStage==='image'){previousCancel();return;}
  progressCancel.disabled=true;
  try{
   const result=await cancelLlmWait(progressId);
   if(!running)return;
   if(!result.cancelled&&result.active&&chatAction!=='consult')previousCancel();
   else if(!result.cancelled)progressCancel.disabled=false;
  }catch(e){if(running){progressCancel.disabled=false;status.textContent=e.message;}}
 };
 let firstImageStage='prompt',stagePollBusy=false,replyShown=false,assistantReplyEntry=null;
 function showReplyEarly(reply,mode){
  if(replyShown)return;replyShown=true;
  const labels={consult:'会話',new:'新規生成',full:'全体編集',inpaint:'部分編集'};
  add('assistant',reply||'',labels[mode]||mode);
  assistantReplyEntry={role:'assistant',content:reply||''};history.push(assistantReplyEntry);
 }
 const stagePoll=setInterval(async()=>{
   if(!running||stagePollBusy)return;stagePollBusy=true;
   try{
     const response=await fetch('/api/request-stage?id='+encodeURIComponent(progressId),{cache:'no-store'});
     if(response.ok){const progress=await readApiResponse(response),stage=progress.stage;

       if(running&&stage==='image'&&firstImageStage!=='image'){
         firstImageStage='image';
         if(!cancelAfterCurrent){
          progressCancel.classList.toggle('show',canCancelAfterCurrent);
          progressCancel.textContent='この1枚の完了後に中止';
         }
         if(!cancelAfterCurrent)updateProgress('画像を生成しています…'+(count>1?' 1 / '+count:''),progressMode+' ｜ '+progressModel+progressSize+' ｜ 1/'+count+'枚');
       }
     }
   }catch(_e){}finally{stagePollBusy=false}
 },300);
 try{
  const mask=exportMask();
  const markedImage=fromEdit&&hasMask&&(window.imageModelUsesOutline?.(editModel.value)??editModel.value.startsWith('qwen21'))?exportOutlineImage():null;
  // 複数枚の新規生成ではLLMでプロンプトを1回準備し、まず1枚目を生成する。
  const firstCount=(!fromEdit&&chatAction!=='consult'&&count>1)?1:count;
  const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({progress_id:progressId,cancel_id:direct?undefined:progressId,history,direct_prompt:direct?text:null,mask,marked_image:markedImage,count:firstCount,width,height,source_image:currentSourceInfo,from_edit:fromEdit,chat_action:chatAction,edit_strength:syncStrength(editStrength.value),selected_character_id:fromEdit?'':(window.characterForGeneration?window.characterForGeneration():selectedCharacterId),generation_model:generationModel.value,edit_model:editModel.value})});
  const data=await readApiResponse(r);
  if(!r.ok)throw new Error(data.error||'エラー');
  if(data.cancelled){
   const index=history.indexOf(userEntry);
   if(index!==-1)history.splice(index,1);
   status.textContent='LLMの待機を中止しました。';
   return;
  }
  firstImageStage='image';
  if(canCancelAfterCurrent&&!cancelAfterCurrent)progressCancel.textContent='この1枚の完了後に中止';
  showReplyEarly(data.reply,data.mode);
  // Keep the actual new-image prompt in LLM context without changing the visible reply.
  const assistantHistory=(!fromEdit&&chatAction==='generate'&&data.final_prompt)
    ? (data.reply||'')+'\n\n【この新規画像生成に実際に使用した最終プロンプト】\n'+data.final_prompt
    : (data.reply||'');
  if(assistantReplyEntry)assistantReplyEntry.content=assistantHistory;
  if(data.images&&data.images.length){addImages(data.images);generatedCount+=data.images.length;shouldReleaseComfy=true;if(fromEdit)showView('edit');}

  // 残りは完成するたびに1枚ずつ追加。LLMは再実行しない。
  if(!fromEdit&&chatAction!=='consult'&&count>1){
    if(!data.final_prompt)throw new Error('最終プロンプトを取得できませんでした。');
    for(let i=2;i<=count;i++){
      if(cancelAfterCurrent)break;
      status.textContent=`画像を生成しています… ${i} / ${count}`;
      updateProgress(`画像を生成しています… ${i} / ${count}`,progressMode+' ｜ '+progressModel+progressSize+' ｜ '+i+'/'+count+'枚');
      const rr=await fetch('/api/generate-from-prompt',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:data.final_prompt,image_batch_id:progressId,user_instruction:text,width,height,model:generationModel.value,selected_character_id:window.characterForGeneration?window.characterForGeneration():selectedCharacterId})});
      const dd=await readApiResponse(rr);
      if(!rr.ok)throw new Error(dd.error||`${i}枚目の生成に失敗しました。`);
      if(dd.image){addImages([dd.image]);generatedCount++;shouldReleaseComfy=true;}
      if(cancelAfterCurrent)break;
    }
  }
  if((data.images&&data.images.length)||count>1){
    if(cancelAfterCurrent){status.textContent='';updateProgress('生成を中止しました','完成済み画像はそのまま残しています。');}else{status.textContent=fromEdit?(data.mode==='inpaint'?(Boolean(window.imageModelSupportsStrength?.(editModel.value))?'マスク編集しました。':'輪郭線の目印を使って編集しました。'):'全体編集しました。'):'新規画像を生成しました。右の画像をクリックすると編集できます。';}
  }else if(data.mode==='consult'){status.textContent='会話のみです。画像は生成していません。内容が固まったら「画像生成」を押してください。';}
  else status.textContent='';
  if(generatedCount)completionMessage=(cancelAfterCurrent&&generatedCount<count?'画像生成を中止しました':'画像生成が完了しました')+'（'+generatedCount+'枚）';
 }catch(err){
   add('assistant','エラー：'+err.message);status.textContent='';
 }finally{
   running=false;
   progressCancel.onclick=previousCancel;
   clearInterval(stagePoll);
   // 設定がオンの場合、連続生成の終了後にComfyUIのモデルを解放する。
   // 画像の途中では解放しない。次のローカルLLM要求で使えるVRAMを増やす。
   if(shouldReleaseComfy){
     try{await fetch('/api/comfy/free',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch(_e){}
   }
   stopProgress();
   if(completionMessage)notifyImageCompletion(completionMessage);
   send.disabled=false;directSend.disabled=false;consult.disabled=false;editSend.disabled=false;
   (fromEdit?editInput:input).focus();
 }
}
consult.onclick=()=>submit(false,'consult');
send.onclick=()=>submit(false,'generate');
directSend.onclick=()=>submit(false,'direct');
editSend.onclick=()=>submit(true);
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();submit(false,'generate')}});
refreshCharacters();
restoreGallery();
editInput.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();submit(true)}});
</script></body></html>"""


HTML = gallery_import.install_html(inspiration.install_html(exploration.install_html(HTML)))
HTML = HTML.replace('</nav>', '<button class="tab" id="settingsTab">設定</button></nav>', 1)
HTML = HTML.replace('</main>', '<section id="settingsView" class="view"></section></main>', 1)
HTML = HTML.replace('</style>', '.app.settingsWorkspace{grid-template-columns:minmax(0,1fr)}\n.app.settingsWorkspace #leftPanel,.app.settingsWorkspace #rightPanel{display:none}\n.app.settingsWorkspace #workspace{grid-column:1/-1}\n#settingsView{overflow:auto;min-height:0}\n.modelSettingsPage{padding:24px;width:100%;box-sizing:border-box;margin:0}.modelSettingsPage>h2{margin:0;font-size:18px;font-weight:800}.modelSettingsPage>p{margin:8px 0 12px;line-height:1.6}\n.modelSettingsGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:20px;margin-top:24px}\n.modelSettingsCard .danger{background:#8f2630;color:#fff;border:1px solid #d65763}.modelSettingsCard .danger:hover{background:#ac303c}.modelSettingsCard{min-width:0;padding:22px;background:#171b22;border:1px solid #343d4b;border-radius:12px}\n.modelSettingField{display:grid;gap:6px;margin:12px 0;font-size:13px;min-width:0}\n.modelSettingField input,.modelSettingField textarea{box-sizing:border-box;width:100%;min-width:0;padding:8px;background:#10151e;color:#e5eaf3;border:1px solid #45536b;border-radius:6px}\n.modelSettingsCard button,#modelParameters button{margin:6px 6px 0 0}\n#modelParameters[hidden]{display:none}\n@media(max-width:700px){.modelSettingsPage{padding:16px}.app header{overflow-x:auto}.app.settingsWorkspace #workspace{height:auto;min-height:72vh}}\n' + '</style>', 1)

for _menu, _capability in (("generationModel", "generate"), ("editModel", "edit")):
    import re, html
    _options = ''.join('<option value="' + html.escape(m['id'], quote=True) + '"' + (' selected' if m['id'] == 'qwen21_turbo' else '') + '>' + html.escape(m['label']) + '</option>' for m in model_registry.listing() if m['capabilities'][_capability])
    HTML = re.sub(r'(<select id="' + _menu + r'"[^>]*>).*?(</select>)', lambda match: match[1] + _options + match[2], HTML, count=1)
HTML = HTML.replace('</head>', '<script src="/region_outline.js"></script></head>')
HTML = HTML.replace('</body>', '<script src="/model_settings.js"></script><script src="/gallery_select_delete.js"></script></body>')





def is_client_disconnect(exc):
    return isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)) or (
        isinstance(exc, OSError) and (exc.errno in (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED)
                                     or getattr(exc, "winerror", None) in (10053, 10054, 10058)))


def request_boundary(method):
    @wraps(method)
    def wrapped(self):
        try:
            self.check_request_source()
            return method(self)
        except Exception as exc:
            if is_client_disconnect(exc):
                self.note_disconnect()
                return
            return self.send_exception(exc)
    return wrapped


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def note_disconnect(self):
        if not getattr(self, "client_disconnected", False):
            print("[http] ブラウザーとの接続が切れたため応答を終了しました。保存済みデータは保持します。", flush=True)
        self.client_disconnected = True
        self.close_connection = True

    def check_request_source(self):
        port = self.server.server_port
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise HTTPRequestError(403, "SceneLabのコンソールウィンドウに表示されたURLから操作してください。")
        origin = self.headers.get("Origin")
        if (origin and origin != "http://" + host) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise HTTPRequestError(403, "別のサイトからSceneLabを操作することはできません。")
        if self.command == "POST" and self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPRequestError(415, "操作はJSON形式で送信してください。")

    def send_exception(self, exc):
        if isinstance(exc, HTTPRequestError):
            code = exc.status
        elif isinstance(exc, (RequestBusyError, BlockingIOError)):
            code = 409
        elif isinstance(exc, FileNotFoundError):
            code = 404
        elif isinstance(exc, (TimeoutError,)):
            code = 504
        elif isinstance(exc, (LLMConnectionError, LLMOutputError, urllib.error.URLError)):
            code = 502
        elif isinstance(exc, (ValueError, UnicodeError)):
            code = 400
        else:
            code = 500
        message = str(exc) or "処理に失敗しました。"
        if isinstance(exc, (json.JSONDecodeError, UnicodeError)):
            message = "JSONデータを読み取れませんでした。入力形式を確認してください。"
        return self.send_bytes(code, json.dumps({"error": message}, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_error(self, code, message=None, explain=None):
        return self.send_bytes(code, json.dumps({"error": message or "操作を受け付けられませんでした。"}, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_bytes(self, code, body, ctype):
        if getattr(self, "client_disconnected", False):
            return
        if code >= 400:
            try:
                payload = json.loads(body)
            except (ValueError, TypeError):
                payload = {"error": body.decode("utf-8", errors="replace")}
            if not isinstance(payload, dict):
                payload = {"error": str(payload)}
            labels = {400: "invalid_request", 403: "forbidden", 404: "not_found", 409: "busy", 415: "unsupported_media_type", 500: "internal_error", 501: "unsupported_method", 502: "upstream_error", 504: "timeout"}
            payload.setdefault("error", "処理に失敗しました。")
            payload["code"] = labels.get(code, "request_failed")
            body = json.dumps(payload, ensure_ascii=False).encode()
            ctype = "application/json; charset=utf-8"
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            if not is_client_disconnect(exc):
                raise
            self.note_disconnect()

    def handle_one_request(self):
        token = COMFY_REQUEST_CONNECTION.set(COMFY_CONNECTION)
        llm_token = LLM_REQUEST_CONNECTION.set(LLM_CONNECTION)
        try:
            super().handle_one_request()
        except OSError as exc:
            if not is_client_disconnect(exc):
                raise
            self.note_disconnect()
        finally:
            LLM_REQUEST_CONNECTION.reset(llm_token)
            COMFY_REQUEST_CONNECTION.reset(token)

    @request_boundary
    def do_GET(self):
        if self.path == "/api/llm/connection":
            return self.send_bytes(200, json.dumps(public_llm_connection(LLM_CONNECTION)).encode(), "application/json; charset=utf-8")
        if self.path == "/api/comfy/connection":
            payload = json.dumps(current_comfy_connection()).encode()
            return self.send_bytes(200, payload, "application/json; charset=utf-8")
        if self.path == "/gallery_select_delete.js":
            return self.send_bytes(200, (HERE / "gallery_select_delete.js").read_bytes(), "application/javascript; charset=utf-8")
        if self.path == "/region_outline.js":
            return self.send_bytes(200, (HERE / "region_outline.js").read_bytes(), "application/javascript; charset=utf-8")
        if self.path == "/model_settings.js":
            return self.send_bytes(200, (HERE / "model_settings.js").read_bytes(), "application/javascript; charset=utf-8")
        if self.path in ("/api/models", "/api/model-settings"):
            try:
                data = {"models": model_registry.listing()} if self.path == "/api/models" else model_registry.status(comfy_url())
                return self.send_bytes(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except Exception as exc:
                return self.send_exception(exc)
        if self.path == "/gallery_import.js":
            return self.send_bytes(200, (HERE / "gallery_import.js").read_bytes(), "application/javascript; charset=utf-8")
        if self.path == "/notifications.js":
            return self.send_bytes(200, (HERE / "notifications.js").read_bytes(), "application/javascript; charset=utf-8")
        if inspiration.handle_get(self):
            return
        if exploration.handle_get(self):
            return
        if self.path == "/" or self.path.startswith("/?"):
            return self.send_bytes(200, HTML.encode(), "text/html; charset=utf-8")
        if self.path == "/api/characters":
            payload = json.dumps({"characters": load_characters()}, ensure_ascii=False).encode()
            return self.send_bytes(200, payload, "application/json; charset=utf-8")
        if self.path.startswith("/api/request-stage?"):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            rid = query.get("id", [""])[0]
            with REQUEST_STAGES_LOCK:
                stage = REQUEST_STAGES.get(rid, "")
                early = REQUEST_REPLIES.get(rid, {})
                retry = dict(LLM_RETRY_PROGRESS.get(rid, {}))
            return self.send_bytes(200, json.dumps({"stage": stage, "retry": retry, **{k: v for k, v in early.items() if k in ("reply", "mode")}}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        if self.path == "/api/gallery":
            payload = json.dumps({"images": load_gallery()}, ensure_ascii=False).encode()
            return self.send_bytes(200, payload, "application/json; charset=utf-8")
        if self.path.startswith("/gallery-image?"):
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                gid = q.get("id", [""])[0]
                image_file = _gallery_entry_path(gid)
                if not image_file.is_file():
                    raise FileNotFoundError("画像が見つかりません。")
                return self.send_bytes(200, image_file.read_bytes(), "image/png")
            except Exception as e:
                return self.send_exception(e)
        if self.path.startswith("/character-image?"):
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                cid = q.get("id", [""])[0]
                filename = q.get("file", [""])[0]
                image_file = character_image_path(cid, filename)
                return self.send_bytes(200, image_file.read_bytes(), "image/png")
            except Exception as e:
                return self.send_exception(e)
        if self.path.startswith("/image?"):
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                p = {k: v[0] for k, v in q.items()}
                with urllib.request.urlopen(comfy_url() + "/view?" + urllib.parse.urlencode(p), timeout=60) as r:
                    return self.send_bytes(200, r.read(), r.headers.get("Content-Type", "image/png"))
            except Exception as e:
                return self.send_exception(e)
        self.send_bytes(404, b"Not found", "text/plain")

    @request_boundary
    def do_POST(self):
        global LAST_IMAGE
        if self.path not in ("/api/request-cancel", "/api/llm/connection", "/api/llm/models", "/api/comfy/connection", "/api/model-settings", "/chat", "/api/characters", "/api/characters/remove-image", "/api/characters/delete", "/api/gallery/favorite", "/api/gallery/delete", "/api/gallery/delete-nonfavorites", "/api/generate-from-prompt", "/api/comfy/free", "/api/explore/plan", "/api/explore/generate", "/api/explore/rate", "/api/explore/stop", "/api/explore/delete", "/api/explore/copy-gallery", "/api/explore/reset-scores", "/api/inspire/create", "/api/inspire/groups", "/api/inspire/expand", "/api/inspire/start", "/api/inspire/prepare", "/api/inspire/next", "/api/inspire/stop", "/api/inspire/adopt", "/api/inspire/copy-gallery", "/api/inspire/delete", "/api/gallery/import"):
            return self.send_bytes(404, b"Not found", "text/plain")
        cancel_token = None
        cancel_id = ""
        retry_token = None
        retry_id = ""
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n < 0:
                raise ValueError("Content-Lengthが不正です。")
            body = json.loads(self.rfile.read(n).decode())
            if not isinstance(body, dict):
                raise ValueError("操作データはJSONオブジェクトで送信してください。")
            if self.path in ("/api/inspire/create", "/api/inspire/expand", "/api/inspire/prepare", "/api/inspire/next", "/api/explore/plan"):
                retry_id = body.get("retry_progress_id", "")
                if isinstance(retry_id, str) and re.fullmatch(r"[a-zA-Z0-9-]{12,100}", retry_id):
                    def report_retry(attempt, limit, label):
                        with REQUEST_STAGES_LOCK:
                            LLM_RETRY_PROGRESS[retry_id] = {"attempt": attempt, "limit": limit}
                    retry_token = LLM_RETRY_OBSERVER.set(report_retry)
            if self.path in ("/chat", "/api/explore/plan", "/api/inspire/create", "/api/inspire/expand", "/api/inspire/prepare", "/api/inspire/next"):
                cancel_id = body.get("cancel_id")
                if isinstance(cancel_id, str) and re.fullmatch(r"[a-zA-Z0-9-]{12,100}", cancel_id):
                    state = {"event": threading.Event(), "committed": False}
                    with REQUEST_STAGES_LOCK:
                        if cancel_id in LLM_CANCEL_REQUESTS:
                            raise RequestBusyError("このリクエストはすでに実行中です。")
                        LLM_CANCEL_REQUESTS[cancel_id] = state
                    cancel_token = LLM_CANCEL_STATE.set(state)
            if self.path in ("/api/request-cancel", "/api/llm/connection", "/api/llm/models", "/api/comfy/connection"):
                if self.path == "/api/request-cancel":
                    request_id = body.get("id")
                    with REQUEST_STAGES_LOCK:
                        state = LLM_CANCEL_REQUESTS.get(request_id) if isinstance(request_id, str) else None
                        cancelled = state is not None and not state["committed"]
                        if cancelled:
                            state["event"].set()
                    result = {"cancelled": cancelled, "active": state is not None}
                elif self.path == "/api/llm/connection":
                    result = save_llm_connection(body)
                elif self.path == "/api/llm/models":
                    settings = LLM_CONNECTION
                    credentials = llm_credentials_for(normalize_llm_url(body.get("url")), settings)
                    url = normalize_llm_url(body.get("url"), bool(credentials))
                    result = {"models": list_llm_models(url, **credentials)}
                else:
                    result = save_comfy_connection(body)
                return self.send_bytes(200, json.dumps(result).encode(), "application/json; charset=utf-8")
            if self.path == "/api/model-settings":
                result = model_registry.save(body, comfy_url())
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if self.path == "/api/gallery/import":
                result = gallery_import.import_image(body, globals())
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if self.path.startswith("/api/inspire/"):
                batch = mode_image_batch("inspire", body.get("session_id"), body.get("batch_id")) if self.path == "/api/inspire/next" else nullcontext()
                with batch:
                    result = inspiration.post(self.path, body, globals())
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if self.path.startswith("/api/explore/"):
                batch = mode_image_batch("explore", body.get("session_id"), body.get("round_id")) if self.path == "/api/explore/generate" else nullcontext()
                with batch:
                    result = exploration.post(self.path, body, globals())
                return self.send_bytes(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if self.path == "/api/comfy/free":
                with REQUEST_STAGES_LOCK:
                    MODE_IMAGE_BATCHES.clear()
                result = release_comfy_vram()
                payload = json.dumps(result, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/generate-from-prompt":
                prompt = str(body.get("prompt", "")).strip()
                if not prompt:
                    raise ValueError("画像生成プロンプトが空でした。")
                w = clamp_dimension(body.get("width"), 1024)
                h = clamp_dimension(body.get("height"), 1024)
                selected_model = str(body.get("model", "qwen21_turbo"))
                model_registry.get(selected_model)
                selected_character = None
                selected_character_id = str(body.get("selected_character_id", ""))
                if selected_character_id:
                    selected_character = get_character(selected_character_id)
                    if not selected_character:
                        raise ValueError("選択中のキャラクターが見つかりません。")
                IMAGE_PROMPT.set(prompt)
                batch_id = str(body.get("image_batch_id", ""))[:100]
                options = {}
                if batch_id:
                    with REQUEST_STAGES_LOCK:
                        entry = REQUEST_REPLIES.get(batch_id)
                        if entry and entry["mode"] == "new" and time.time() - entry["created"] <= 3600:
                            options["image_batch"] = entry["image_batch"]
                info = generate_image(prompt, w, h, "new", None, None, None, 0.70, selected_character, selected_model, **options)
                prompt = IMAGE_PROMPT.get() or prompt
                metadata = {
                    "user_instruction": str(body.get("user_instruction", "")),
                    "final_prompt": prompt,
                    "model": selected_model,
                    "mode": "new",
                    "width": w,
                    "height": h,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "character": selected_character.get("name") if selected_character else None
                }
                try:
                    image = save_gallery_image(info, metadata)
                except Exception:
                    image = {"url": make_image_url(info), "info": info}
                payload = json.dumps({"image": image, "model": selected_model}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/gallery/favorite":
                gid = str(body.get("gallery_id", ""))
                favorite = bool(body.get("favorite", False))
                set_gallery_favorite(gid, favorite)
                payload = json.dumps({"gallery_id": gid, "favorite": favorite}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/gallery/delete":
                gid = str(body.get("gallery_id", ""))
                delete_gallery_image(gid)
                payload = json.dumps({"deleted": gid}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/gallery/delete-nonfavorites":
                deleted = delete_nonfavorite_gallery_images()
                payload = json.dumps({"deleted": deleted}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/characters":
                character = save_character_image(body)
                payload = json.dumps({"character": character, "characters": load_characters()}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/characters/delete":
                cid = str(body.get("character_id", ""))
                delete_character(cid)
                payload = json.dumps({"characters": load_characters()}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            if self.path == "/api/characters/remove-image":
                cid = str(body.get("character_id", ""))
                filename = str(body.get("filename", ""))
                target = character_image_path(cid, filename)
                target.unlink()
                folder = _character_path(cid)
                meta_file = folder / "character.json"
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                data["images"] = [x for x in data.get("images", []) if x.get("filename") != filename]
                data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                tmp = folder / "character.json.tmp"
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(meta_file)
                payload = json.dumps({"characters": load_characters()}, ensure_ascii=False).encode()
                return self.send_bytes(200, payload, "application/json; charset=utf-8")
            from_edit = bool(body.get("from_edit"))
            chat_action = str(body.get("chat_action", "generate")).lower()
            consult_only = (not from_edit and chat_action == "consult")
            direct_generation = (not from_edit and chat_action == "direct")
            selected_character = None
            selected_character_id = str(body.get("selected_character_id", ""))
            # Character references belong to new-image/chat mode only.
            # Editing is based solely on the selected source image.
            if not from_edit and selected_character_id:
                selected_character = get_character(selected_character_id)
                if not selected_character:
                    raise ValueError("選択中のキャラクターが見つかりません。キャラクター一覧を更新してください。")
            progress_id = str(body.get("progress_id", ""))[:100]
            if progress_id:
                with REQUEST_STAGES_LOCK:
                    REQUEST_STAGES[progress_id] = "prompt"
            if direct_generation:
                direct_prompt = body.get("direct_prompt")
                if not isinstance(direct_prompt, str) or not direct_prompt.strip():
                    raise ValueError("最終プロンプトを入力してください。")
                result = {"reply": "入力されたプロンプトをそのまま使用して画像を生成しました。", "prompt": direct_prompt.strip()}
            else:
                guide = body.get("marked_image")
                region_edit = (from_edit and body.get("mask") and
                               model_registry.get(str(body.get("edit_model", "qwen21_turbo")))["backend"] == "qwen21")
                if region_edit:
                    if not isinstance(guide, dict) or guide.get("color") not in ("red", "blue", "green"):
                        raise ValueError("範囲指定の色を取得できません。画面を再読み込みしてください。")
                    result = call_llm(body.get("history", []), character=None, edit_context={"color": guide["color"]})
                else:
                    result = call_llm(body.get("history", []), consult=consult_only, character=selected_character)
            commit_llm_request()
            reply = str(result.get("reply", ""))
            prompt = str(result.get("prompt", "")).strip()
            mask = body.get("mask")
            marked_image = body.get("marked_image")
            count = clamp_count(body.get("count", 1))
            selected_source = body.get("source_image")
            try:
                edit_strength = max(0.10, min(1.0, float(body.get("edit_strength", 0.70))))
            except Exception:
                edit_strength = 0.70
            images = []
            need_mask = False

            # Fixed routing:
            # chat + consult => conversation only, no image
            # chat + generate => new generation
            # edit + mask => inpaint
            # edit + no mask => img2img (full)
            if consult_only:
                mode = "consult"
                out = json.dumps({
                    "reply": reply,
                    "mode": mode,
                    "images": [],
                    "need_mask": False
                }, ensure_ascii=False).encode()
                return self.send_bytes(200, out, "application/json; charset=utf-8")

            if from_edit:
                if not selected_source:
                    raise ValueError("先に右の画像をクリックして編集対象を選んでください。")
                source_image = selected_source
                mode = "inpaint" if mask else "full"
            else:
                source_image = None
                mode = "new"

            if not prompt:
                raise ValueError("画像生成プロンプトが空でした。")
            image_batch = {}
            if progress_id:
                with REQUEST_STAGES_LOCK:
                    for old_id, entry in list(REQUEST_REPLIES.items()):
                        if time.time() - entry["created"] > 3600:
                            REQUEST_REPLIES.pop(old_id, None)
                            REQUEST_STAGES.pop(old_id, None)
                    REQUEST_REPLIES[progress_id] = {
                        "reply": reply, "mode": mode, "created": time.time(), "image_batch": image_batch,
                    }

            w = clamp_dimension(body.get("width", result.get("width")), 1024)
            h = clamp_dimension(body.get("height", result.get("height")), 1024)
            generation_model = str(body.get("generation_model", "qwen21_turbo"))
            edit_model = str(body.get("edit_model", "qwen21_turbo"))
            selected_model = edit_model if from_edit else generation_model
            model_registry.get(selected_model)
            if progress_id:
                with REQUEST_STAGES_LOCK:
                    REQUEST_STAGES[progress_id] = "image"
            IMAGE_PROMPT.set(prompt)
            generated_infos = []
            options = {"image_batch": image_batch} if progress_id or count > 1 else {}
            for _ in range(count):
                generated_infos.append(generate_image(prompt, w, h, mode, source_image, mask, marked_image,
                                                      edit_strength, None if from_edit else selected_character,
                                                      selected_model, **options))
            prompt = IMAGE_PROMPT.get() or prompt
            LAST_IMAGE = generated_infos[0] if generated_infos else LAST_IMAGE
            images = []
            user_instruction = ""
            history_items = body.get("history", [])
            if isinstance(history_items, list):
                for hist_item in reversed(history_items):
                    if isinstance(hist_item, dict) and hist_item.get("role") == "user":
                        user_instruction = str(hist_item.get("content", "")).strip()
                        break
            gallery_metadata = {
                "user_instruction": user_instruction,
                "final_prompt": prompt,
                "model": selected_model,
                "mode": mode,
                "width": w,
                "height": h,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "character": selected_character.get("name") if selected_character else None
            }
            for info in generated_infos:
                try:
                    images.append(save_gallery_image(info, gallery_metadata))
                except Exception:
                    # Generation succeeded even if the persistent gallery copy fails.
                    images.append({"url": make_image_url(info), "info": info})

            out = json.dumps({
                "reply": reply,
                "mode": mode,
                "images": images,
                "need_mask": need_mask,
                "character": selected_character.get("name") if selected_character else None,
                "model": selected_model,
                "final_prompt": prompt
            }, ensure_ascii=False).encode()
            self.send_bytes(200, out, "application/json; charset=utf-8")
        except LLMCancelled:
            self.send_bytes(200, b'{"cancelled":true}', "application/json; charset=utf-8")
        except urllib.error.URLError as e:
            msg = "接続エラーです。ComfyUI（" + comfy_url() + "）の起動と接続先を確認してください。詳細: " + str(e)
            self.send_exception(urllib.error.URLError(msg))
        except Exception as e:
            self.send_exception(e)
        finally:
            if retry_token is not None:
                LLM_RETRY_OBSERVER.reset(retry_token)
                with REQUEST_STAGES_LOCK:
                    LLM_RETRY_PROGRESS.pop(retry_id, None)
            if cancel_token is not None:
                LLM_CANCEL_STATE.reset(cancel_token)
                with REQUEST_STAGES_LOCK:
                    LLM_CANCEL_REQUESTS.pop(cancel_id, None)


if __name__ == "__main__":
    server = None
    chosen_port = None
    for port in range(PORT, PORT + 10):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            chosen_port = port
            break
        except OSError:
            continue
    if server is None:
        raise OSError("8765〜8774番ポートを使用できません。古いSceneLabウィンドウを閉じて再実行してください。")
    url = f"http://127.0.0.1:{chosen_port}/?v=1.0"
    print("SceneLab v1.0")
    if chosen_port != PORT:
        print(f"※ {PORT}番ポートが使用中のため、{chosen_port}番ポートで起動します。")
    print(url)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.serve_forever()
