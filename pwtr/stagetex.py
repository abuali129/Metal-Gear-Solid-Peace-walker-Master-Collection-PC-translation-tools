r"""Putting edited STAGEDAT textures back.

The deep unpack follows four levels down -- entity, QAR sub-archive, ``.txp``
member, DDS -- and writes a PNG per texture::

    STAGEDAT/0178_@cache1_qar/title_cz0_tex/07_2048x1152_DXT1.png

That path is the whole address, so it is also the way back.  A texture is
overwritten inside its member at the offset it already occupies: block
compression makes the encoded size a function of the dimensions alone, so a
PNG of the same size lands on exactly the bytes it came from and nothing in
the container moves.  The entity is then handed to ``pdt_pack.build``, which
recompresses it and rebuilds the tables -- the same write path the mission
text uses.

Only textures whose *pixels* differ from the game's are touched.  Encoding is
lossy, so writing back one that was never edited would cost a generation of
compression for nothing.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from pwtr.formats import pdt_pack, qar, txp_pack

#: ``STAGEDAT/<index>_<name>_qar/<member>_tex/NN_WxH_FMT.png``
TEX_NAME = re.compile(r"^(\d+)_(\d+)x(\d+)_(\w+)\.png$", re.I)
ENTITY_DIR = re.compile(r"^(\d+)_(.*?)(_qar)?$")

NAME = "009645fa.PDT"


class Edit:
    """One edited PNG, and where it goes."""

    def __init__(self, path: Path, entity: int, member: str, index: int,
                 width: int, height: int, fourcc: str):
        self.path = path
        self.entity, self.member, self.index = entity, member, index
        self.width, self.height, self.fourcc = width, height, fourcc

    def __repr__(self) -> str:
        return (f"<{self.path.name} entity {self.entity} "
                f"{self.member}[{self.index}]>")


def candidates(root: str | Path) -> list[Edit]:
    """Every texture PNG under a STAGEDAT unpack, with its address."""
    root = Path(root)
    out: list[Edit] = []
    for tex_dir in sorted(root.glob("*/*_tex")):
        entity_dir = tex_dir.parent
        match = ENTITY_DIR.match(entity_dir.name)
        if not match:
            continue
        for path in sorted(tex_dir.glob("*.png")):
            hit = TEX_NAME.match(path.name)
            if not hit:
                continue
            out.append(Edit(path, int(match.group(1)),
                            tex_dir.name[:-len("_tex")], int(hit.group(1)),
                            int(hit.group(2)), int(hit.group(3)),
                            hit.group(4).upper()))
    return out


def _slots(member: bytes):
    """(offset, w, h, fourcc, payload size) for every DDS in a member.

    The same scan the unpacker used, so the numbering matches the file names
    it wrote -- which is the only thing tying a PNG to a slot.
    """
    found = []
    for match in re.finditer(b"DDS ", member):
        start = match.start()
        if start + 128 > len(member):
            continue
        height, width = struct.unpack_from("<2I", member, start + 12)
        fourcc = member[start + 84:start + 88]
        if fourcc not in (b"DXT1", b"DXT3", b"DXT5"):
            continue
        if not (0 < width <= 8192 and 0 < height <= 8192):
            continue
        block = 8 if fourcc == b"DXT1" else 16
        size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block
        if start + 128 + size > len(member):
            continue
        found.append((start, width, height, fourcc, size))
    return found


def recent(edits: list[Edit], margin: float = 60.0) -> float:
    """A cutoff that separates edited files from the unpack that wrote them.

    An unpack writes thousands of PNGs within a couple of minutes, so the
    median of their timestamps is the unpack itself and anything much newer
    was touched afterwards.  It is only ever a shortlist -- what actually
    decides is whether the pixels differ -- but it is the difference between
    reading one entity and decoding all 557.
    """
    stamps = sorted(e.path.stat().st_mtime for e in edits)
    if not stamps:
        return 0.0
    return stamps[len(stamps) // 2] + margin


def plan(pdt_path: str | Path, root: str | Path, note=lambda _m: None,
         since: float | None = None, base: dict | None = None):
    """-> ({entity: new plaintext}, [what was written], [complaints])

    ``since`` skips PNGs untouched since then.  None checks every one, which
    is right and slow: it decodes every texture in the container.

    ``base`` is what another build already made of these entities -- the
    mission text, in practice.  STAGEDAT carries both, and both are written
    by replacing whole entities, so a texture spliced into the *stock*
    entity would throw away the translation that had just been put in it.
    Starting from what is already there is what lets the two share a file.
    """
    template = pdt_pack.Template(str(pdt_path))
    base = base or {}
    wanted = candidates(root)
    if since is not None:
        keep = [e for e in wanted if e.path.stat().st_mtime > since]
        note(f"{len(keep)} of {len(wanted)} texture(s) touched since the "
             f"unpack -- checking those")
        wanted = keep
    by_entity: dict[int, list[Edit]] = {}
    for edit in wanted:
        by_entity.setdefault(edit.entity, []).append(edit)

    payloads: dict[int, bytes] = {}
    written: list[Edit] = []
    complaints: list[str] = []

    for entity, edits in sorted(by_entity.items()):
        data = base.get(entity)
        if data is None:
            try:
                data = template.payload(entity)
            except Exception as problem:
                complaints.append(f"entity {entity}: {problem}")
                continue
        if not data:
            continue
        items = qar.parse(data)
        buffer = bytearray(data)
        touched = False
        for edit in edits:
            if items:
                item = next((i for i in items
                             if Path(i["name"]).stem == edit.member), None)
                if item is None:
                    complaints.append(
                        f"{edit.path.name}: no member {edit.member} in "
                        f"entity {entity}")
                    continue
                where, span = item["off"], item["size"]
            else:
                where, span = 0, len(data)
            slots = _slots(bytes(buffer[where:where + span]))
            if edit.index >= len(slots):
                complaints.append(
                    f"{edit.path.name}: {edit.member} holds {len(slots)} "
                    f"texture(s), so there is no {edit.index}")
                continue
            offset, width, height, fourcc, size = slots[edit.index]
            if (width, height) != (edit.width, edit.height):
                complaints.append(
                    f"{edit.path.name}: that slot is {width}x{height}")
                continue
            at = where + offset
            original = bytes(buffer[at:at + 128 + size])
            slot = {"w": width, "h": height, "fcc": fourcc}
            problems: list[str] = []
            new = txp_pack._slot_bytes(str(edit.path), slot, problems,
                                       original)
            if new is None:
                complaints += [f"{edit.path.name}: {p}" for p in problems]
                continue
            if new == original:
                continue                    # not edited: leave it alone
            if len(new) != len(original):
                complaints.append(
                    f"{edit.path.name}: encodes to {len(new)} bytes, the "
                    f"slot holds {len(original)}")
                continue
            buffer[at:at + len(new)] = new
            written.append(edit)
            touched = True
        if touched:
            payloads[entity] = bytes(buffer)
            note(f"entity {entity}: "
                 f"{sum(1 for e in written if e.entity == entity)} texture(s)")
    return payloads, written, complaints


def merge(payloads: dict, pdt_path: str | Path, root: str | Path,
          note=lambda _m: None, since: float | None = None) -> tuple:
    """Splice edited textures into payloads another build has prepared.

    ``payloads`` is updated in place, so one ``pdt_pack.build`` writes the
    text and the pictures together.
    """
    mine, written, complaints = plan(pdt_path, root, note, since, payloads)
    payloads.update(mine)
    return written, complaints


def build(pdt_path: str | Path, root: str | Path, out_path: str | Path,
          note=lambda _m: None, since: float | None = None) -> dict:
    """Write a STAGEDAT.PDT carrying the edited textures and nothing else.

    Careful: this starts from the game's own container, so it does NOT carry
    the mission text.  Use the project's stage build for both.
    """
    payloads, written, complaints = plan(pdt_path, root, note, since)
    if not payloads:
        note("Nothing edited -- no texture differs from the game's own.")
        return {"textures": 0, "entities": 0, "complaints": complaints}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    note(f"Rebuilding {NAME} with {len(payloads)} entity/entities changed...")
    pdt_pack.build(template=pdt_pack.Template(str(pdt_path)),
                   payloads=payloads, out_path=str(out_path), out_name=NAME)
    note(f"{len(written)} texture(s) written -> {out_path}")
    return {"textures": len(written), "entities": len(payloads),
            "complaints": complaints, "out": str(out_path)}
