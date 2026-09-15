# Reading the game executable

Three small tools used to work out how the PC build addresses `BRIEFING.DAT`.
They exist because a 9.5 MB `.text` cannot be swept linearly -- capstone stops
at the first byte that is not an instruction -- so both of the search tools
work by scanning for the **encoding** rather than by disassembling, and take
function boundaries from `.pdata`, which lists all 40 715 of them outright.

They point at the Steamless-unpacked exe; the path is at the top of each file.

```
python tools/exe_disasm.py <VA> [instructions] [bytes-back]
    disassemble a window, e.g.  python tools/exe_disasm.py 0x1400A56C0 40

python tools/exe_refs.py <lo VA> [hi VA]
    every RIP-relative reference into a VA range, with the function each
    one sits in -- how the briefing state block at 0x141180080 was mapped

python tools/exe_calls.py <VA>
    every call/jmp rel32 landing on a function
```

Findings are in [../docs/PC_EXE.md](../docs/PC_EXE.md).

Needs `capstone` (`pip install capstone`), which the toolkit itself does not.
