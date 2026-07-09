from __future__ import annotations

"""Tracked metrics: cost/performance instrumentation, reported alongside but
separate from correctness graders (agent_doc_bench.scorers). These answer
"how fast/expensive was this?" rather than "was this correct?", mirroring
the transcript/latency metrics split described in Anthropic's evals guide.
"""


# Every key collect() can emit, in a stable order — kept here (rather than
# re-derived elsewhere, e.g. results_fetcher.py) so callers that need the
# full key set can't drift out of sync with what collect() actually reports.
KEYS = [
    "metric_n_turns",
    "metric_n_toolcalls",
    "metric_n_total_tokens",
    "metric_time_to_first_token",
    "metric_time_to_last_token",
    "metric_output_tokens_per_sec",
]


def collect(run_outputs: dict) -> list[dict]:
    token_usage = run_outputs.get("token_usage") or {}
    latency = run_outputs.get("latency") or {}
    input_tokens = token_usage.get("input_tokens", 0)
    output_tokens = token_usage.get("output_tokens", 0)

    metrics = [
        {"key": "metric_n_turns", "score": run_outputs.get("steps", 0)},
        {"key": "metric_n_toolcalls", "score": run_outputs.get("n_toolcalls", 0)},
        {"key": "metric_n_total_tokens", "score": input_tokens + output_tokens},
        {"key": "metric_time_to_first_token", "score": latency.get("time_to_first_token")},
        {"key": "metric_time_to_last_token", "score": latency.get("time_to_last_token")},
        {"key": "metric_output_tokens_per_sec", "score": latency.get("output_tokens_per_sec")},
    ]
    return [m for m in metrics if m["score"] is not None]
