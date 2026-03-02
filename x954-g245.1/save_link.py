"""Save a URL to Readwise Reader. Usage: python save_link.py <url>"""
import os, sys, requests

TOKEN = os.environ.get("READWISE_TOKEN", "")

if not TOKEN:
    print("Set READWISE_TOKEN env var first:")
    print('  export READWISE_TOKEN="your_token_here"')
    sys.exit(1)

url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ").strip()

r = requests.post(
    "https://readwise.io/api/v3/save/",
    headers={"Authorization": f"Token {TOKEN}"},
    json={"url": url},
)

if r.ok:
    print(f"Saved: {url}")
else:
    print(f"Error {r.status_code}: {r.text}")
