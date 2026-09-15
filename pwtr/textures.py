"""The texture side: what has been edited, and how it gets back into a package.

An **edit set** here is a plain folder of folders -- one per ``.txp`` package,
named after it, holding the DDS files that package carries::

    <root>/
        008299c5/  00_256x256_DXT1.dds  10_512x512_DXT5.dds  contents.tsv
        0082988a/  ...

which is exactly what the vendored unpacker writes.  No index, no mapping file:
a texture's address *is* its file name, because the packer finds it by the
``NN`` prefix and splices it back over the same byte range.

## There is no copy of the original

MGS4 needed one; Peace Walker does not.  The stock bytes are always one cheap
read away inside the game's own package, so "the game's version" is read from
the game rather than from a duplicate folder that can drift out of date -- and
"edited" means *differs from what the game ships*, which is the question that
actually matters, rather than *differs from whatever was dumped last time*.

## What a replacement may not change

Width, height, format and byte length.  The game addresses textures by offset
inside the package, so a texture of a different size pushes every later one out
of place.  The packer refuses those rather than corrupting the file, and this
module refuses them earlier, where the message can still name the picture.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from pwtr.formats import pwtex, txp_pack

#: ``NN_WxH_FMT.png`` -- what the unpacker writes now -- or the ``.dds`` it
#: used to write, which still works and is still what the game holds.
DDS_NAME = re.compile(r"^(\d+)_(\d+)x(\d+)_(\w+)\.(dds|png)$", re.I)

FOURCC = {"DXT1": pwtex.DXT1, "DXT3": pwtex.DXT5, "DXT5": pwtex.DXT5}


class TextureError(Exception):
    pass


class Texture:
    """One DDS inside one package, as a file you can paint."""

    def __init__(self, package: "Package", path: Path, index: int,
                 width: int, height: int, fourcc: str):
        self.package = package
        self.path = path
        self.index = index
        self.width, self.height = width, height
        self.fourcc = fourcc

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def key(self) -> str:
        return f"{self.package.name}/{self.name}"

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def is_png(self) -> bool:
        return self.path.suffix.lower() == ".png"

    def bytes(self) -> bytes:
        """The file's own bytes -- DDS for a .dds, PNG for a .png."""
        return self.path.read_bytes()

    def slot_bytes(self) -> bytes | None:
        """What would go into the package: DDS, encoding the PNG if need be."""
        if not self.is_png:
            return self.bytes()
        warnings: list[str] = []
        slot = {"w": self.width, "h": self.height,
                "fcc": self.fourcc.encode("ascii")}
        return txp_pack._slot_bytes(str(self.path), slot, warnings,
                                    self.original())

    def original(self) -> bytes | None:
        """The stock bytes, straight out of the game's own package."""
        return self.package.original(self.index)

    def edited(self) -> bool:
        """Whether this differs from the game's own texture.

        For a PNG that has to be a comparison of *pixels*: re-encoding is
        lossy and never reproduces the original bytes, so comparing those
        would call every texture edited the moment it was unpacked.
        """
        stock = self.original()
        if stock is None:
            return False
        if not self.is_png:
            return self.bytes() != stock
        mine, theirs = self.image(), self.image(stock)
        if mine is None or theirs is None:
            return True
        return not np.array_equal(np.asarray(mine), np.asarray(theirs))

    def complaint(self) -> str | None:
        """Why this file would be refused, or None if it is fine.

        Checked against the *header the file itself carries*, not against its
        name, because renaming a file is easier than resizing one and a name
        that still says 512x512 is exactly how a wrong size gets this far.
        """
        if self.is_png:
            picture = self.image()
            if picture is None:
                return "will not open as a picture any more"
            height, width = picture.shape[0], picture.shape[1]
            if (width, height) != (self.width, self.height):
                return (f"is {width}x{height}; the game addresses this "
                        f"texture by offset, so it has to stay "
                        f"{self.width}x{self.height}")
            encoded = self.slot_bytes()
            if encoded is None:
                return f"cannot be encoded back to {self.fourcc}"
            stock = self.original()
            if stock is not None and len(encoded) != len(stock):
                return (f"encodes to {len(encoded)} bytes; the slot holds "
                        f"{len(stock)}")
            return None
        data = self.bytes()
        if data[:4] != b"DDS ":
            return "not a DDS file any more"
        height, width = struct.unpack_from("<2I", data, 12)
        fourcc = data[84:88].decode("ascii", "replace")
        if (width, height) != (self.width, self.height):
            return (f"is {width}x{height}; the game addresses this texture by "
                    f"offset, so it has to stay {self.width}x{self.height}")
        if fourcc != self.fourcc:
            return f"is {fourcc}; the original is {self.fourcc}"
        stock = self.original()
        if stock is not None and len(data) != len(stock):
            return (f"is {len(data)} bytes; the original is {len(stock)}, and "
                    f"the package has no room for the difference")
        return None

    def image(self, data: bytes | None = None):
        """Decoded to RGBA, for showing.  ``None`` if it cannot be decoded."""
        if data is None and self.is_png:
            try:
                return np.asarray(Image.open(self.path).convert("RGBA"))
            except Exception:
                return None
        data = self.bytes() if data is None else data
        if len(data) < 128 or data[:4] != b"DDS ":
            return None
        fmt = FOURCC.get(data[84:88].decode("ascii", "replace"))
        if fmt is None:
            return None
        height, width = struct.unpack_from("<2I", data, 12)
        count = pwtex.level_size(width, height, fmt)
        try:
            return pwtex.decode_bc(data[128:128 + count], width, height, fmt)
        except Exception:
            return None


class Package:
    """One ``.txp`` from the game, and the folder of DDS files edited from it."""

    def __init__(self, folder: Path, source: Path):
        self.folder = Path(folder)
        self.source = Path(source)
        self._stock: list | None = None
        self._raw: bytes | None = None

        self.textures: list[Texture] = []
        # A .dds beside a .png wins: it is the untouched original, and
        # re-encoding it would cost a generation of block compression for
        # nothing.
        found: dict[int, Texture] = {}
        for path in sorted(self.folder.glob("*.*")):
            match = DDS_NAME.match(path.name)
            if not match or path.name.lower().endswith("__ink.png"):
                continue
            index = int(match.group(1))
            if index in found and path.suffix.lower() == ".png":
                continue
            found[index] = Texture(
                self, path, index, int(match.group(2)),
                int(match.group(3)), match.group(4).upper())
        self.textures = [found[k] for k in sorted(found)]

    @property
    def name(self) -> str:
        return self.folder.name

    def _load(self) -> list:
        """The game's own package, decrypted once and kept."""
        if self._stock is None:
            self._raw = txp_pack.decrypt_txp(str(self.source))
            self._stock = txp_pack.textures(self._raw)
        return self._stock

    def original(self, index: int) -> bytes | None:
        try:
            records = self._load()
        except OSError:
            return None
        for record in records:
            if record["index"] == index:
                return self._raw[record["off"]:record["off"] + record["size"]]
        return None

    def edits(self) -> list[Texture]:
        return [t for t in self.textures if t.edited()]

    def pack(self, out_path: Path) -> tuple[Path, int, list[str]]:
        """Splice the edited DDS files back and write a game-ready package.

        Nothing is rebuilt: each texture is overwritten in place over its own
        byte range, so the file length never changes and no offset in the
        master table moves.  The result is re-encrypted under the *source*
        name, which is the name the game's cipher is keyed by.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        written, replaced, warnings = txp_pack.pack(
            str(self.source), str(self.folder), str(out_path))
        return Path(written), replaced, list(warnings)


class TextureSet:
    """Every unpacked package under one folder."""

    def __init__(self, root: str | Path, game: str | Path):
        self.root, self.game = Path(root), Path(game)
        if not self.root.is_dir():
            raise TextureError(f"{self.root} is not a folder")

        sources = {p.stem: p for p in self.game.rglob("*.txp")}
        self.packages: list[Package] = []
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            source = sources.get(folder.name)
            if source is None:
                continue          # a folder that is not one of the game's
            package = Package(folder, source)
            if package.textures:
                self.packages.append(package)

        if not self.packages:
            raise TextureError(
                f"No unpacked packages under {self.root}.\n\n"
                f"Each one should be a folder named after its .txp -- "
                f"008299c5, say -- holding NN_WxH_FMT.png files.")

    def __iter__(self):
        for package in self.packages:
            yield from package.textures

    def all(self) -> list[Texture]:
        return list(self)

    def edits(self) -> list[Texture]:
        return [t for t in self if t.edited()]

    def complaints(self) -> list[tuple[Texture, str]]:
        out = []
        for texture in self.edits():
            problem = texture.complaint()
            if problem:
                out.append((texture, problem))
        return out

    def build(self, out_root: str | Path, note=None) -> dict:
        """Write a patched copy of every package that has an edit in it.

        Packages land under ``out_root`` at the path they occupy in the game,
        so installing is a copy and nothing has to remember where a package
        came from.
        """
        out_root = Path(out_root)
        packed, edited, spliced, warnings = 0, 0, 0, []
        for package in self.packages:
            edits = package.edits()
            if not edits:
                continue
            relative = package.source.relative_to(self.game)
            if note:
                note(f"{relative}: {len(edits)} texture(s)")
            _out, count, complaints = package.pack(out_root / relative)
            packed += 1
            edited += len(edits)
            spliced += count
            warnings += [f"{package.name}: {c}" for c in complaints]
        # `spliced` counts every DDS written back, which is all of them: the
        # packer overwrites each texture from the folder it is given, and the
        # ones nobody touched are written back as the bytes they already were.
        # `edited` is the number that actually differ from the game.
        return {"packages": packed, "edited": edited, "spliced": spliced,
                "warnings": warnings, "out": out_root}
