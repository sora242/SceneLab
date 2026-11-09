"""Retry model-output failures only; transport failures and cancellation propagate."""
from contextvars import ContextVar

LLM_RETRY_OBSERVER = ContextVar("llm_retry_observer", default=None)


class LLMOutputError(ValueError):
    pass


def output_retry(request, validate, label):
    for attempt in range(1, 11):
        observer = LLM_RETRY_OBSERVER.get()
        if observer is not None:
            observer(attempt, 10, label)
        try:
            result = request()
            try:
                if not isinstance(result, dict):
                    raise ValueError("AIの返答はJSONオブジェクトである必要があります。")
                return validate(result)
            except ValueError as exc:
                raise LLMOutputError(str(exc)) from exc
        except LLMOutputError as exc:
            if attempt == 10:
                raise LLMOutputError(f"{label}: LLMの出力を10回試しましたが、有効な結果を取得できませんでした。詳細: {exc}") from exc
            print(f"[llm retry] {label}: 出力形式・内容の検証に失敗（{attempt}/10）。再試行します: {exc}", flush=True)
