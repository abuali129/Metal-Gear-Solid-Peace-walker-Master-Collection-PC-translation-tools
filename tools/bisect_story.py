"""Narrow a story hang to one block, one play test at a time.

Keeps the candidate set in a file so each round is a single command.  The
build translates the first half of the candidates and leaves the rest, plus
everything already cleared, in English -- so exactly one variable moves.
"""
import subprocess
import sys
from pathlib import Path

STATE = Path('_bisect.txt')
MENU = ("002FF,00455,004E6,00001,0007F,001FC,003CE,195FE,00258,0038C,00428,"
        "00478,0051D,00539,00569,00581,005A5,005DB,00607,0061B,0063B,0064B,"
        "00663,006B7,006CF,006FF,0071B,00729,00745,00753,0076F,0081F,00837,"
        "0088B,0089F,008AA,008BA,00910,00930,00940,00960,0099A,009BA,009CA,"
        "009E3,00A14,00A30,00A3E,00A4F,00A58,00A70,00B0C,00B3B,00B52,00B6A,"
        "00B9A,00BB2,00BE2,00BF6,00C25,00C40,00C4D,00C6D,00C7D,00C8D,00C95,"
        "00CA9,00CB3,00CC3,00CCB,00CDB,112B9").split(',')

verdict = sys.argv[1] if len(sys.argv) > 1 else 'start'
if verdict == 'start' or not STATE.exists():
    candidates = [b for b in MENU if b not in ('002FF', '00455', '004E6')]
else:
    tried, rest = STATE.read_text().split('|')
    tried, rest = tried.split(','), [x for x in rest.split(',') if x]
    # hangs -> it is in the half we just switched on; works -> the other half
    candidates = tried if verdict == 'hangs' else rest

half = max(1, len(candidates) // 2)
on, off = candidates[:half], candidates[half:]
STATE.write_text(','.join(on) + '|' + ','.join(off))
skip = [b for b in MENU if b not in on]
print('%d candidate(s); translating %d, holding %d'
      % (len(candidates), len(on), len(off)))
if len(candidates) == 1:
    print('*** narrowed to block %s ***' % candidates[0])
Path('_skip.txt').write_text(','.join(skip))
