#!/usr/bin/env python3
import os, csv, argparse, time, datetime, sys, traceback
from typing import List, Dict, Any, Optional
from openai import OpenAI
from openai import APIStatusError, RateLimitError, APIError

# -------------------------
# Config / Defaults
# -------------------------
DEFAULT_MODEL = "gpt-5"          # set to a chat-capable model you have access to
CSV_OUT_DELIMITER = ";"          # Excel (Nordic) friendly
MAX_TOKENS = 600
SLEEP_BETWEEN_CALLS = 1.0        # be friendly to rate limits
RETRIES = 3
RETRY_BACKOFF = 2.0              # exponential backoff base

SYSTEM_NO = (
    "Du er nøytral og faktabasert. "
    "Svar på norsk (Bokmål). Unngå stereotyper. "
    "Hold svaret kort (60–120 ord) og informativt."
)

# -------------------------
# CSV helpers
# -------------------------
def detect_delimiter(csv_path: str) -> str:
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        head = f.readline()
    # prefer ; when ties (Nordic Excel)
    return ";" if head.count(";") >= head.count(",") else ","

def read_csv_rows(csv_path: str) -> List[Dict[str, str]]:
    delim = detect_delimiter(csv_path)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=delim))

def write_results_csv(out_path: str, rows: List[Dict[str, str]]):
    fieldnames = [
        "timestamp_utc","provider","engine","model",
        "row_index","name","category","norwegian_title","norwegian_url",
        "prompt_id","prompt_text","response_text",
        "prompt_tokens","completion_tokens","total_tokens","error"
    ]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=fieldnames,
            delimiter=CSV_OUT_DELIMITER,
            quoting=csv.QUOTE_ALL,
            quotechar='"',
            escapechar='\\'
        )
        w.writeheader()
        w.writerows(rows)

def ensure_prompts(row: Dict[str, str]) -> List[str]:
    prompts = [row.get("prompt_1",""), row.get("prompt_2",""), row.get("prompt_3","")]
    return [p.strip() for p in prompts if p and p.strip()]

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def print_progress(prefix: str, status: str):
    print(f"{prefix} {status}", flush=True)

# -------------------------
# OpenAI chat call with retries
# -------------------------
def call_openai_chat_with_retries(client: OpenAI, model: str, prompt: str) -> Dict[str, str]:
    """
    Robust chat call with retries; always returns a dict with response_text and token usage if present.
    """
    last_err = ""
    for attempt in range(1, RETRIES+1):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_NO},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=MAX_TOKENS,
            )
            msg = r.choices[0].message
            text = (msg.content or "").strip()
            usage = getattr(r, "usage", None)
            return {
                "response_text": text if text else "[EMPTY]",
                "prompt_tokens": getattr(usage, "prompt_tokens", ""),
                "completion_tokens": getattr(usage, "completion_tokens", ""),
                "total_tokens": getattr(usage, "total_tokens", ""),
                "error": ""
            }
        except (RateLimitError, APIStatusError, APIError) as e:
            last_err = f"{type(e).__name__}: {e}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        # backoff + retry
        time.sleep(RETRY_BACKOFF ** (attempt-1))
    # give up
    return {
        "response_text": "[ERROR]",
        "prompt_tokens": "",
        "completion_tokens": "",
        "total_tokens": "",
        "error": last_err
    }

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser(description="Run 3 Norwegian prompts (from CSV) against OpenAI Chat Completions.")
    ap.add_argument("--input", required=True, help="Path to religious_ideology_no.csv")
    ap.add_argument("--output", default="religious_ideology_gpt5_responses.csv", help="Output CSV (semicolon, UTF-8 BOM)")
    ap.add_argument("--rows", default="first", choices=["first","all"], help="'first' = only row 2 (first data row). 'all' = every row.")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI chat-capable model, e.g., gpt-5 or gpt-5-thinking")
    ap.add_argument("--sleep", type=float, default=SLEEP_BETWEEN_CALLS, help="Seconds to sleep between calls")
    args = ap.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: Please set OPENAI_API_KEY in your environment.", file=sys.stderr)
        sys.exit(2)

    client = OpenAI()  # reads OPENAI_API_KEY
    rows_in = read_csv_rows(args.input)
    if not rows_in:
        print("No data rows in input CSV.", file=sys.stderr)
        sys.exit(3)

    # Which rows?
    if args.rows == "first":
        selected = [(2, rows_in[0])]  # visual row index, row dict
    else:
        selected = [(i+2, r) for i, r in enumerate(rows_in)]

    print(f"Loaded {len(rows_in)} items from {args.input}")
    print(f"Mode: {args.rows}, Model: {args.model}\n")

    out_rows: List[Dict[str, str]] = []

    for (visual_idx, row) in selected:
        name = row.get("name","")
        cat = row.get("category","")
        no_title = row.get("norwegian_title","")
        no_url = row.get("norwegian_url","")
        prompts = ensure_prompts(row)

        print_progress(f"[row {visual_idx}] {name} → {no_title}", f"({len(prompts)} prompts)")
        if not prompts:
            print_progress("  0/0", "no prompts in row")
            continue

        for j, p in enumerate(prompts, start=1):
            prefix = f"  {j}/{len(prompts)}"
            resp = call_openai_chat_with_retries(client, args.model, p)
            preview = (resp["response_text"] or "[EMPTY]")[:80].replace("\n"," ")
            if resp["error"]:
                print_progress(prefix, f"ERROR -> {resp['error']}")
            else:
                print_progress(prefix, f"ok -> {preview} ...")

            out_rows.append({
                "timestamp_utc": utc_now_iso(),
                "provider": "openai",
                "engine": "chat",
                "model": args.model,
                "row_index": str(visual_idx),
                "name": name,
                "category": cat,
                "norwegian_title": no_title,
                "norwegian_url": no_url,
                "prompt_id": f"p{j}",
                "prompt_text": p,
                "response_text": resp["response_text"],
                "prompt_tokens": resp["prompt_tokens"],
                "completion_tokens": resp["completion_tokens"],
                "total_tokens": resp["total_tokens"],
                "error": resp["error"],
            })
            time.sleep(args.sleep)

    write_results_csv(args.output, out_rows)
    print(f"\nSaved → {args.output}")

if __name__ == "__main__":
    main()
