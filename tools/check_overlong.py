import sys
sys.path.insert(0, '.')
from pwtr.project import Project
p = Project.load(sys.argv[1])
lines = p.overlong(sys.argv[2])
print('%s: %d line(s) reported too long' % (sys.argv[2], len(lines)))
for k, v in list(lines.items())[:10]:
    print('   %r -> %s' % (str(k)[:50], v))
