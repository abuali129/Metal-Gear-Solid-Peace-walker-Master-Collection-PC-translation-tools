r"""The English words in the three burned-in scenes.

Typed out from the video, because they exist nowhere else: not in a container,
not in an ``.olang`` table, not in the PS3 or PSP script dumps.  Checked
against the picture line by line, with the line breaks the video itself uses,
so a translator can see how much room a line has before it wraps.

Keyed by the name the game knows each movie by.  Two scenes are the same
chronology -- ``004bc518`` is the one played after the true ending and carries
two entries the other does not.
"""
from __future__ import annotations

#: Shared by both chronology rolls, in order.
_CHRONOLOGY = [
    "November 24th, 1974\n"
    "The U.S. and the Soviet Union agree on a framework for a SALT II\n"
    "(Strategic Arms Limitations Talks) treaty. The treaty limits the number\n"
    "of strategic nuclear delivery vehicles on each side to 2,400.\n"
    "Among that number, MIRVs are restricted to 1,320.",

    "December 1974\n"
    "Former Prime Minister of Japan Eisaku Sato receives the Nobel Peace\n"
    "Prize for laying forth Japan's Three Non-Nuclear Principles.",

    "December 27th, 1974\n"
    "Nicaragua. The FSLN (Sandinista National Liberation Front) takes\n"
    "hostages at a party at the house of a minister in the Somoza\n"
    "government. They secure the release of 14 political prisoners and\n"
    "achieve recognition as a representative of anti-Somoza sentiment.",

    "March 1975\n"
    "Former President of Costa Rica Jose Figueres Ferrer admits to\n"
    "collaborating with the CIA.",

    "April 30th, 1975\n"
    "The Ho Chi Minh Campaign and the fall of Saigon reunites North and\n"
    "South Vietnam.",

    "July 17th, 1975\n"
    "Soyuz 19 and Apollo 18 dock in orbit.\n"
    "The U.S. and Soviet Union complete their first peaceful joint activity in\n"
    "space.",

    "July 19th, 1979\n"
    "The FSLN overthrows Somoza in an armed uprising, helping realize\n"
    "the Nicaraguan Revolution. Somoza is assassinated the following\n"
    "year.",

    "December 24th, 1979\n"
    "The Soviet Union invades Afghanistan.",

    "1980\n"
    "Dr. Emmerich (Huey) becomes a father. He names his son \"Hal.\"\n"
    "The United Nations establishes the University for Peace in Costa Rica.",

    "November 17th, 1983\n"
    "Costa Rican President Luis Alberto Monge refuses to allow the\n"
    "construction of U.S. military bases in his country, declaring permanent\n"
    "neutrality in Costa Rica.",

    "March 1985\n"
    "Gorbachev comes to power in the Soviet Union.\n"
    "Another period of Detente begins as the Cold War draws to an end.",

    "November 9th, 1989\n"
    "The Berlin Wall falls. Germany is reunified the following year.",

    "1994\n"
    "The Costa Rican government persuades Panama to abolish its\n"
    "military.",
]

#: ``stem -> (what it is, [event, ...])``, each event a list of blocks.
SCENES = {
    "004bc514": (
        "Historical chronology, part one",
        [
            list(_CHRONOLOGY),
            ["To be continued in Chapter 5:\nOuter Heaven"],
        ],
    ),
    "004bc518": (
        "Historical chronology, part two",
        [
            _CHRONOLOGY + [
                "1995\n"
                "Big Boss triggers the Outer Heaven Uprising.",
            ],
            ["2005\n"
             "Miller is found assassinated in his home\n"
             "by an unknown assailant."],
        ],
    ),
    "00568c22": (
        "Kant epigraph",
        [
            [
                "In time, all standing armies must be totally abolished.",
                "- Immanuel Kant, \"Perpetual Peace\" Chapter 1",
            ],
        ],
    ),
}


#: The events that scroll, as ``(stem, event index)``.  Only these are timed to
#: how many lines they carry -- tested in game, equal line counts per field is
#: what keeps a roll in sync.  The splash cards and the Kant card are placed,
#: not scrolled, so a different line count there moves nothing out of step.
ROLLS = {("004bc514", 0), ("004bc518", 0)}


def is_roll(key: str) -> bool:
    """Whether an entry key ``mov/<stem>/<event>/<block>`` is part of a roll."""
    parts = key.split("/")
    return len(parts) == 4 and (parts[1], int(parts[2])) in ROLLS


def blocks(stem: str):
    """``[(event index, block index, English)]`` for one movie."""
    out = []
    for event, items in enumerate(SCENES[stem][1]):
        for block, text in enumerate(items):
            out.append((event, block, text))
    return out
