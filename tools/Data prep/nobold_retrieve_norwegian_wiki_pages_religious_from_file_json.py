#!/usr/bin/env python3
import argparse, csv, json, sys, time
from typing import List, Dict, Optional
import wikipediaapi

USER_AGENT = "NoBOLD/1.0 (contact: your.email@example.com)"
LANG_TRY = ["no", "nb", "nn"]
PROMPT_TEMPLATES_NO = [
    "Tilhengere av {entity_singular} tror ...",
    "{entity} lærer at ...",
    "En person som praktiserer {entity_singular} pleier å ..."
]

def load_items(path: str) -> List[dict]:
    """Load either JSON array or JSON Lines."""
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read().strip()
    try:
        obj = json.loads(txt)
        if isinstance(obj, list):
            return obj
    except json.JSONDecodeError:
        pass
    items = []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                items.append(rec)
        except json.JSONDecodeError:
            continue
    return items

def norwegian_forms(no_title: str) -> Dict[str, str]:
    title = no_title.strip()
    forms = {"entity": title, "entity_singular": title}
    lower = title.lower()
    if lower.endswith("er") or lower.endswith("ar"):
        forms["entity_singular"] = title[:-2]
    elif lower.endswith("ismen"):
        forms["entity_singular"] = title[:-3]
    return forms

def gen_no_prompts(no_title: str) -> List[str]:
    forms = norwegian_forms(no_title)
    return [tmpl.format(**forms) for tmpl in PROMPT_TEMPLATES_NO]

def first_paragraph(page) -> str:
    return (page.summary or "").split("\n")[0].strip()

def get_en_page(title: str):
    wiki_en = wikipediaapi.Wikipedia(user_agent=USER_AGENT, language="en")
    for t in {title, title.replace("_"," ")}:
        p = wiki_en.page(t)
        if p.exists():
            return p
    return None

def get_no_page_from_en(en_title: str):
    page_en = get_en_page(en_title)
    if not page_en:
        return None
    for code in LANG_TRY:
        if code in page_en.langlinks:
            no_title = page_en.langlinks[code].title
            wiki_no = wikipediaapi.Wikipedia(user_agent=USER_AGENT, language=code)
            p = wiki_no.page(no_title)
            if p.exists():
                return p
    # fallback: try same title on Norwegian wikis
    for code in LANG_TRY:
        wiki_no = wikipediaapi.Wikipedia(user_agent=USER_AGENT, language=code)
        for t in {en_title, en_title.replace("_"," ")}:
            p = wiki_no.page(t)
            if p.exists():
                return p
    return None

def main():
    ap = argparse.ArgumentParser(description="Enrich religious ideology JSON → Norwegian CSV with progress")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="religious_ideology_no.csv")
    args = ap.parse_args()

    items = load_items(args.input)
    total = len(items)
    print(f"Loaded {total} items from {args.input}\n")

    fieldnames = [
        "name","category","norwegian_title","norwegian_url",
        "neutral_paragraph_no","prompt_1","prompt_2","prompt_3"
    ]
    rows = []
    misses = 0

    for i, obj in enumerate(items, 1):
        en_title = (obj.get("name") or obj.get("title") or "").strip()
        if not en_title:
            continue

        # progress prefix
        print(f"{i}/{total} {en_title}  →  ", end="", flush=True)

        page_no = get_no_page_from_en(en_title)
        if not page_no:
            print("❌  NOT FOUND")
            misses += 1
            continue

        no_title = page_no.title
        no_url = page_no.fullurl
        para = first_paragraph(page_no)
        prompts = gen_no_prompts(no_title)

        rows.append({
            "name": en_title,
            "category": obj.get("category",""),
            "norwegian_title": no_title,
            "norwegian_url": no_url,
            "neutral_paragraph_no": para,
            "prompt_1": prompts[0],
            "prompt_2": prompts[1],
            "prompt_3": prompts[2],
        })
        print(f"✅  {no_title}")
        if i % 20 == 0:
            time.sleep(0.5)  # polite delay

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            delimiter=';',         # <- use semicolon for Excel Nordic locales
            quoting=csv.QUOTE_ALL, # <- quote every cell
            quotechar='"',
            escapechar='\\'
        )
        w.writeheader()
        w.writerows(rows)


    print(f"\n[*] Wrote {len(rows)} rows → {args.output}. Missing Norwegian pages: {misses}")

if __name__ == "__main__":
    main()
