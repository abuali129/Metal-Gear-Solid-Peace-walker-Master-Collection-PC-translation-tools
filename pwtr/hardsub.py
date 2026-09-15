r"""The three movies whose text is burned into the picture.

Most of the script is text in a container and can simply be replaced.  Three
scenes are not: the Kant epigraph and the two halves of the historical
chronology are pre-rendered video with the words already in the pixels.  The
only way to translate those is to draw over them and re-encode.

## What a project holds

    <project>/hardsub/Mov/<stem>.ass      for the 1280x720 set
    <project>/hardsub/hqMov/<stem>.ass    for the 1920x1080 set
    <project>/hardsub/source/             whatever those were made from

Two resolutions because the game picks between ``Mov`` and ``hqMov`` at
runtime and an ``.ass`` carries its own ``PlayResX``: subtitling only one set
means the other still plays in English, which looks exactly like the mod not
working.

The build writes ``output/MLG/data/<folder>/<stem>.xmx``, which the ordinary
install then copies into the game with a ``.bak`` like anything else.

## Blanking

A subtitle is drawn *over* the picture, and the English is in the picture.

The chronology scrolls, and its ``Roll`` style paints an opaque box -- but
only as wide as its own line, so the English column beside it survives.  These
movies are white text on black and nothing else, so :data:`BLANK_ALL` takes
the whole frame to black and lets the subtitle supply the entire picture.
That is also what the PS3 and PSP versions of this translation did.

The Kant card is one still line.  Blanking everything would work, but it is
the only file where the black band can be measured from the ink itself, so
:data:`BLANK_INK` paints out just the rows the English occupies.

Blanking the whole frame is not a cost: with nothing underneath to encode,
the chronology came out at 6.6 MB against a 35.7 MB original.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pwtr import movies

#: The game's two movie folders, and the resolution each holds.
FOLDERS = ("Mov", "hqMov")

BLANK_ALL = "all"      # black out the frame; the subtitle is the picture
BLANK_INK = "ink"      # black out only the rows the English sits on

#: What to do with each movie, keyed by the name the game knows it by.
SCENES = {
    "004bc514": (BLANK_ALL, "Historical chronology, part one"),
    "004bc518": (BLANK_ALL, "Historical chronology, part two"),
    "00568c22": (BLANK_INK, "Kant epigraph"),
}

#: Quality for the re-encode.  Text needs more than the near-static picture it
#: sits on would suggest -- the stock chronology runs at 4 600 kbit/s -- and
#: these are constant-quality rather than a fixed rate so a black frame costs
#: nothing and a busy one is not starved.
CRF = {720: 18, 1080: 19}


class HardsubError(Exception):
    pass


#: Stops each ffmpeg call from flashing a console window up over the app.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmpeg(cmd: list, duration: float, on_fraction) -> None:
    """Run ffmpeg, reporting how far through the movie it has got.

    ``-progress pipe:1`` makes ffmpeg write ``key=value`` lines as it goes,
    and ``out_time_us`` against the movie's length is a real fraction -- the
    difference between a bar that moves and one that sits there for a minute
    looking hung.  (``out_time_ms`` is microseconds too, despite the name.)
    """
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + list(cmd[1:])
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               creationflags=NO_WINDOW)
    for line in process.stdout:
        key, _sep, value = line.strip().partition("=")
        if key in ("out_time_us", "out_time_ms") and duration > 0:
            try:
                on_fraction(min(1.0, int(value) / 1e6 / duration))
            except ValueError:
                continue
    complaint = process.stderr.read()
    if process.wait() != 0:
        last = (complaint or "").strip().splitlines()
        raise HardsubError("ffmpeg failed: " + (last[-1] if last
                                                else "no message"))
    on_fraction(1.0)


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def subtitles(root: Path) -> dict:
    """``{(folder, stem): path}`` for every subtitle a project carries."""
    found = {}
    for folder in FOLDERS:
        for path in sorted((Path(root) / "hardsub" / folder).glob("*.ass")):
            found[(folder, path.stem)] = path
    return found


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=level,width,height:format=duration", "-of", "json",
         str(path)],
        capture_output=True, text=True, check=True, creationflags=NO_WINDOW)
    data = json.loads(out.stdout)
    info = data["streams"][0]
    info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
    return info


def stock(game: Path, folder: str, stem: str, work: Path) -> Path:
    """The movie as Konami shipped it, decrypted.

    Prefers the ``.bak``: once a translated movie has been installed the
    ``.xmx`` is the translation, and re-subtitling that would stack one burn
    on another.
    """
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{folder}_{stem}.mp4"
    if out.exists():
        return out
    here = Path(game) / "MLG" / "data" / folder
    source = here / f"{stem}.xmx.bak"
    if not source.exists():
        source = here / f"{stem}.xmx"
    if not source.exists():
        raise HardsubError(f"{folder}/{stem}.xmx is not in the game folder")
    out.write_bytes(movies.decrypt(source))
    return out


def stock_audio(game: Path, folder: str, stem: str, work: Path) -> Path | None:
    """The movie's soundtrack, decrypted, or None if it has none.

    The game keeps audio beside the video as ``<stem>.xsx`` -- Ogg Vorbis
    under the usual cipher.  The Kant card has no soundtrack of its own.
    """
    here = Path(game) / "MLG" / "data" / folder
    source = here / f"{stem}.xsx.bak"
    if not source.exists():
        source = here / f"{stem}.xsx"
    if not source.exists():
        return None
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{folder}_{stem}.ogg"
    if not out.exists():
        out.write_bytes(pwcrypt_decrypt(source))
    return out


def pwcrypt_decrypt(path: Path) -> bytes:
    return movies.decrypt(path)


def _blank(kind: str, source: Path, info: dict, work: Path) -> str:
    if kind == BLANK_ALL:
        return ("drawbox=x=0:y=0:w=%d:h=%d:color=black:t=fill"
                % (info["width"], info["height"]))
    still = work / (source.stem + ".png")
    movies.still(source, still, 4.0)
    bands = movies.ink_bands(still)
    if not bands:
        return ""
    top = max(0, min(b[0] for b in bands) - 16)
    bottom = min(info["height"], max(b[1] for b in bands) + 16)
    return ("drawbox=x=0:y=%d:w=%d:h=%d:color=black:t=fill"
            % (top, info["width"], bottom - top))


def build(root: Path, game: Path, out_root: Path,
          note=lambda _m: None, subs: dict | None = None,
          fonts_dir: Path | None = None, preview: bool = False,
          folders=FOLDERS, tick=lambda _m: None) -> dict:
    """Burn every subtitle a project carries and write the movies.

    Returns what was made and what was skipped.  A scene with no ``.ass`` is
    not an error -- a project may be translating one of the three and not the
    others -- and neither is a folder the game does not have.

    ``subs`` is ``{(folder, stem): .ass}`` to burn instead of the project's
    own files -- the ones generated from the Movies tab.  ``fonts_dir`` is
    where libass should look for a font that is not installed.

    ``preview`` writes a plain ``.mp4`` per movie into ``out_root`` for
    watching, encoded fast and never encrypted.  It must not go under the
    project's ``output``: everything there is installed into the game.
    """
    if not have_ffmpeg():
        raise HardsubError(
            "ffmpeg and ffprobe are not on PATH. They do the drawing and the "
            "re-encoding; everything else here is pure Python.")

    work = Path(root) / "hardsub-work"
    made, skipped = [], []
    chosen = subs if subs is not None else subtitles(root)
    jobs = [(key, ass) for key, ass in sorted(chosen.items())
            if key[0] in folders]
    for number, ((folder, stem), ass) in enumerate(jobs):
        def report(fraction, folder=folder, stem=stem, number=number):
            # One figure for the whole run, so the bar does not start over
            # at every movie.
            whole = (number + fraction) / len(jobs)
            tick("%s/%s: %s -- movie %d of %d   %d%%"
                 % (folder, stem, "preview" if preview else "burning",
                    number + 1, len(jobs), whole * 100))

        report(0.0)
        kind = SCENES.get(stem, (BLANK_ALL, stem))[0]
        try:
            source = stock(Path(game), folder, stem, work)
        except HardsubError as problem:
            skipped.append(f"{folder}/{stem}: {problem}")
            continue
        info = _probe(source)
        chain = [step for step in (_blank(kind, source, info, work),) if step]
        # libass reads the path itself, and ':' and '\' are how a filter
        # graph separates its own arguments.
        escape = lambda p: str(p).replace("\\", "/").replace(":", "\\:")
        burn = "subtitles='%s'" % escape(ass)
        if fonts_dir:
            burn += ":fontsdir='%s'" % escape(fonts_dir)
        chain.append(burn)

        if preview:
            # Fast and a little soft -- this is for reading the words and
            # watching them against the soundtrack, not for shipping.  The
            # game keeps a movie's audio in a separate .xsx, so it is muxed
            # back in here; the shipped .xmx stays silent as the stock one is.
            out = Path(out_root) / f"{folder}_{stem}.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            audio = stock_audio(Path(game), folder, stem, work)
            note(f"{folder}/{stem}: preview"
                 + (" with its soundtrack..." if audio
                    else " (no soundtrack)..."))
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(source)]
            if audio:
                cmd += ["-i", str(audio)]
            cmd += ["-vf", ",".join(chain), "-map", "0:v:0"]
            if audio:
                # Vorbis in MP4 plays in almost nothing; AAC plays everywhere.
                cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
            else:
                cmd += ["-an"]
            cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(out)]
            _ffmpeg(cmd, info.get("duration", 0.0), report)
            made.append(str(out))
            continue

        burned = work / f"{folder}_{stem}_ar.mp4"
        note(f"{folder}/{stem}: burning {ass.name}...")
        _ffmpeg(
            ["ffmpeg", "-v", "error", "-y", "-i", str(source),
             "-vf", ",".join(chain), "-c:v", "libx264", "-profile:v", "high",
             "-level", str(info["level"] / 10),
             "-crf", str(CRF.get(info["height"], 19)),
             "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
             str(burned)], info.get("duration", 0.0), report)

        destination = Path(out_root) / "MLG" / "data" / folder / f"{stem}.xmx"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(movies.encrypt(burned.read_bytes(),
                                               destination.name))
        made.append(str(destination.relative_to(out_root)))
        note(f"   -> {destination.name}, "
             f"{destination.stat().st_size / 1e6:.1f} MB")
    return {"made": made, "skipped": skipped}
