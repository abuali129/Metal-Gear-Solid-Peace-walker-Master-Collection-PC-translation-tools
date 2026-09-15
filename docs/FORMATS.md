# Peace Walker file formats

Everything here was recovered from the game's own executable and from the
archives themselves. Function addresses refer to
`METAL GEAR SOLID PEACE WALKER.exe` **after the SteamStub layer is removed**,
image base `0x140000000`.

*(Українською — [нижче](#формати-файлів-peace-walker).)*

---

## 1. The Master Collection cipher

Every asset the Master Collection ships is wrapped in one stream cipher. It is
symmetric, so the same routine encrypts and decrypts.

**Step 1 — the key is the file name.** Not the path, not the contents:

```
h = 0
for c in basename:            # after the last  /  \  :   and skip one leading '_'
    if c == '.': break        # the extension is NOT part of the key
    h = (h * 0x2356F + int8(c) * 0x1D35) mod 2^32
```

`FUN_14010F440`. The character is **sign-extended** to a signed byte first,
which matters for any name containing a high byte.

**Step 2 — MT19937, seeded the 1998 way** (`FUN_14010EFF0`):

```
for k in 0..623:
    v  =  s & 0xFFFF0000
    s  = (s * 69069 + 1) mod 2^32
    v |= (s & 0xFFFF0000) >> 16
    s  = (s * 69069 + 1) mod 2^32
    mt[k] = v
```

then an **immediate full twist and temper**, so a block of output is already
waiting and the read index is 0. Miss that and every byte is wrong.

**Step 3 — skip five words.** `FUN_14010ED80(rng, 0x14)` sets the index to
`0x14 / 4 = 5`. The keystream a file sees starts at word 5.

**Step 4 — apply** (`FUN_14010F5B0`):

```
for each little-endian dword:  word ^= mt_next() ^ 0xB9D3018F
```

Trailing bytes past the last whole dword take one more keystream word and are
XORed from its **low byte upward**.

### Two traps

**The naive vectorised twist is wrong.** Splitting the MT block into two slices
— the version in most numpy snippets — produces a different keystream. For
`k >= 454` the scalar loop reads `mt[k-227]`, which was already rewritten
earlier in the *same* pass; numpy evaluates the whole right-hand side before
assigning, so a two-slice version reads stale values. The block must split at
**227 / 454 / 623**, four slices.

The damage is distinctive and easy to misread: exactly **169 of 624 words
differ, in one contiguous run [454..622]**. So every 2496-byte block decrypts
correctly for its first 1816 bytes and turns to noise for the last 680. If your
output looks "half right, half wrong", this is why.

**The stream restarts on every read.** See §3 — this is what hides the .PDT
payloads.

### Proof it is right

Every `.olang` decrypts to a header whose first word is `0x00584252` (`"RBX"`)
and whose second word equals the file's own name in hex — `00327f6a.olang`
gives `0x00327F6A`. Two independent checks landing at once.

---

## 2. `strcode` — the 24-bit name hash

Peace Walker names almost everything by a 24-bit hash rather than a string:

```
h = 0
for c in name:                # stops at the first '.'
    h = (((h << 5) | (h >> 19)) + ord(c)) & 0xFFFFFF
```

`strcode("en") == 0x0D0E`, which is exactly the language tag on the first
reference of every `.olang` entity — that is how the language codes were
identified.

It also names the archives on disk. Every shipped container resolves:

| PSP name | strcode | file | size |
|---|---|---|---|
| `STAGEDAT.PDT` | `9645FA` | `009645fa.PDT` | 511 MB |
| `BGM.PDT` | `01112D` | `0001112d.PDT` | 105 MB |
| `VOICEBF.PDT` | `B2B2A8` | `00b2b2a8.PDT` | 279 MB |
| `VOICEPS.PDT` | `B2B475` | `00b2b475.PDT` | 2.8 MB |
| `VOICERT.PDT` | `B2B4B6` | `00b2b4b6.PDT` | 82 MB |
| `SLOT.DAT` | `2ABA34` | `002aba34.DAT` + `.KEY` | 544 MB |
| `BRIEFING.DAT` | `76531D` | `0076531d.DAT` | 4.2 MB |

Eight for eight. `SLOT.DAT` and `SLOT.KEY` share one name because the hash
stops at the dot.

---

## 3. `.PDT` — the container

Three layers, and the middle one comes in two flavours.

### Header, after layer 1

```
0x00  u32 HashKey1        0x10  u32 magic 0x007A7E9C
0x04  u32 HashKey2        0x14  u32 version 0x00010000
0x08  u32 HashKey3        0x18  u16 EntityCount
0x0C  u32 ?               0x20  u32 LookupStart
                          0x28  Entity[count] { u32 size, u32 hash, u32 pos }
             LookupStart:  Lookup[count] { u32 key, u32 index, u32, u32 }
```

`LookupStart == 0x28 + count*12` holds exactly for all five `.PDT` files, the
entities tile the file with zero overlaps, and the last one ends at EOF. The
magic at `0x10` is the fastest check that a decrypt worked.

### Layer 2 — two ciphers, chosen by `HashKey2`

Read straight out of `FUN_1401238c0`:

```
HashKey2 == 0   ->  FUN_140123dc0:  every byte ^= (HashKey1 & 0xFF)
otherwise       ->  FUN_140123cb0:  every dword ^= High
                                    High = High * 0x2E90EDD + Low
```

where the state comes from `FUN_140123da0`:

```
h0   = HashKey1 ^ HashKey2
High = h0 | ((h0 ^ 0x6576) << 16)
Low  = h0 * HashKey3
```

`0x2E90EDD` is 48 828 125, i.e. 5^11.

Only `STAGEDAT.PDT` has non-zero `HashKey2/3`, so it is the only archive using
the LCG — and the only one whose entries are compressed. The four byte-XOR
archives hold sector-aligned streaming media, which is exactly what BGM and the
three VOICE archives should be.

### Layer 3 — and the thing that hides it

**The Master Collection keystream restarts at the beginning of every entity.**

The header and both tables are read back to back from offset 0 through a single
generator, so decrypting the file as one continuous stream gives perfect
tables. Every entity is then fetched by its own seek-and-read, and each of
those reads gets a **fresh** generator, index 5 again. Decrypt the file as one
stream and you get flawless tables followed by garbage payloads — which is
precisely the symptom that stalls people here.

So, per entity:

```
seg = mc_decrypt(raw[pos : pos+size], container_filename)   # stream from word 5
layer2(seg, fresh cipher state)                             # state copied, never carried
u32 uncompressed_size, then a zlib stream
```

Layer 2 restarts per entity too — the state is copied, never advanced from the
previous entity.

**Result: `STAGEDAT.PDT` unpacks 557 of 557 entries, every one inflating to
exactly its declared size.** About 1.3 GB.

---

## 4. QAR — the sub-archive inside `.PDT` entries

142 of `STAGEDAT.PDT`'s entities are QAR archives of named `.txp` files.
Little-endian, and the table of contents is at the **end**:

```
0x00    data, each entry padded up to a 128-byte boundary
<toc>   u32 count
        count x { u32 hash, u32 size }
        count x asciiz name
EOF-4   u32 toc
```

Entry 0 starts at offset 0; each following entry starts at the 128-byte-aligned
end of the previous one.

Other entity payloads: `.nocache`/`.cache` manifests, `oEbN` blobs
(`0x4E62456F` — the same magic `BRIEFING.DAT` opens with), `.mdp` models, and
129 entities carrying DDS textures.

---

## 5. `.olang` — the text tables

A six-language string pool addressed by 24-bit hashes. Everything
little-endian; string offsets are relative to `BodyOffset`, all others absolute.

```
0x00 u32 magic 0x00584252 "RBX"      0x10 u32 GroupOffset  (always 0x20)
0x04 u32 strcode - its own name      0x14 u32 EntityOffset = Group  + GC*8
0x08 u32 reserved (0)                0x18 u32 RefOffset    = Entity + EC*8
0x0C u32 GroupCount << 16            0x1C u32 BodyOffset   = Ref    + RC*12

GROUP  [GC] x8   u32 Key, u16 EntityIndex, u16 EntityCount   (sorted, bsearch)
ENTITY [EC] x8   u32 Key, u16 RefIndex,    u16 RefCount (= 6 * slots)
REF    [RC] x12  u32 Lang, u32 Offset, u32 Style
BODY             UTF-8, NUL-terminated, padded to 2 bytes, no slack
```

Only the four offsets are stored — the counts are derived from them.

References inside one entity are grouped **by language first, then by slot**:
`ref[RefIndex + lang*slots + slot]`. The six languages, in order, are
`en fr ge it jp sp`.

`Style` is `0x0402` for normal text and `0x0001` for the caps-only UI font. In
Style 1 strings the accented letters are **already remapped onto ASCII slots** —
`"hQUIPE"` is what renders as `"EQUIPE"` — because that font has no lowercase
and no accents (§6).

`MLG\Text` carries all six languages. `EXLANG\Text` is a **Spanish-only
overlay**: en/fr/ge/it/jp are the empty string in all three of its files.

Round trip is byte-exact on all 17 shipped files.

### Limits when repacking

Strings may grow freely — offsets are `u32` and the tables are rebuilt. But
`Group.EntityIndex`, `Group.EntityCount`, `Entity.RefIndex` and
`Entity.RefCount` are all `u16`, and `EXLANG\00c7f1dc.olang` already sits at
RefIndex 60348 of 65535, so new *entities* cannot be added there. Editing text
is unconstrained.

---

## 6. Fonts — three separate systems

Peace Walker does not have "a font". It has three, and none of them contains a
single Cyrillic glyph.

### 6.1 `FONT\*.xpr` — subtitles

Not a PSP `.PGF`. The Master Collection build is the **HD (Xbox 360 / PS3)**
port, so these are **XPR2** resource bundles, big-endian:

```
'XPR2', u32 headerSize, u32 dataSize, u32 resourceCount = 2
then 24-byte entries:  'TX2D' -> "FontTexture",  'USER' -> "FontData"
```

The texture is a **linear, untiled, 8-bit alpha atlas** — no mips, no swizzle.
`reshape(h, w)` is the whole decoder.

The character map is a **flat table indexed by the Unicode code point itself**:
`charmap[codepoint] -> glyph index`, 65 375 entries covering U+0000..U+FF5E,
u16 big-endian, glyph 0 being `.notdef`. Not Shift-JIS, not a sequential index.
Glyph records are 16 bytes: `u0 v0 u1 v1 bearingX width advance 0`.

| file | atlas | glyphs | codes |
|---|---|---|---|
| `0007ccd8.xpr` | 4096 x 4096 | 643 | 642 (Japanese, 323 kanji) |
| `000ebbe8.xpr` | 2048 x 1024 | 459 | 458 (Latin, 155 kanji) |

Both are subsets — only what the shipped script uses was baked in. The ASCII
block is pixel-identical between them.

### 6.2 `db_font.txp` / `fontprint.txp` — inside `STAGEDAT.PDT`

Despite the extension these are not `.txp` containers; they are raw blobs with
**DDS textures embedded inside them**. `fontprint.txp` carries a 512x128 DXT5
sheet and a 256x128 one. The glyph rectangle table for these has not been
located yet.

### 6.3 `.txp` texture packages — the UI font

```
0x00  u32 flags        0x18  u32 master table offset  (always 0x30)
0x04  u32 strcode      0x20  u32 sub table offset     (0x30 + 32*count)
0x08  u32 count        0x30  master[count], 32 bytes each
0x0C  u32 count              then the payload: `count` DDS files back to back
```

The master records are not needed to get pixels out — every texture carries a
real `DDS ` header, so scanning for the magic is exact and simpler.

The `strcode` at `0x04` is the name of the **set**, not of the file:
`005302d4`, `005318e4`, `005318e5`, `0082988a` and `008299c5` all carry
`0x0024E502`.

626 textures across six packages, 159 of them font-like. The canonical UI sheet
is **`008299c5` texture 10: 512x512 DXT5, 16 columns, cell 32x48,
`cell = code - 0x20`**:

```
row 0   U+0020..U+002F      row 3   U+0050..U+005F   P..Z [ ¥ ] ^ _
row 1   U+0030..U+003F      row 4   U+00C0..U+00CF   À Á Â Ã Ä Å Ç È...
row 2   U+0040..U+004F      row 5   U+00D0..         Ô Ö Ù Ú Û Ü Ý Ÿ Œ Þ Ñ
```

It jumps from `_` (U+005F) straight to `À` (U+00C0) — **there are no lowercase
letters at all**. That is the caps-only font `.olang` marks with Style
`0x0001`, and it explains the pre-remapped strings there.

Rows 7-10 of the 512px sheet are empty, so there is room for more glyphs.

### Adding Cyrillic

The `.xpr` fonts need no engine patch — their character map is indexed by real
Unicode, and 64 917 slots are still zero, the whole Cyrillic block included.
The JP atlas has 3 484 empty rows (about 5 300 glyph slots) and the Latin one
276. So this is data work. It is simply not written yet.

---
---

## 7. `SLOT.DAT` - where the story text actually is

`MLG\disc0_rel\002aba34.DAT` + `002aba34.KEY` is the PSP `SLOT.DAT`/`SLOT.KEY`
pair. It holds **the half of the script the shipped `.olang` files do not**:
4 458 olang files across 2 137 compressed blocks, 145 groups, 52 396 distinct
strings, ~10 000 English lines - every subtitle included. The line
`Hey, Boss, wanna go a round?` is in here and nowhere else in the install.

Three layers, each with a trap:

1. **Master Collection MT19937** (`pwcrypt`), keyed by the name the *engine*
   knows - `002aba34.DAT` / `002aba34.KEY`, never the local path. The KEY is one
   pass over the whole file. The DAT's stream **restarts at every block**,
   exactly as it restarts per entity in a `.PDT`. Decrypting the DAT as one
   continuous 500 MB stream gives a perfect first block and garbage after it.
2. **An affine word cipher**: `word ^= acc; acc = acc*0x02E90EDD + low`, seeded
   from the KEY's 12-byte header. It restarts per block, so one keystream serves
   all 2 137. This is the same recurrence MGS4 uses for its stage pages - the
   constant and the `0x6576` salt twist are shared between the two games.
3. **zlib**, behind `{u32 0x00100004, u32 0, u32 csize, u32 usize}`.

### The KEY record, and the field that misleads

20 bytes: `rawStart`, `rawEnd`, `hash`, `tag`, `pageCount`, with
`rawStart = (tag << 20) | startPage` and `rawEnd = (pageCount << 20) | endPage`.

`pageCount` is **not** the footprint. `endPage - startPage` is. Measured on the
stock MLG container, `startPage..endPage` tiles the whole file - 132 947 pages,
**zero holes, zero overlaps**, ending exactly at the last page - while
`startPage..startPage+pageCount` appears to leave 13 447 pages (~55 MB) spare.
Those pages are not free space; they are the zero padding **inside each block's
own allocation**. Reading them as a free list and relocating other blocks into
them writes one block inside another's span and stops the records tiling, which
matters because the game binary-searches them by page.

So the in-place budget is `endPage - startPage`, which is *more* room than
`pageCount` for 389 of the 2 137 blocks - 55 MB of real headroom - and there is
no free list at all. A block that outgrows its footprint has nowhere safe to go.

### The hash is an identity, not a checksum

Measured, not assumed: over 401 stock blocks the record hash matches CRC32 of the
inflated data **0 times** and CRC32 of the zlib bytes **0 times**. The game
resolves resources by it. Never recompute it - a stale hash with fresh bytes
works; a "corrected" hash loses models and textures.

### The `.slot` element container

Each block inflates to a flat directory of typed elements:

```
0x00  u32 count        entries + 3        0x08  u64 0x7F000002
0x04  u32 0                               0x10  u64 data_size
0x18  count x {u64 key, u64 offset}       then 0x7F000000, data_size, 0
      data_base = ((count << 4) + 0x1007) & ~0xFFF
      elements back to back, each padded to a multiple of 16
```

`key` is `(ext_byte << 24) | strcode24(name)`; the name itself is not stored.
The extension byte table has collisions - `mdp`, `mdc`, `mdl`, `mdb` all hash to
`0x13` - so the byte cannot tell models apart, and the last definition wins.

The slack between elements is **not always zero**: the engine writes into a reused
buffer and leaves whatever was there. Elements are therefore kept as their full
padded region, which is what makes an untouched rebuild byte-identical
(verified 2 137 / 2 137).

### Editing

`slottext.py` exports the same `### N - KEY - ORIGINAL` blocks as everything
else, and matches translations back **by original text**, not by key: the same
English line occurs in many scenes, and keying would translate only the first.
Pool entries are shared between languages, so a reference is re-pointed at a new
pool entry rather than edited in place - and the pool is then compacted, because
`build()` writes the whole pool and an orphaned string leaves bytes no offset
points at, which makes the file unreadable on the next parse.


# Формати файлів Peace Walker

Усе тут відновлено з виконуваного файлу самої гри та з її архівів. Адреси
функцій стосуються `METAL GEAR SOLID PEACE WALKER.exe` **після зняття шару
SteamStub**, база образу `0x140000000`.

---

## 1. Шифр Master Collection

Кожен ассет, який постачає Master Collection, загорнутий в один потоковий шифр.
Він симетричний, тож та сама процедура і шифрує, і розшифровує.

**Крок 1 — ключем є ім'я файлу.** Не шлях і не вміст:

```
h = 0
для кожного символа basename:   # після останнього  /  \  :  , пропустити один '_' на початку
    якщо c == '.': стоп         # розширення НЕ входить у ключ
    h = (h * 0x2356F + int8(c) * 0x1D35) mod 2^32
```

`FUN_14010F440`. Символ спершу **розширюється зі знаком** до знакового байта —
це важливо для будь-якого імені з високим байтом.

**Крок 2 — MT19937, засіяний за схемою 1998 року** (`FUN_14010EFF0`), а далі —
**одразу повний скрут із темперуванням**, тож блок виводу вже готовий, а індекс
читання дорівнює нулю. Пропустиш це — і кожен байт буде хибним.

**Крок 3 — пропустити п'ять слів.** `FUN_14010ED80(rng, 0x14)` виставляє індекс
`0x14 / 4 = 5`.

**Крок 4 — застосувати** (`FUN_14010F5B0`):

```
для кожного 4-байтового слова:  слово ^= mt_next() ^ 0xB9D3018F
```

Хвостові байти після останнього цілого слова беруть ще одне слово потоку і
XOR-яться **від його молодшого байта вгору**.

### Дві пастки

**Наївна векторизована реалізація скруту хибна.** Поділ блоку MT на два зрізи —
той варіант, що трапляється в більшості прикладів на numpy — дає інший потік.
Для `k >= 454` скалярний цикл читає `mt[k-227]`, які вже перезаписані в цьому ж
проході; numpy обчислює праву частину цілком до присвоєння, тож дворізовий
варіант читає застарілі значення. Різати треба на **227 / 454 / 623**, чотири
зрізи.

Наслідок дуже характерний: розходяться рівно **169 слів із 624, суцільним
блоком [454..622]**. Тобто кожні 2496 байт розшифровуються правильно перші 1816
і перетворюються на сміття в останніх 680. Якщо результат виглядає «наполовину
правильний» — причина саме тут.

**Потік перезапускається на кожному читанні.** Див. §3.

### Доказ правильності

Кожен `.olang` розшифровується в заголовок, де перше слово — `0x00584252`
(`"RBX"`), а друге дорівнює власному імені файлу в шістнадцятковому вигляді:
`00327f6a.olang` дає `0x00327F6A`. Два незалежні збіги одночасно.

---

## 2. `strcode` — 24-бітний хеш імені

Гра називає майже все 24-бітним хешем, а не рядком:

```
h = 0
для кожного символа:            # зупиняється на першій крапці
    h = (((h << 5) | (h >> 19)) + ord(c)) & 0xFFFFFF
```

`strcode("en") == 0x0D0E` — саме це значення стоїть мовною міткою на першому
посиланні кожного запису `.olang`; так і були визначені коди мов.

Цей же хеш дає імена архівів на диску: `STAGEDAT.PDT` → `9645FA` →
`009645fa.PDT` (511 МБ), `BGM.PDT` → `0001112d.PDT`, `VOICEBF/PS/RT.PDT` →
`00b2b2a8` / `00b2b475` / `00b2b4b6`, `SLOT.DAT` → `002aba34` (разом із `.KEY`,
бо хеш зупиняється на крапці), `BRIEFING.DAT` → `0076531d`. Вісім із восьми.

---

## 3. `.PDT` — контейнер

Три шари, і середній має два різновиди. Заголовок після першого шару містить
три ключі, магію `0x007A7E9C` за зсувом `0x10`, версію, кількість записів і
`LookupStart`. Рівність `LookupStart == 0x28 + count*12` виконується точно для
всіх п'яти файлів, записи вкривають файл без перекриттів, останній закінчується
рівно на кінці файлу.

### Другий шар — два шифри, вибір за `HashKey2`

Прочитано просто з `FUN_1401238c0`:

```
HashKey2 == 0   ->  FUN_140123dc0:  кожен байт ^= (HashKey1 & 0xFF)
інакше          ->  FUN_140123cb0:  кожне слово ^= High
                                    High = High * 0x2E90EDD + Low
```

а стан походить із `FUN_140123da0`: `h0 = HashKey1 ^ HashKey2`,
`High = h0 | ((h0 ^ 0x6576) << 16)`, `Low = h0 * HashKey3`.

Ненульові `HashKey2/3` має лише `STAGEDAT.PDT` — тому він єдиний працює через
LCG і єдиний зі стисненим вмістом. Чотири «байтові» архіви містять потокову
медіа з секторним вирівнюванням, тобто саме те, чим і мають бути BGM та три
архіви озвучки.

### Третій шар — і те, що його ховає

**Потік Master Collection перезапускається на початку кожного запису.**

Заголовок і обидві таблиці читаються поспіль від нуля через один генератор,
тому суцільний потік дає ідеальні таблиці. А кожен запис дістається окремим
seek+read, і на кожне таке читання створюється **новий** генератор, знову з
індексу 5. Розшифруєш файл як єдиний потік — отримаєш бездоганні таблиці й
сміття у вмісті. Це рівно той симптом, на якому тут усі й спиняються.

Отже, на кожен запис: зняти шар Master Collection з його власних байтів, потім
другий шар зі **свіжим** станом шифру, а далі `u32 розмір` і потік zlib.

**Результат: `STAGEDAT.PDT` розпаковується повністю — 557 із 557 записів, і в
кожного розпакований розмір точно дорівнює оголошеному.** Близько 1,3 ГБ.

---

## 4. QAR — підархів усередині записів `.PDT`

142 записи `STAGEDAT.PDT` — це QAR-архіви іменованих `.txp`. Таблиця вмісту
лежить **у кінці**: останні 4 байти вказують на неї, далі `u32 count`,
`count` пар `{u32 hash, u32 size}` і `count` нуль-термінованих імен. Запис 0
починається зі зсуву 0, кожен наступний — з вирівняного на 128 байт кінця
попереднього.

---

## 5. `.olang` — текстові таблиці

Пул рядків на шість мов, адресований 24-бітними хешами. Зберігаються лише
чотири зсуви — кількості виводяться з них. Посилання всередині запису
згруповані **спершу за мовою, потім за слотом**: `ref[RefIndex + lang*slots +
slot]`. Мови в порядку: `en fr ge it jp sp`.

`Style` дорівнює `0x0402` для звичайного тексту і `0x0001` для капсового
UI-шрифту. У рядках стилю 1 акцентовані літери **вже перемальовані на слоти
ASCII** — `"hQUIPE"` показується як `"EQUIPE"` — бо в тому шрифті немає ні
малих літер, ні акцентів (§6).

`MLG\Text` містить усі шість мов. `EXLANG\Text` — це **іспанський оверлей**: у
всіх трьох його файлах en/fr/ge/it/jp порожні.

Перезбирання побайтово точне на всіх 17 файлах.

**Обмеження.** Рядки можуть рости вільно — зсуви 32-бітні, таблиці
перебудовуються. Але `EntityIndex`, `EntityCount`, `RefIndex` і `RefCount` —
16-бітні, а `EXLANG\00c7f1dc.olang` уже стоїть на RefIndex 60348 з 65535, тож
нові *записи* туди додати не вийде. Правити текст можна без обмежень.

---

## 6. Шрифти — три окремі системи

У Peace Walker немає «шрифту». Їх три, і в жодному немає жодної кириличної
літери.

**`FONT\*.xpr` — субтитри.** Це не PSP `.PGF`: Master Collection зібрано з
**HD-порту (Xbox 360 / PS3)**, тож це пакунки **XPR2**, big-endian. Текстура —
**лінійний 8-бітний альфа-атлас** без мипів і свізлу. Таблиця символів
**індексується прямо кодом Unicode**: `charmap[код] -> номер гліфа`, 65 375
записів. Японський: 4096×4096, 643 гліфи. Латинський: 2048×1024, 459.

**`db_font.txp` / `fontprint.txp` — усередині `STAGEDAT.PDT`.** Попри
розширення це не контейнери `.txp`, а сирі блоби з **вшитими DDS-текстурами**:
`fontprint.txp` містить аркуш 512×128 DXT5 і ще 256×128. Таблицю прямокутників
гліфів для них ще не знайдено.

**`.txp` — UI-шрифт.** Заголовок 0x30, далі таблиця по 32 байти, далі `count`
файлів DDS поспіль. `strcode` за зсувом `0x04` — це ім'я **набору**, а не
файлу. 626 текстур у шести пакетах, 159 схожих на шрифт. Головний аркуш —
`008299c5` текстура 10: **512×512 DXT5, 16 колонок, комірка 32×48,
`комірка = код − 0x20`**. Рядки 0–3 це `U+0020..U+005F`, далі стрибок одразу на
`À` (`U+00C0`) — **малих літер немає взагалі**. Це і є капсовий шрифт зі стилю
`0x0001`. Рядки 7–10 порожні.

**Щоб додати кирилицю** шрифтам `.xpr` не потрібен патч рушія: їхня таблиця
символів індексується справжнім Unicode, і 64 917 комірок досі нульові, разом з
усім кириличним блоком. У японському атласі вільні 3 484 рядки (близько 5 300
комірок під гліфи), у латинському — 276. Тобто це робота з даними. Її просто
ще не написано.


## 7. `SLOT.DAT` - де насправді лежить сюжетний текст

`MLG\disc0_rel\002aba34.DAT` + `002aba34.KEY` - це пара PSP `SLOT.DAT`/`SLOT.KEY`.
Саме тут **та половина сценарію, якої немає у комплектних `.olang`**: 4 458
olang-файлів у 2 137 стиснених блоках, 145 груп, 52 396 унікальних рядків,
близько 10 000 англійських - усі субтитри включно. Рядок
`Hey, Boss, wanna go a round?` є тут і більше ніде у грі.

Три шари, у кожному пастка:

1. **MT19937 Master Collection** (`pwcrypt`), ключ - ім`я, яке знає *рушій*:
   `002aba34.DAT` / `002aba34.KEY`, а не локальний шлях. KEY розшифровується
   одним проходом. Потік DAT **перезапускається на кожному блоці**, так само як
   на кожній сутності у `.PDT`. Розшифрувати DAT одним суцільним потоком на
   500 МБ - це отримати ідеальний перший блок і сміття далі.
2. **Афінний пословний шифр**: `word ^= acc; acc = acc*0x02E90EDD + low`, стан з
   12-байтового заголовка KEY, перезапуск на кожному блоці. Та сама рекурента,
   що й у сторінках stage у MGS 4 - константа й твіст `0x6576` спільні для обох.
3. **zlib** за заголовком `{u32 0x00100004, u32 0, u32 csize, u32 usize}`.

### Запис KEY і поле, яке вводить в оману

20 байтів: `rawStart`, `rawEnd`, `hash`, `tag`, `pageCount`.

`pageCount` - **не** футпринт блоку. Футпринт - це `endPage - startPage`. Виміряно
на стоковому MLG: `startPage..endPage` вкриває весь файл - 132 947 сторінок,
**нуль дірок, нуль перетинів**, рівно до останньої сторінки. А
`startPage..startPage+pageCount` ніби лишає 13 447 вільних сторінок (~55 МБ).
Вони **не вільні** - це нульове доповнення **всередині власного блоку**. Вважати
їх вільним списком і переселяти туди інші блоки означає записати один блок
усередину чужого діапазону; записи перестають вкривати файл, а гра шукає їх
двійковим пошуком за сторінкою.

Отже, бюджет для запису на місці - `endPage - startPage`, і для 389 із 2 137
блоків це **більше** місця, ніж `pageCount`: 55 МБ реального запасу. Вільного
списку не існує взагалі.

### Хеш - це ідентифікатор, а не контрольна сума

Виміряно, а не припущено: на 401 стоковому блоці хеш запису збігається з
CRC32 розпакованих даних **0 разів** і з CRC32 zlib-байтів **0 разів**. Гра за
ним знаходить ресурси. **Ніколи не перераховувати** - старий хеш зі свіжими
байтами працює, «виправлений» хеш втрачає моделі й текстури.

### Контейнер елементів `.slot`

Кожен блок розпаковується у плаский каталог типізованих елементів; `key` - це
`(байт_розширення << 24) | strcode24(назва)`, сама назва не зберігається. У
таблиці розширень є колізії - `mdp`, `mdc`, `mdl`, `mdb` дають `0x13`.

Проміжки між елементами **не завжди нульові**: рушій пише у повторно
використаний буфер. Тому елемент зберігається разом із доповненням - саме це
робить перезбірку недоторканого блоку побайтово тотожною (перевірено 2137/2137).

### Редагування

`slottext.py` віддає ті самі блоки `### N - KEY - ORIGINAL`, а переклад
підставляє **за оригінальним текстом**, а не за ключем: той самий англійський
рядок трапляється у багатьох сценах, і прив`язка до ключа переклала б лише
перший. Записи пулу спільні для мов, тому посилання перенаправляється на новий
запис, а не редагується на місці; після цього пул ущільнюється - інакше
осиротілий рядок лишає у тілі байти, на які ніщо не вказує, і файл більше не
читається.
