"""Rebuild only the story container, reporting what would not fit."""
import sys
sys.path.insert(0, '.')
from pwtr.project import Project

project = Project.load(sys.argv[1])
report = project.build_story(note=lambda m: print('   ' + str(m)),
                             progress=lambda m: None)
for k, v in sorted(report.items()):
    print('%-14s %s' % (k, v))
