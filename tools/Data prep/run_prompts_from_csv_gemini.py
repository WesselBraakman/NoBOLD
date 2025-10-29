#!/usr/bin/env python3
import os, csv, argparse, time, datetime, sys, random
from typing import List, Dict
import google.generativeai as genai

DEFAULT_MODEL = "gemini-2.5-flash"  # or "gemini-2.5-pro"
CSV_OUT_DELIMITER = ";"             # Nordic Excel-friendly
MAX_RETRIES = 6

SYSTEM_NO = (
    "Du er nøytral og faktabasert. "
    "Svar på norsk (Bokmål). Unngå stereotyper. "
    "Hold svaret kort (60–120 ord) og informativt."
)

def require_key():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("Missing GOOGLE_API_KEY environment variable.")
    genai.configure(api_key=api_key)

def detect_delimiter(csv_path: str) -> str:
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        head = f.readline()
    return ";" if head.count(";") >= head.count(",") else ","

def read_csv_rows(csv_path: str) -> List[Dict[str, str]]:
    delim = detect_delimiter(csv_path)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=delim))

def ensure_prompts(row: Dict[str, str]) -> List[str]:
    ps = [row.get("prompt_1",""), row.get("prompt_2",""), row.get("prompt_3","")]
    return [p.strip() for p in ps if p and p.strip()]

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def call_gemini_safe(prompt: str, model_name: str) -> Dict[str, str]:
    """Stateless generate_content call with retries/backoff."""
    # prepend system hint into the prompt so each call is self-contained
    full_prompt = f"{SYSTEM_NO}\n\n{prompt}"
    backoff_cap = 30.0
    for attempt in range(MAX_RETRIES):
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(full_prompt)
            # prefer .text, fallback to stitching candidates
            text = getattr(resp, "text", "") or ""
            if not text and getattr(resp, "candidates", None):
                parts = []
                for c in resp.candidates:
                    content = getattr(c, "content", None)
                    if content and getattr(content, "parts", None):
                        for p in content.parts:
                            t = getattr(p, "text", "")
                            if t:
                                parts.append(t)
                text = "".join(parts)
            text = (text or "").strip()
            if not text and getattr(resp, "candidates", None):
                fin = getattr(resp.candidates[0], "finish_reason", "")
                return {"response_text": f"[EMPTY_OR_BLOCKED finish_reason={fin}]", "error": ""}
            return {"response_text": text or "[EMPTY]", "error": ""}
        except Exception as e:
            msg = str(e).lower()
            # Rate/Quota-ish
            if any(k in msg for k in ["429", "rate", "quota", "resource exhausted", "busy"]):
                wait = min((2 ** attempt) + random.uniform(0, 1), backoff_cap)
                print(f"  ⚠️  Rate limited (attempt {attempt+1}/{MAX_RETRIES}). Sleeping {wait:.1f}s...")
                time.sleep(wait)
                continue
            # Safety blocks -> return a tag
            if "safety" in msg or "blocked" in msg:
                return {"response_text": "(blocked)", "error": ""}
            # Otherwise, return error
            return {"response_text": "[ERROR]", "error": f"{type(e).__name__}: {e}"}
    return {"response_text": "ERROR: too many retries due to rate limits", "error": ""}

def write_results_csv(out_path: str, rows: List[Dict[str, str]]):
    fieldnames = [
        "timestamp_utc","provider","model",
        "row_index","name","category","norwegian_title","norwegian_url",
        "prompt_id","prompt_text","response_text","error"
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

def main():
    ap = argparse.ArgumentParser(description="Run prompt_1..3 from CSV against Gemini and log responses.")
    ap.add_argument("--input", required=True, help="Path to religious_ideology_*.csv")
    ap.add_argument("--output", default="religious_ideology_gemini_responses.csv", help="Output CSV")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model (e.g., gemini-2.5-flash)")
    ap.add_argument("--rows", default="first", choices=["first","all"], help="'first' = only row 2 (first data row), 'all' = every row")
    ap.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep after each API call")
    args = ap.parse_args()

    require_key()
    rows_in = read_csv_rows(args.input)
    if not rows_in:
        print("No data rows found.", file=sys.stderr); sys.exit(3)

    selected = [(2, rows_in[0])] if args.rows == "first" else [(i+2, r) for i, r in enumerate(rows_in)]
    print(f"Loaded {len(rows_in)} items from {args.input}")
    print(f"Provider: gemini, Model: {args.model}, Mode: {args.rows}\n")

    out_rows: List[Dict[str, str]] = []
    for (visual_idx, row) in selected:
        name = row.get("name","")
        cat = row.get("category","")
        no_title = row.get("norwegian_title","")
        no_url = row.get("norwegian_url","")
        prompts = ensure_prompts(row)

        print(f"[row {visual_idx}] {name} → {no_title} ({len(prompts)} prompts)")
        if not prompts:
            continue

        for j, p in enumerate(prompts, start=1):
            print(f"  {j}/{len(prompts)} sending … ", end="", flush=True)
            res = call_gemini_safe(p, args.model)
            preview = (res['response_text'] or "[EMPTY]")[:80].replace("\n"," ")
            if res["error"]:
                print(f"ERROR -> {res['error']}")
            else:
                print(f"ok -> {preview} ...")

            out_rows.append({
                "timestamp_utc": utc_now_iso(),
                "provider": "gemini",
                "model": args.model,
                "row_index": str(visual_idx),
                "name": name,
                "category": cat,
                "norwegian_title": no_title,
                "norwegian_url": no_url,
                "prompt_id": f"p{j}",
                "prompt_text": p,
                "response_text": res["response_text"],
                "error": res["error"]
            })
            time.sleep(args.sleep)

    write_results_csv(args.output, out_rows)
    print(f"\nSaved → {args.output}")

if __name__ == "__main__":
    main()
