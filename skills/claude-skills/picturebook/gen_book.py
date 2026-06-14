#!/usr/bin/env python3
"""Generate picture-book illustrations via the OpenAI Images API.

Usage:
    OPENAI_API_KEY=sk-... python3 gen_book.py <out_dir> <scenes.json> [quality]

<scenes.json> maps output filename -> full image prompt, e.g.
    {"bobo-01.png": "Children's picture-book illustration ... Scene: ...", ...}

quality: low | medium (default) | high   (gpt-image-1 tiers)
Per-image sticker price (1024x1024): low $0.011, medium $0.042, high $0.167.

Each prompt should already contain the LOCKED recurring-character + style text so
the cast stays consistent across pages. Reads the key from $OPENAI_API_KEY only;
never write the key to disk.
"""
import os, sys, json, base64, urllib.request, urllib.error

def main():
    if len(sys.argv) < 3:
        print("usage: gen_book.py <out_dir> <scenes.json> [low|medium|high]")
        return 2
    out_dir = os.path.expanduser(sys.argv[1])
    scenes = json.loads(open(os.path.expanduser(sys.argv[2]), encoding="utf-8").read())
    quality = sys.argv[3] if len(sys.argv) > 3 else "medium"
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        # Fall back to the macOS login Keychain (account=$USER, service=OPENAI_API_KEY).
        try:
            import subprocess
            key = subprocess.check_output(
                ["security", "find-generic-password", "-a", os.environ.get("USER", ""),
                 "-s", "OPENAI_API_KEY", "-w"], stderr=subprocess.DEVNULL).decode().strip()
        except Exception:
            key = None
    if not key:
        print("ERR: OPENAI_API_KEY not set (env or Keychain item 'OPENAI_API_KEY')")
        return 1
    os.makedirs(out_dir, exist_ok=True)

    def gen(prompt):
        body = {"model": "gpt-image-1", "prompt": prompt,
                "size": "1024x1024", "quality": quality, "n": 1}
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())

    ok = 0
    for fname, prompt in scenes.items():
        path = os.path.join(out_dir, fname)
        try:
            d = gen(prompt)["data"][0]
            raw = (base64.b64decode(d["b64_json"]) if d.get("b64_json")
                   else urllib.request.urlopen(d["url"], timeout=120).read())
            with open(path, "wb") as f:
                f.write(raw)
            ok += 1
            print(f"OK  {fname}  {len(raw)//1024} KB")
        except urllib.error.HTTPError as e:
            print(f"ERR {fname}  HTTP {e.code}: {e.read().decode()[:200]}")
        except Exception as e:
            print(f"ERR {fname}  {type(e).__name__}: {e}")
    print(f"--- {ok}/{len(scenes)} generated ({quality}) ---")
    return 0 if ok == len(scenes) else 1

if __name__ == "__main__":
    sys.exit(main())
