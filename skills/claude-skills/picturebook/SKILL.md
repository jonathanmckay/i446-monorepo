---
name: "picturebook"
description: "Generate an illustrated Chinese picture book for Theo: constrain to a vocabulary size, pick a subject and length, write the story, generate one OpenAI image per sentence, assemble it in Obsidian, and export a page-through PDF. Usage: /picturebook <vocab_count>, <subject>, <length> [low|medium|high]"
user-invocable: true
---

# Picture Book Generator (/picturebook)

Create a Chinese first-reader for Theo: a short story written to a **bounded
vocabulary**, with **one illustration per sentence**, assembled as an Obsidian
doc he can read aloud. Images come from the OpenAI Images API.

Companion to the agency/curriculum work in `xk23 学习 McKay Curriculum/`.

## Usage

```
/picturebook <vocab_count>, <subject>, <length> [quality]
```

- **vocab_count** — approximate number of distinct Chinese characters Theo knows
  (the story must stay at or under this, reusing characters heavily). e.g. `120`.
- **subject** — what the story is about. e.g. `X-Wings`, `a panda`, `going to the beach`.
- **length** — number of sentences = number of pages/pictures. e.g. `14`.
- **quality** (optional) — `low` | `medium` (default) | `high`. Per-image price
  1024×1024: low $0.011, medium $0.042, high $0.167. Tell the user the estimate
  (`length × per-image`) before generating.

Examples:
```
/picturebook 120, X-Wings, 14
/picturebook 80, a little panda who is lost, 10 low
/picturebook 200, 去海边, 16 high
```

## Prerequisite: OpenAI key

The key is stored in the **macOS login Keychain** (account `$USER`, service
`OPENAI_API_KEY`). `gen_book.py` reads it automatically — from `$OPENAI_API_KEY`
if exported, otherwise from the Keychain. No pasting needed for normal runs.

- Retrieve manually: `security find-generic-password -a "$USER" -s OPENAI_API_KEY -w`
- (Re)store / rotate: `security add-generic-password -a "$USER" -s OPENAI_API_KEY -U -w '<key>'`
- Never write the key into any file, the doc, or memory. If the Keychain item is
  missing, ask the user to paste a key (`ask_user`) and offer to store it with the
  command above.

## Steps

### 1. Parse args
Split on the last two commas: `vocab_count, subject, length`. Peel an optional
trailing quality word (`low|medium|high`). Validate `vocab_count` and `length`
are integers; if not, ask the user to reformat.

### 2. Decide the cast and lock a style
- Default protagonist is **波波 (Bobo)**. **Bobo is Lemmee, the ring-tailed lemur
  from the *YooHoo & Friends* series** (he is Theo's plush toy; note 琪琪's 波波 is
  the YooHoo character). Draw him as a cute cartoon ring-tailed lemur, **not** a
  human boy. Reuse this exact look so the whole series stays coherent:
  *silver-grey fur; white face, belly and round ears; very large bright green
  eyes; small dark nose; little dark-grey hands; a long fluffy tail with
  black-and-white rings; cheerful happy expression.*
  Use a different lead only if the subject clearly calls for one.
- Write ONE **style-lock string** you will prepend to every page's prompt, e.g.:
  > "Children's picture-book illustration, soft flat shapes, warm gentle colors,
  > thick clean outlines, simple friendly storybook style. Recurring character
  > <NAME>: <fixed physical description>. <Any recurring object, fixed description>.
  > Square composition, simple uncluttered background. No text, no words, no
  > letters anywhere in the image."
- The character/object description must be **identical, verbatim**, in every page
  prompt — this is what keeps the cast consistent across separate generations.

### 3. Write the story (vocabulary-bounded)
- Write exactly **`length`** sentences in **simplified Chinese**.
- Stay within ~`vocab_count` **distinct** characters. Favor the highest-frequency
  characters; **reuse** words across sentences rather than introducing new ones.
- **Story-specific words (the glossary rule):** you may introduce up to **one
  out-of-vocabulary word per 20–50 characters** of story text — names, animals,
  or props the story genuinely needs (e.g. 狐猴, 飞船, 太空). Don't exceed that
  density. Collect every such word into a **生词 glossary** so JM can pre-teach
  them with Theo before reading.
- Keep sentences short (3–8 characters typical for low counts). Simple narrative
  arc: setup → adventure → friend/turn → resolution → warm ending.
- If a canonical list of Theo's known characters exists (check
  `xk23 学习 McKay Curriculum/中文*.md` for a character spine), prefer it over raw
  frequency. Otherwise approximate with common-character frequency.
- After drafting, **list the unique characters used and the count**, and confirm
  it is within `vocab_count` (excluding the glossary words, which are tracked
  separately).

### 4. Create the book folder + markdown
- Slug = ASCII-safe short name (e.g. `bobo-xwing`); title may be Chinese.
- Folder: `~/vault/xk87/xk23 学习 McKay Curriculum/readers/<slug>/`
- Write `<slug>.md`:
  ```
  # <中文标题>

  *A first reader for Theo — ~<vocab_count>-character vocabulary. Read aloud, one picture per sentence.*

  ## 生词 — read these together first

  | 词 | 拼音 | English |
  |---|---|---|
  | <word> | <pinyin> | <gloss> |

  ---

  <sentence 1>
  ![[<slug>-01.png]]

  <sentence 2>
  ![[<slug>-02.png]]
  ...
  ```
  Embeds are zero-padded (`-01`, `-02`, …) so they sort correctly. The 生词 table
  lists exactly the out-of-vocabulary story words from step 3.

### 5. Build scenes.json
For each sentence write a concrete **visual scene** (what's happening, not the
text). Compose the full prompt = `<style-lock> + " Scene: " + <scene>`. Write a
temp JSON mapping `<slug>-NN.png -> full_prompt`:
```
/tmp/picturebook-scenes.json   # {"<slug>-01.png": "...full prompt...", ...}
```

### 6. Generate the images
```bash
export OPENAI_API_KEY='...'   # from env or pasted; never persisted
python3 ~/.copilot/skills/picturebook/gen_book.py \
    "<book folder>" /tmp/picturebook-scenes.json <quality>
unset OPENAI_API_KEY
rm -f /tmp/picturebook-scenes.json
```
The script prints `OK/ERR` per page and a final `N/M generated` line. Re-run any
failed pages individually if needed.

### 7. Verify + open
- Confirm `length` valid PNGs exist (`file` each).
- `open "<book folder>/<slug>.md"` so it renders in Obsidian.

### 8. Export a page-through PDF
Build a `book.json` manifest and render it (title/glossary page + one page per
sentence), then open the PDF:
```bash
# book.json: {"title","subtitle","glossary":[["词","pinyin","gloss"],...],
#             "pages":[{"image":"<abs>/slug-01.png","caption":"<sentence 1>"},...]}
python3 ~/.copilot/skills/picturebook/make_pdf.py \
    "<book folder>/<slug>.pdf" /tmp/picturebook-book.json
open "<book folder>/<slug>.pdf"
```
`make_pdf.py` is pure Pillow and uses a macOS CJK font, so the Chinese renders.

### 9. Report
Report: title, page count, glossary words, unique-character count vs `vocab_count`,
quality, paths to the `.md` and `.pdf`, and total cost (`length × per-image price`).

## Notes / conventions
- **Cost reporting:** tokens/images × sticker price, not wall-clock. State the
  dollar estimate up front and the actual at the end.
- **No text in illustrations:** the Chinese lives in the markdown; always include
  "no text, no letters" in the style-lock to avoid garbled characters in the art.
- **Consistency caveat:** gpt-image-1 keeps characters roughly (not pixel-)
  consistent across calls. The verbatim character description is the main lever;
  `high` quality helps a little. Offer to regenerate any off-model page.
- **Series reuse:** for sequel Bobo books, reuse the exact Bobo (Lemmee the
  ring-tailed lemur) description so the whole shelf looks like one series.
- Existing example to match: `xk23 学习 McKay Curriculum/Theo PK3 Curriculum →08
  2025/波波和X-Wing/` (14 pages, ~150-char vocab, 5-word 生词 glossary, medium
  quality, with `波波和X-Wing.pdf`).
