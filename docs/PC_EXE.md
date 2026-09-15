# The PC executable, and what is known about briefing addressing

Findings from the Steamless-unpacked `METAL GEAR SOLID PEACE WALKER.exe`
(x64, image base `0x140000000`, `.text` 9.5 MB, **40 715 functions** from
`.pdata`). Every address below is a virtual address in that image.

## Located

| what | where | how it was found |
|---|---|---|
| `strcode` — the 24-bit name hash | `0x14010F5A0` | strips a leading slash, stops at `.`; matches the hash that turns `BRIEFING` into `0x76531D` |
| the stream cipher | `0x14010F610` | the `0xB9D3018F` constant, 9 occurrences, all in this module |
| `BRIEFING.DAT` opener | `0x1400A56C0` | the only code reference to the literal string at `0x140990B68` |
| its two callers | `0x1408042A0`, `0x140804370` | both pass a field at `+0x90` of a briefing object |
| the briefing state block | `0x141180080` | flag, path buffer, page count, buffer pointer |
| a page-based container reader | `0x1400A63E0` (chunks to `0x1400A668D`) | four `crypt` calls; 20-bit start/end page fields, `pages = size >> 12` |

**The cipher signature is `crypt(buffer, length, name_hash)`** — one continuous
keystream per call, no internal reset. So the per-4 KB-page decryption of
`BRIEFING.DAT` is not a property of the cipher; it is a property of **how the
engine reads that file: one page per call**.

## Ruled out

* **No record scan.** The magic `oEbN` (`0x4E62456F`) appears nowhere in the
  exe, in either byte order. The engine never searches for a record — it goes
  straight to a position.
* **No position table in the exe.** Not as raw offsets, not in 16-byte units,
  not as the PS3 packed word. No run of even eight consecutive real offsets
  survives in any encoding. So positions are computed at runtime, exactly as
  the PS3 investigation concluded.

## Not found

**The record-index-to-file-position computation.** This is the piece a hook
would have to target, and it is still open. The briefing class occupies roughly
`0x140803130`–`0x140806125`; most of its methods have no direct callers, so
they are virtual and reached through a vtable that has not been located.

## The cheaper question

Before hunting the hook further it is worth knowing whether the PC engine minds
at all. `pwtr.briefing.relayout()` builds a `BRIEFING.DAT` with every record at
the size its translation needs — 358 records grow, 2 611 of 2 624 move, the
first at `0x004590`. Install it over a backup and play a briefing:

* **all of them play in Arabic** → the engine walks the file, and the byte cap
  was never real on PC;
* **early ones play, later ones break** → positions are precomputed, and the
  point where it starts breaking says how they are derived.

Either answer is worth more than more static reading.

### First attempt: crashed, and it was my fault

The first grown build crashed the game on playing a briefing, which looked like
a clean answer and was not one. Two bugs in the rebuild, either of them enough
on its own:

* **`relayout` emitted only the records `parse` recognised.** 2 624 of the
  2 761 magics validate; the rest are deliberately skipped, and skipping them
  on the way *out* dropped 259 KB of live data on the floor. The tell was a
  shift-only build coming out 217 KB **smaller** than stock while padding was
  being added to it.
* **`fon_off` was written unchanged.** It points from the record start to the
  voice-cue block, so growing the text moves the block out from under it and
  the engine reads cue data from the middle of a sentence. It is now bumped by
  the body delta, and the body is no longer padded, so that delta is the only
  correction needed.

Both fixed. The rebuild now recovers 2 624 of 2 624 records, `fon_off` still
points at its own block on all 540 records that have one, and the file grows by
227 KB rather than shrinking. Retesting is what tells us about the engine —
the first crash told us about the builder.


---

## Tested in game: records may move, but a record may not outgrow its pages

**The grown build runs.** Briefings display Arabic, which settles the first
question outright: the PC engine does not refuse a moved record the way the PS3
one does, and the in-place byte cap was never the real constraint.

What it does refuse is a record that needs more pages than it had. The first
briefing showed Arabic through nine lines, then the text stopped while the
audio played on to the end, and the briefing never closed.

Measured on that record — `0x0944B0`, whose start did **not** move:

| | stock | built |
|---|---|---|
| span | 2 128 | 3 264 |
| pages occupied | 148 only | 148 **and 149** |
| record ends | `0x094D00`, inside its page | `0x095170`, past the page end |
| text ends | `0x094A8B`, inside | `0x094EED`, still inside |
| cue block | `0x094A84`, 629 bytes, all present | `0x094EE6`, **282 of 643 bytes present** |

The text all fits; the **voice-cue block is cut off by the page boundary**. The
cues drive which line is shown when, so the ones that survived advanced the
text nine lines and then ran out — the audio is a separate stream and kept
playing, and with no end-of-briefing cue the screen never closed. Every symptom
follows from those 282 bytes.

So the engine reads **the pages a record occupied in the stock file**, and that
footprint is precomputed. It is the same staleness the PS3 investigation found,
in a different unit: pages rather than a packed offset word.

### What that rules out

Growing inside a page is not available either. The record could in principle
run to `0x095000` — 2 896 bytes against its stock 2 128, a useful 768 bytes of
headroom — but the **next record starts at `0x094D00`, inside the same page**.
Records tile densely, so taking that headroom means moving the neighbour, and
the neighbour's own footprint then goes stale.

No record can grow without moving another, and no record can move without its
page footprint going stale. The hook is unavoidable after all — but it now has
a specific target: the per-record **(start page, page count)** the engine reads,
which is the shape the container reader at `0x1400A63E0` already works in,
masking two 20-bit page fields out of a record structure.


---

## Where the footprint is built — and where it is not

Traced the paged-stream subsystem end to end:

| | |
|---|---|
| `0x1400A5E30` | builds the packed word: `(count << 20) \| (start_page & 0xFFFFF)` |
| `0x14008AFB0` | binary search that supplies the count — **20-byte entries, sorted by a 20-bit start page, count at `+0xC`** |
| `0x1410CBA10` / `0x1410CBA28` | the table's base pointer and entry count |
| `0x14008B480`, `0x14008B6D0` | fill it, from a file the code calls **`slotkey`** |
| `0x1400A63E0` | the reader that consumes it, masking start/end pages out of the entry |

The 20-byte entry is exactly the SLOT `.KEY` record: dword0 is
`(tag << 20) | start_page`, dword1 is `(content_pages << 20) | end_page`.

**But this is SLOT's subsystem, not the briefing's.** Walking 59 functions
outward from the briefing entry points (`0x1400A56C0`, `0x1400A5630`,
`0x1408042A0`, `0x140804370`) never reaches any of it. And only 31 of the
briefing's 931 distinct start pages coincide with a `SLOT.KEY` entry, which is
what chance gives you. `BRIEFING.DAT` has no `.KEY` of its own.

So the briefing's page footprint is **not** KEY-backed, and the mechanism
behind the observed page truncation is still unidentified. The correlation
itself is solid — the record that grew from one page to two lost exactly the
part of its cue block that fell past the boundary — but what enforces it is not
this table.

### Testing the correlation instead

`_budget`-style layout: lay every record out as before, but never let one
occupy more pages than it did in stock, page-aligning a record rather than
letting a move push it over. Records whose Arabic still will not fit stay
English.

The first attempt of that got the order wrong: it judged the fit at the
record's natural position, dropped the translation, and *then* page-aligned the
English version anyway — so the record under test came out in English and the
test proved nothing except that a relaid-out file plays. Trying the aligned
position **before** giving up is the whole difference:

| | records translated | left English |
|---|---|---|
| in place, no growth | 26 | 357 |
| page budget, judged unaligned | 234 | 149 |
| **page budget, aligned first** | **379** | **4** |

A record needing two pages from halfway through one needs only one from the
top, so a page fragment of padding buys a whole page of room. 161 records are
aligned that way; only 4 cannot be made to fit at all.


---

## The page budget is not the constraint either

The page-budget build hangs too, with the record page-aligned, one page wide,
and its voice-cue block ending seven bytes inside the page window. So the page
correlation was a coincidence and is dropped.

What the four runs actually separate:

| build | the record under test | result |
|---|---|---|
| stock | English, stock size, stock place | plays |
| page budget v1 | English, **moved** | **plays** |
| relayout | Arabic, **grown** | hangs |
| page budget v2 | Arabic, **grown**, page-aligned, cue inside | hangs |

**Moving a record is fine. Growing one is not** — independently of pages. That
is the only variable that tracks the outcome across all four.

### The isolating test

In-place partial fill cannot test this on the record in question: its body is
100% full, so no English line can be swapped for a longer Arabic one and it
comes out entirely English. So the test inverts it — a **deliberately short**
Arabic line, shorter than the English it replaces:

* one record, one line;
* the record keeps its exact byte length, so nothing after it moves;
* the file is the same length as stock and differs from it in **1 333 bytes,
  all inside that one record**.

Then: **plays and closes** → growth is the whole constraint, and the ceiling is
each record's own byte budget. **Hangs** → the engine objects to the Arabic
itself, which is a different problem and would explain the earlier runs without
any reference to size.


---

## Growth is the constraint — content is not

The isolating build **plays and closes**. One record, one line, a deliberately
short Arabic string replacing a longer English one, the record's byte length
unchanged so nothing after it moved. `نعم يا زعيم` displayed, the audio ran to
the end, the briefing closed.

So:

* **Arabic content is fine.** The engine draws it, and the earlier failures had
  nothing to do with the text being Arabic.
* **Moving a record is fine.** Established twice over.
* **Growing a record is the whole problem.**

### Where the size is not

Not in the exe. Searched for a table of per-record spans, body sizes and `u5`
values as u32 LE, u16 LE and u32 BE, at several starting indices — nothing. (A
"run of 1s" matches for page counts, which is every buffer of zeros with a one
in it, not a table.) Not in a `.KEY` either: that subsystem is SLOT's and the
briefing path never reaches it.

### What fits every run so far

A record that keeps its **body at exactly its stock size** keeps its cue block
at the same offset within the record, and plays. A record that grows moves its
cue block and updates `fon_off` to match, and hangs. Both are explained if the
engine **does not read `fon_off`** and instead expects the cue block where it
has always been.

That is testable without any more reverse engineering: take the file that works
and make `fon_off` wrong, changing nothing else. Four bytes.

* **still plays** → `fon_off` is ignored, the cue block's position is the
  constraint, and no record can grow by a byte;
* **hangs** → `fon_off` is read and honoured, and the grown builds failed for a
  reason still unaccounted for.

**It hangs.** So `fon_off` is read and honoured — and the grown builds set it
correctly, verified against the cue block on all 540 records that carry a FON
magic. Both of my models are now dead: the page budget, and the decorative
pointer.

## Where this leaves it

Established, each by a play-through rather than by argument:

| | |
|---|---|
| Arabic content | **fine** — a short Arabic line in a stock-sized record plays and closes |
| moving a record | **fine** — a fully relaid-out English file plays with no issue |
| growing a record | **breaks it** — every grown build hangs, page-aligned or not |
| `fon_off` | **read and honoured** — four wrong bytes hang it |
| a per-record size table | **not in the exe**, in any encoding, and not in a `.KEY` |

The remaining shape is that the engine loads a bounded region for each record
and `fon_off` must point inside it. That would explain a grown record hanging
with a correct `fon_off`: the pointer is right, the bytes it points at were
never loaded. It does not yet explain the page-aligned build, whose record and
cue block both sat inside a single page.

**The next move is not another build.** `fon_off` being read is a signature to
search on: code that loads a dword at `+0x0C` from a briefing record and uses it
as an offset. That is a far narrower target than "the record-to-offset
computation", and it is where the bound will be too.

## What ships today

In-place with partial fill, which respects every constraint above:

* file length identical to stock;
* **0 records moved, 0 cue blocks moved, 0 `fon_off` values changed**;
* 51 Arabic lines across 32 records.

Modest against 4 987, but it is the whole of what the container allows without
a code patch, and it is built on the one configuration that has been shown to
play.


---

## Where the static hunt stalls

With `fon_off` known to be read, the next step was to find the code reading it.
It did not come out:

* **No header validation.** `cmp dword [reg+0x10], 0x14` — the record's constant
  field — appears nowhere in `.text`. The engine does not check the header the
  way a parser would, so there is no cheap signature on it.
* **No vtable for the briefing entry points.** Nothing in `.rdata` holds a run
  of pointers into `0x140802000..0x140808000`. A 14-entry dispatch table does
  exist in `.data` at `0x141013700` — it contains `0x140804370`, the function
  that opens `BRIEFING.DAT` — but its methods are small and none of them reads
  a record.
* **No `+0x0C` read that is a `fon_off` read.** Seven functions in the briefing
  range touch `+0x0C`; every one is a float or a flag word. So the engine
  reaches the field through some other anchor — from `text_base`, or from a
  pointer already advanced — and the displacement is not one I can guess.

The remaining approach is the one the PS3 work used, and its notes say so
outright: **a debugger**. Break on reads of the briefing buffer, play a
briefing, and read the bound out of the registers. Fifteen days of static
searching there found nothing; one runtime session found the packed word.

Everything needed to set that up is in this document — the buffer pointer at
`0x141180098`, the page count at `0x141180094`, the opener at `0x1400A56C0`,
and the decrypt at `0x14010F610` that fills the buffer and is the natural place
to break.

## The state of the briefings

| | |
|---|---|
| container | fully decoded, byte-exact round trip |
| text | 4 987 English lines extracted, 4 974 translated |
| write-back | works — proven in game |
| constraint | **a record may not grow**; moving is fine, Arabic is fine |
| ceiling without a code patch | 26 records whole, or 51 lines by partial fill |

Not worth shipping at that ceiling. The container is solved; what is not solved
is the size bound, and that wants a debugger rather than more reading.


---

## A fresh reading of the container, PS3 assumptions dropped

Re-derived from the PC bytes alone, believing nothing from the PS3 write-up.

**The header, measured across all 2 761 records:**

| field | what the data says |
|---|---|
| `+0x00` | constant `4E62456F` |
| `+0x04`, `+0x08` | constant `FFFFFFFF` — both, every record |
| `+0x0C` | 674 distinct values |
| `+0x10` | `0x14` in 2 750 records; something else in **11** |
| `+0x14` | only **79 distinct values** across the whole file |
| `+0x18` | mirrors `+0x0C` minus 4 |
| `+0x1C` | 0 — the first string offset |

**No field encodes the record's length.** Checked directly: not one header word
equals the distance to the next record, in any of the 2 761. So the engine
cannot be getting a record's extent from the record.

**Eleven records break per-page decryption**, and they are not scattered: every
one sits at an offset ending `0xFF0` — sixteen bytes before a page boundary,
with its header split across it. Their first sixteen bytes decrypt correctly
under the page's keystream; the continuation does not. A keystream started one
page earlier decodes such a record whole (`fon_off 0x18C`, `u5 0x28` for the one
at `0x1CFF0`), so the file is **not uniformly page-encrypted** — but decrypting
in extended chunks makes things worse overall (2 733 valid records against
2 750), so the rule is not "extend while a record straddles" either. Unresolved,
and it affects 11 records of 2 761.

**It is not the cause of the hangs.** Counting header-straddling cases per
build settles that:

| build | straddling headers | result |
|---|---|---|
| stock | 11 | plays |
| in-place partial | 11 | plays |
| relayout, grown | 18 | hangs |
| page budget | **9** | hangs |

The build with *fewer* than stock still hangs, so this is not the mechanism.

## The one measurement that would constrain everything

Every build that worked grew nothing. Every build that failed grew records by
hundreds of bytes. The middle is untested, and it decides the whole question:

* **+16 bytes is fatal** → no growth at all is tolerated, and only a code patch
  changes that;
* **+16 bytes is fine** → there is a bound, and a packer that respects it ships
  a real translation without touching the executable.

So: one record padded by 16 bytes, `fon_off` moved to match, no text altered,
everything after it shifted 16 along. The file grows by exactly 16 bytes.


---

## Correction: moving a record is fatal, and growth was never the cause

The +16 build plays its first briefing and hangs on the tapes after it. That
record did not move — the padding went inside it — but **every record after it
shifted 16 bytes**, and those are the ones that hang.

So the earlier conclusion was wrong, and wrong because it rested on a single
briefing: the "moving is fine" build was only ever tested on one tape, whose
record happened not to have moved. Restated:

| | |
|---|---|
| a record's **offset** | must not change — anything after a shift hangs |
| a record's **size** | only matters because changing it moves what follows |
| Arabic content | irrelevant, as established |

Sixteen bytes is enough. There is no bound to find and no packer that fits
inside one.

## The engine cannot be walking the file

Worth establishing, because a walk would have been the way out:

* **No length in the header.** No header word equals the distance to the next
  record, in any of the 2 761.
* **No length in the block at `fon_off`.** `span - fon_off` takes 326 distinct
  values between 76 and 3 292; testing every field position in the first 40
  bytes there against the tail length, the span, and both divided by 16 gives
  nothing above 1% — noise.

A record's extent is not recoverable from the file, so the engine cannot find
record N+1 from record N. The offsets come from outside.

## Where they must come from — the EXLANG lead

`EXLANG` is a parallel set, and the executable names it: `.rdata` carries
`./JPN/Text/...`, `./MLG/Text/...` and `./EXLANG/Text/...` paths, so there are
three language sets, not two.

And `EXLANG/disc0_rel/0076531d.DAT` is **a different layout of the same
container** — 4 166 848 bytes against 4 142 432, 2 620 records against 2 624,
and only the first 42 share an offset with MLG's. Two shipped copies whose
records sit in different places.

That rules out one hardcoded offset list and points at a **per-set** one: MLG's
offsets somewhere on the MLG side, EXLANG's on the EXLANG side. Not in the exe,
and not in any file under 8 MB — 285 of them searched, raw and decrypted, for
runs of consecutive offsets in four encodings. So the next place to look is
inside the big containers, which is exactly what a parallel-set structure would
predict.


## Where the offsets are not

Searched exhaustively for a table of the 2 624 MLG record offsets, in four
encodings (`u32` LE and BE, `u16` LE, and each divided by 16), both as adjacent
runs and as a **strided** table — the latter because an array of structs would
never show up as a run, which the first sweep would have missed:

| searched | result |
|---|---|
| the executable | nothing |
| all 285 files under 8 MB, raw and decrypted | nothing |
| `SLOT.DAT`, all 2 137 blocks | nothing |
| `STAGEDAT.PDT`, all 557 entities | nothing |
| the save data, all 8 files | nothing |

The saves are worth a note: they are encrypted with something other than the
name-keyed cipher, and none carries a briefing offset. So the "the index is
cached in the save" theory is dead too.

## Facts worth keeping from the game itself

The game writes a log — `logs/MGSPatriotFix_Game.log`, from the MGSPatriotFix
ASI loader — which gives two things reverse engineering had not:

* the launch arguments: `-region eu -lan en -selfregion EU`, so the language
  set is chosen **on the command line**;
* the save path: `mgspw_savedata_win`, which is inside the game folder rather
  than in Documents or Steam userdata.

`-lan en` and the MLG edits appearing in game together say English resolves to
**MLG**, and EXLANG is a different set rather than an overlay on the one being
read.


---

## The way through: stop making the text bigger

Every failure has been growth, and growth was never really about Arabic being
verbose — it is about **where the glyphs were put**. Forms-B lives at
U+FE70..U+FEFC, which costs **three bytes per character** in UTF-8 against
English's one. That is the whole 1.86x overrun.

But the code points are ours to choose. The XPR character map is indexed by
code point and this toolkit decides which code point carries which glyph. Put
them low and the same sentence costs less:

| bytes per Arabic glyph | records that fit | total against budget |
|---|---|---|
| 3 (Forms-B, as now) | 25 of 383 | 1.86x |
| 2 | 30 of 383 | 1.30x |
| **1** | **383 of 383** | **0.75x** |

### Which low code points are actually free

A code point under 0x80 is one byte. Counting what the game's own English text
uses across all four containers: **91 of them are taken, 36 are free** —
`01`–`08`, `0B`, `0C`, `0E`–`1F`, and `5C 5E 60 7C 7D 7E 7F`.

The briefings use **124 distinct forms**, and the commonest 36 cover **75.5%**
of every Arabic character in the script. So the practical encoding is a hybrid:
the 35 commonest forms at one byte, the remaining 89 at two.

| | |
|---|---|
| records that fit | **368 of 383** |
| records over | 15, by 35, 23, 15, 5 and 5 bytes |
| total | 232 492 of 261 487 bytes — **0.89x** |

The fifteen are over by amounts a slightly shorter sentence absorbs, or partial
fill handles.

**And all of it is in place.** No record grows, no record moves, no `fon_off`
changes — the exact configuration that has already been shown to play a
briefing through and close it. Nothing about the engine has to be understood or
patched; the offset table can stay unfound.

### What still needs proving

The single-byte slots are mostly control codes, `0x01`–`0x1F`. Whether the
engine's text path passes those through to the character map, or treats one as
a terminator, is not established — and only seven of the free slots (`5C 5E 60
7C 7D 7E 7F`) are ordinary printable characters. If the control range is
rejected, seven slots is not enough on its own and the sums come back to about
1.0x, which is too tight to ship.

So the next test is one record encoded that way, in place.

### Built

All of it, not one record — the same test either way, and if it works the
translation is done.

| | |
|---|---|
| glyphs planned | 125 — **36 at one byte**, 89 at two |
| fonts | 133 of 133 painted into both faces, sizes unchanged |
| briefing records written | **379 of 383** |
| file length | **identical to stock** |
| records moved | **0** |
| `fon_off` changed | **0** |
| cue blocks moved | **0** |

The record that has been the test case all along now carries **all 23 of its
lines in Arabic** in 1 167 of its 1 379 bytes — 212 to spare. Its second line
is 42 bytes of Arabic where the English was 66.

The safest slots went to the commonest letters, so a failure should be legible
rather than total: if the control range is rejected, the seven forms on
printable code points still draw and the rest go missing.

One bug worth recording, because it cost the first build: the greedy in-place
fill judged each line against the record's total **before** the later lines had
shrunk it, so an early line was refused and never reconsidered — leaving the
first two lines in English while the record ended with hundreds of bytes spare.
With a compact encoding most replacements are *shorter* than what they replace,
so the room appears as the pass goes on. It iterates to a fixed point now.


## The control range does not draw

The first compact build spent its scarce single-byte slots on 0x01..0x1F, on
the reasoning that the census showed them unused and the worst case was a
legible partial failure.  It was exactly that.  The tape played to the end
with no hang -- the in-place approach was vindicated -- but eighteen of the
forty-nine characters in the opening line were simply absent, the sentence
closing up around the gaps.  Cross-checking the screenshot against the mapping
named the missing set precisely: every glyph on a control code point, and only
those.  The seven on printable slots and all eighty-nine on two-byte code
points drew correctly.

The census had been misleading.  Counted over the raw container rather than
over the extracted strings, each of 0x01..0x1F appears seven to eight thousand
times, near-uniformly -- the signature of binary data, not text.  Whatever the
engine does with those bytes, it does not look them up in the character map.

## Reclaiming code points

That leaves nine free single-byte slots, which carry 35% of the Arabic
characters and place 3% of the translated lines.  Not a solution.  UTF-8 fixes
the ceiling at 128 one-byte code points and the game's English already uses
ninety-one of them, so the remainder has to be taken back rather than found.

The insight is that a code point only has to be free in the English that
*survives* translation.  Counting characters across the 2 196 lines with no
target and taking the rarest first, thirty-five code points -- ``+ # [ ] % &
; Q / *`` and their like -- cost 3.23% of that text's characters.  Against
that, the pool of forty-two single-byte slots covers 82% of Arabic characters
and places 4 812 of 4 975 translated lines, 96.7%, with the file the same
length, no record moved and no ``fon_off`` touched.

Where one of the thirty-five does survive it now draws an Arabic letter
instead of itself.  That is the entire cost.


## The lookup ceiling, and two glyphs that set the scale

The reclaimed build rendered, and left two faults.

**Boxes where the punctuation should be.**  Every comma and question mark came
out as tofu, and the face was not at fault: the charmap entry was sound, the
rectangle right, the atlas ink there, and an offline layout straight from the
installed face drew both correctly.  What separates them from everything that
did draw is where they live.  The letters had all been moved onto reclaimed
ASCII and Latin Extended; the punctuation was the only thing still sitting at
its own code point above U+0600.  U+00AB draws, U+0100..U+024F draws, U+060C
and U+061F do not -- so the engine's lookup gives out somewhere between, well
short of the U+FF5E the header advertises.  ``compact.CEILING`` records it,
and the plan now relocates everything above it rather than only the
presentation forms.

**Every letter a fifth too small.**  The shared scale is measured over the
whole set, and the set carried an em dash and the Hangul filler used as a wide
space.  In a Japanese face both come back full-width -- 70 px of ascent
against Arabic's 51 -- so they, and not the script, were setting the scale:
0.685 where the letters wanted 0.868, with the baseline pushed from row 49 to
53 and the deepest descender left one row off the floor of its cell.  That is
what made the final meem look lifted off the line.  The measurement now runs
over the script only; the punctuation takes whatever size that gives it.  A
per-form ``nudge`` is there for anything the font's own metric still puts
where the eye does not want it.


## Which code points can actually be spared

The first reclaim took the printable code points the surviving English used
least, thirteen of them capitals.  That is the cheapest set on paper and the
wrong one twice over.

Wrong because a capital is not interchangeable with a comma: it opens a
sentence and carries the names and the acronyms, so a wrong ``Q`` reads as a
mistake where a wrong ``k`` reads as a smudge.

Wrong because of something the counting missed entirely.  A translation is
not pure Arabic -- it keeps English names, ranks and acronyms, and it uses
the Latin full stop, 18 048 times.  Those characters are drawn by this same
face.  Taking ``.`` for a presentation form does not merely spoil the English
left behind; it corrupts the Arabic, every full stop in the translation
turning into a lam.  A build made that way placed 15.6% of its lines instead
of 96.7%, and the check that caught it was the round trip, not the eye.

So the rule is narrower than "rarest first": **no letter and no digit**, ever,
because the translated text draws them too.  Punctuation only -- thirty slots,
of which the Arabic itself draws twenty-two, and those are relocated along
with the presentation forms so that nothing drawn anywhere holds a literal
``.`` and the slot is unambiguous.  ``<I=...>`` and ``<C=...>`` are read by
the engine rather than drawn, so encoding steps over them and their
characters are never candidates.

It is also better than what it replaces: 4 921 of 4 975 lines placed, 98.9%,
because the punctuation now gets one-byte slots of its own -- for 3.89% of
the characters in the lines nobody has translated yet.


## Size, and the forms that sit flat

Two things the eye catches that no check does.

**The letters were smaller than they had to be.**  The cell is 67 pixels and
Arabic wants 80 of them -- alef with hamza is 60 on its own, and the meem
bowl reaches 20 below the line -- so the script is always scaled down to fit.
That much is fixed.  What was not is the 0.92 the scale was multiplied by on
top, a margin left over from a Latin installer, costing another 8.7% for
nothing.  ``fill`` names it, and 1.0 is as large as the letters go before the
deepest descender is clipped.

**Some forms sit flat where Arabic wants them under the line.**  The
jeem/hah/khah family in its joining forms has no descent at all in this face,
and the noon bowl only 10 or 11 pixels against a yeh's 16 and a meem's 19 --
shallow enough that the letter reads as lifted off the line.  ``nudge`` moves
them down four pixels, which is as far as any of them go before their entry
and exit strokes stop meeting their neighbours and the word visibly breaks.
Four was chosen by rendering 0, 2, 4, 6 and 7 from the built face and looking,
which ``tools/preview_nudge.py`` now does for any form.


## Why the line would not sit straight

Nudging forms one at a time kept not being enough, which was the clue: the
fault was not in any form, it was in how every one of them was painted.

The installer rendered each glyph at the cell height, cropped it to its ink,
resized *that* by the shared scale, and pasted it at a row worked out with
arithmetic.  Three roundings a glyph -- width, height, and the row it lands on
-- over a bitmap that had already been resampled.  Each letter therefore came
to rest on a baseline a pixel or so of its own: 51 of the 116 Arabic forms
were off the shared line by one pixel, three of them by two.  Individually
invisible, together a line of type that will not sit straight, and no per-form
nudge can correct it because every form is off by a different amount.

The cure is to stop reconstructing what the rasteriser already knows.  Set the
point size to what is actually wanted, draw each glyph once with the pen on a
fixed row, and crop the columns only -- never the rows.  All 116 forms then
land on the same baseline exactly, and as a bonus the outlines are hinted at
their real size instead of being shrunk from a larger bitmap.

    before   51 of 116 forms off by 1px, 3 off by 2px
    after     0 of 116 forms off at all

The two nudges stay.  They correct what the face itself draws -- a hah with no
descent, a shallow noon bowl -- which is a different thing from the jitter.


## The two numbers that set how the letters sit

Nothing about a letter's height is stored per letter; there is no table of
sizes to hand-edit.  Two numbers in ``tools/build_compact_briefing.py`` decide
everything, and both are meant to be turned by hand.

``FILL`` is the size.  The cell is 67 pixels tall and the script wants 80 of
them -- alef with hamza is 60 by itself, the meem bowl reaches 20 below the
line -- so the letters are scaled to ``67 * FILL / 80`` and drawn at that
point size, 56 pt at ``FILL = 1.0``.  Above 1.0 the cell starts losing ink off
its top and bottom, and the build now names every form that loses any and by
how much, so the ceiling is measured rather than guessed:

    FILL 1.00   56 pt   no Arabic form clipped
    FILL 1.03   58 pt   alef+hamza loses 2 px of its hamza
    FILL 1.06   59 pt   alef+hamza 4 px, lam-alef 1 px
    FILL 1.08   61 pt   alef+hamza 5 px, four forms affected
    FILL 1.15   65 pt   44 forms

(The em dash is clipped at every setting.  It is full-width in a Japanese
face, it is excluded from the measurement for exactly that reason, and it
appears 47 times in half a million characters.)

``NUDGE`` is the alignment, form by form, in whole pixels, positive for down.
It is for what the face itself draws wrongly -- a hah with no descent, a
shallow noon bowl -- not for jitter, which is fixed properly elsewhere.
``tools/preview_nudge.py`` renders a line at several nudges straight off the
built face, so a value can be chosen by eye without a play session:

    python tools/preview_nudge.py <face.xpr> <mapping.json> out.png \
        <forms> <line.txt> 0,2,4,6


## A briefing you could read but not find

Two reports, one cause and two more behind it.

`Playa del Alba means "beach of dawn."` played in the game and did not exist
anywhere in the editor.  Its record holds English, but `language_of` called it
Japanese -- because the vote runs over every string in the record, and a few
hundred records declare more offsets than they have text.  The surplus point
into the audio block, so a "string" read from one is binary, and binary
decoded with `errors="replace"` lands all over the CJK range.  One such entry
was enough to send an English tape to the Japanese pile.  Voting only over the
readable strings moves 43 records back: **English records 403 -> 446, and 671
more strings to translate.**

All 43 were also refused as not rebuildable, which was the second thing.  That
guard is right -- repacking a record whose table over-declares would write the
audio block back as text -- but it is broader than it needs to be.  A single
string can still be replaced *where it lies*: the span from its offset to its
terminator belongs to it alone, so a shorter replacement followed by NULs
leaves every other byte untouched.  The budget becomes per line rather than
per record, which is stricter, and 478 records become editable that were not.
Verified: of those, not one had its offset table, its budget or its FON block
altered.

The third was quieter and worse.  `Snake, Zadornov's escaped.` was translated,
the build said nothing about it, and it stayed English in the game.  The Arabic
needs 50 bytes; the record body is 50; with its terminator that is 51.  Over
by one byte -- and because the fill works line by line, the line was dropped
and counted as "untouched".  A line left behind in silence is a translation
the writer believes shipped, so every one is now named with the number of
bytes it is over.  There are 54, and the one reported needs a single character
cut.


## Pooling the readable run

Writing each line inside its own bytes is safe and mean.  A line one byte too
long is refused while the line above it has thirty to spare, and with Arabic
averaging 93% of the English it replaces, that slack is the difference between
a briefing translated and a briefing half translated.

The strings a person wrote sit in one run, and the surplus offsets that make a
record unrebuildable point past it into the audio.  So the run can be relaid on
its own terms: the same pooled budget an ordinary record gets, with only the
offset table entries for the readable strings moving, the surplus entries never
read or written, and the audio block untouched.  418 of the 478 records qualify;
in 34 an offset the record does not own begins inside the run, and those still
go line by line.

Verified over the whole file: 2 624 records before and after, no record moved,
no ``fon_off`` changed, and not one surplus entry altered in any unrebuildable
record.


## The cipher run is not one page

A briefing read fine in the game and its third line was missing from the
editor.  The line is there in the file -- and it stops mid-word, at "the
swaying palm tre", at file offset 0x0BA000.  A page boundary, exactly.

The loader decrypts one 4 KB page at a time, each with the keystream from its
start.  That is right for most of the file and wrong wherever a cipher run
carries across a boundary: everything before the boundary reads, everything
after is noise.  Decrypting the two pages the record spans **as a single
call** returns the line whole -- and the keystream page 186 needs turns out to
be block 1 of a continuous stream, not block 0.

Mapping page 186 in 128-byte windows shows the shape of it: block 1 for the
first 256 bytes, then audio, then block 0 from byte 768 on.  So the runs are
not page-aligned and not one per page; a run begun on one page finishes on the
next, and another starts mid-page.

Trying every unreadable string at its record's span alignment, and keeping
only what reads as text, recovers **8 124 strings**.  In the briefing tab that
is 5 887 entries -> 7 029, and the tape that started this now has all nine of
its lines instead of two.

It also explains the two symptoms before it.  Records looked unrebuildable
because their strings ran past a boundary into noise; English tapes were voted
Japanese because that noise decodes into the CJK range.  Both were this.

**Reading only, for now.**  A recovered string sits at a different cipher
alignment from the page it lives on, so writing one back page-wise would put
noise where the text is.  The build holds those lines in English and says how
many.  The fix is known -- XOR the written bytes with
``ks[x - page] ^ ks[x - run]``, which is an involution, so the page-wise save
then lands the right ciphertext -- and the in-place path is where it belongs,
since those records do not move their strings.


## Moving the capitals rather than taking them

Punctuation alone leaves 54 lines that will not fit, and there is nothing
cheap left: every remaining single-byte code point is a letter or a digit that
the translations themselves draw, in the names and ranks they keep in English.

The way out is not to take a letter but to **move** it.  A capital relocated
to a two-byte code point keeps its glyph, so every line this project writes
still spells MSF and KGB correctly -- verified, they come back through the
round trip intact -- and its old one-byte slot goes to an Arabic form.  What
is given up is the capitals in lines nobody has translated, which draw an
Arabic letter instead.

    punctuation only   30 slots   1.269 bytes/char   54 lines too long   4.5%
    with capitals      56 slots   1.106 bytes/char    1 line  too long   8.0%
    + digits           66 slots   1.072 bytes/char    1 line  too long   8.2%
    + lower case       92 slots   1.024 bytes/char    0 lines too long  81.8%

Capitals are the whole of the win.  Digits buy nothing on top of them and a
wrong digit is a date read wrongly, which is worse than a wrong letter; lower
case would fit everything and destroy four fifths of the English left behind.

Two capitals stay where they are: ``I`` and ``C``, because ``<I=...>`` and
``<C=...>`` are built from them and the engine reads that markup rather than
drawing it.

Every letter in the pool is given a two-byte home whether the text uses one
today or not.  Otherwise a capital absent from every current translation would
have its code point handed to an Arabic form, and the day somebody typed it
the line would quietly draw Arabic.


## Writing back across a boundary

Reading the straddling strings was half of it; 1 201 lines then sat translated
and held back because writing one page-wise would put noise where the text is.

The fix is arithmetic.  ``save`` encrypts page by page, so pre-XOR the bytes
with the difference between the two keystreams:

    mask[x] = ks[x mod PAGE] ^ ks[x - run start]

Encrypting page-wise afterwards yields ``plain ^ ks[x - run]``, which is what
the game decrypts with.  The mask is its own inverse, so the file still reads
back through ``load`` and ``mend`` unchanged.

Two things had to be true for it to work.

A re-keyed string **must not move**: the mask is computed from its address, so
a repack that slid it along would key it wrong.  Records holding one are
pinned to the per-line write.

And its length must come from the mend, not from the record.  Read page-wise a
straddling string runs past its own terminator into noise, so its apparent
length is meaningless -- sizing the write from it NUL-filled over the
neighbours, which is what left ``Good to hear.`` half overwritten in the first
attempt.  The mended extent travels with the entry.

Verified by decrypting the built file at each run's own alignment, which is
what the engine does: **16 of 16 re-keyed lines read back exactly**, 4 refused
because the Arabic is genuinely longer than the bytes it must sit in, none
wrong.  Those 4 are now reported per line with their shortfall, so "Next too
long" walks to them like any other.


## A record holding a recovered string never repacks

Seven records were quietly destroying eighteen of the lines the mend had just
recovered.  All seven were ``rebuildable``, so they took the ordinary repack
path -- which lays the region out afresh from ``record.strings``, and for a
straddling string that is the broken page-wise copy, noise past the boundary.
The recovered text was written over with its own corruption.

So the test moved in front of the ``rebuildable`` branch: a record holding a
recovered string is pinned to the per-line write whatever else is true of it.
Two independent reasons, either sufficient.  The mask that re-keys such a
string is computed from its address, so moving it keys it wrong.  And a repack
lays out only the strings it can read, so one it cannot read is one it writes
over.

The cost is pooling: those records can no longer lend one line's slack to
another, and twelve ordinary lines that used to fit now do not.  They are
reported, not lost.  Against that, 1 202 recovered strings survive intact
where 18 were being destroyed -- and a line reported too long can be shortened
by hand, while a line silently overwritten cannot be found at all.

Verified by decrypting the built file at each run's own alignment: 16 of 16
translated straddling lines exact, 1 186 left in English byte-for-byte, **none
wrong**.


## Where the cipher runs really begin

The records holding recovered strings were pinned to a per-line write, and on
a project where most recovered lines are translated that cost hundreds of
refusals.  The reason given was that the cipher run "changes inside the
record" -- read back, a string's head was right and its tail was noise.

That reason was wrong, and the way to find out was to stop inferring and
solve.  The keystream is length-independent, so for any known plaintext the
index actually used can be recovered: XOR the text against the ciphertext and
look the result up in the stream.  Every recovered string is known plaintext.

Done for a record that had been rejected, all eighteen of its strings came
back with the same answer:

    run start 0x097000 -- the record's own page start, for every one

**The rule is simply that a byte is keyed from the page start of the record it
belongs to.**  Not the page it sits on, which is why a string crossing a
boundary reads as noise past it; and not something that varies within a
record.

So the tail was noise for a different reason, and it was mine.  The check
applied the re-keying mask before comparing -- but the mask turns plaintext
into what the page-wise save must be handed, so it was comparing a cipher
against a plain and rejecting every valid repack.  A second fault sat behind
it: several table entries can share one string, and the duplicate was judged
against its own stale copy of the English instead of the text the first entry
owns.

With both fixed, 18 of 20 records pool where 0 did:

    pinned to per-line   19 lines too long   4 983 of 4 998 ordinary lines
    pooled and checked    2 lines too long   4 997 of 4 998

Verified by decrypting the built file at each run start: 19 of 19 translated
straddling lines exact, 1 183 left in English byte-for-byte, none wrong, no
record moved.


## The harakat, for nothing

The marks were stripped because the face has no way to stack them: all 643
stock glyph records carry a non-zero advance, so a mark painted as its own
glyph is drawn beside its letter rather than over it.  The record has a
bearing field that nothing in the file uses, which made it unknown rather
than impossible -- so four shadda glyphs went into a briefing tape, each with
a different advance and bearing, and the line labelled itself 1 to 4.

The first round answered a question I had not asked.  Lines 3 and 4 broke the
whole line: the bearing had been written as two's complement, and read
*unsigned* that is 65 522 pixels.  So the field can push a glyph right and
never pull it left.

The second round asked the right thing.  With a zero advance the pen does not
move, so a mark written *before* its letter lands on the same x -- and the
shaped text already puts it there, because shaping reverses into visual order.
Lines 3 and 4 came back with the shadda sitting on top of the letter.

    advance 0, mark before the letter, bearing to push it right

No precomposed glyphs, no 224 extra records, no 4.5%.  Nine marks at nine
bearings is 81 glyphs, of which 53 are used, and the bearing centres the mark
over whatever letter follows -- widths run from 4 pixels to 56, so a fixed
offset would not do.  The cost is the mark's own code point in the text: 11
lines too long against 2 without them.


## The gap the marks opened

The first harakat build put a space where each mark sat, which is exactly what
a zero advance was supposed to prevent.  The reasonable-sounding explanation --
that the engine adds the bearing to the pen as well as to the glyph -- was
wrong, and cost a round of reasoning before the code was read: the build tool
never passed ``overlay`` to the installer at all, so every mark was painted
with ``advance = width`` like an ordinary letter.  The space was the mark's own
width, and nothing in the engine was involved.

Two things came out of it.  The offset that centres a mark is now baked into
its tile -- a wider tile with the ink further along -- rather than set as a
bearing, so it cannot matter how the engine treats that field.  And the check
is on the pen rather than the ink: a padded tile legitimately overhangs the
line, so measuring the drawn extent says a mark added width when it did not.

    pen advance, with marks and without: 82 / 82, 164 / 164, 482 / 482


## A record still may not move

The two shipped briefing files have different layouts -- MLG's and EXLANG's
part company at record 42 and drift 24 384 bytes apart -- and the game plays
both, so positions looked derived rather than fixed.  With the keying rule
finally understood, the old "+16 breaks every tape" result had an innocent
explanation: moving a record changes its page start, and the old rebuild
re-encrypted page-wise, so everything downstream was mis-keyed.

Rebuilt properly -- every record's plaintext taken at its own alignment, one
record given sixteen bytes, the 2 583 after it moved, each re-encrypted from
where it now begins -- all 2 624 headers read back and all 2 624 records
survived byte for byte.

**The game hangs anyway.**  So something outside the file tracks where a
record lives, and it is regenerated per tree by Konami's own tools.  Ruled out
in the process: there is no header in the briefing (the first record is at
offset 0), and `002aba34.KEY` does not index it -- 2 137 entries, none hashed
`BRIEFING`, and only incidental page overlap.  Wherever the index is, it is
not in the two places it should have been.

The compact encoding already places 99.9% of the lines, so this is a curiosity
rather than a blocker, and the remaining handful are shorter by a word.

## A re-keyed record cannot be parsed page-wise

Reading an *installed* file back, two records vanish: their last string runs
past the next record with no terminator.  Both are re-keyed, and page-wise
that span is noise, terminator included.  At their own run alignment they
terminate exactly where they should.  Nothing is wrong with the file -- but
`parse` should mend before validating, or extracting from an installed file
would quietly drop them.  It does not bite today because every build reads the
stock `.bak`.


## Story: pages, compression, and an index we can write

SLOT.DAT is bounded the way PS3 and PSP were -- a block must fit the
`footprint` pages its KEY entry claims -- but two things make it far looser
than the briefing.  The bound is a page rather than a byte, so most blocks
carry a couple of kilobytes of slack for nothing; and `build_block` is
`zlib.compress(data, 9)`, so the budget applies to the compressed stream,
which suits an encoding of one or two bytes a glyph with the same forms
repeating.

A full build placed **99 636 strings into 397 blocks, with 30 over**.  Those
30 carry 8 577 references, 7.9% of what was placed, and they are over by a
median of one page and at worst two, on footprints of four to six.

There is nowhere to put them today: the KEY's highest claimed page is exactly
the file length, so the container is tiled with no slack at either end.  But
EXLANG's SLOT.DAT is 340 KB smaller than MLG's, so the length is derived from
the KEY and not fixed anywhere -- and unlike the briefing, whose index was
never found, **this index is a file we can rewrite**.  Relocating an
overflowing block to a lengthened tail and rewriting its KEY entry is
therefore a real option rather than a hope.


## One language per block, and the write that forgot

Asked whether the PS3 trick of stubbing unused languages would free space in
SLOT.DAT, the measurement answered something else.  Every overflowing block
turned out to hold references of a single language -- `{'sp': 3433}`,
`{'jp': 1232}`, `{'ge': 1073}` -- so a block is one language, like a briefing
record, and there is nothing inside an English one to stub.

What it did expose is that ten of the fourteen overflowing blocks were not
English at all.  `build_menus` and `stagedat.build` check a reference's
language before writing; `build_story` never did.  The pool is shared, so any
string the English happened to share with the Spanish had Arabic written into
the Spanish block as well, paid for out of that block's page budget and then
reported to the translator as a line needing a trim.

    blocks written  413 -> 104        strings placed  99 672 -> 62 119
    blocks over      14 -> 4          lines to trim    1 913 -> 792

Verified against stock, reference by reference across all 2 137 blocks: 59 456
English references changed and **zero** non-English ones.

A first check said 794 non-English references still carried Arabic.  They did
not: it compared each reference's text against the set of rendered
translations, and plenty of translations are identical to their source -- a
calibre like `9x18mm`, a placeholder like `xxx` -- so untouched French text
matched.  "Does this look translated" is not the same question as "did we
write this", and only the second one is answerable by comparing with stock.


## The last two blocks were never about the text

With the language filter in, four blocks were still over.  Two of them read:

    block 00843  Arabic is 1 395 bytes *smaller* than the English it replaced
    block 1A79A  Arabic is   556 bytes smaller

Smaller text, still too big -- so trimming was the wrong remedy and would have
been an evening spent on the wrong thing.  The deficit is the encoder.
``build_block_smallest`` was trying four settings and all of them at the
default 32K window; these blocks pack tighter at **wbits 13**, small enough
that the unmodified content fits with 530 bytes to spare.  Eighteen encodings
are tried now, and each candidate is inflated again before it is accepted --
a smaller window is legal, since the size travels in the zlib header, but a
block that will not inflate is a scene that will not play.

That cleared three of the four.  The last, 1A79A, is the only block in 2 137
holding all six languages, which is the case the PS3 tooling's stubbing was
built for -- so it is stubbed, and only it: never a block that already fits,
never Japanese, and always named in the log so its audio can be checked.

    blocks over  14 -> 4 (language filter) -> 1 (encoder) -> 0 (stubbing)
