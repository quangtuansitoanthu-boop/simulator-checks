import json, sys, xml.etree.ElementTree as ET
from pathlib import Path

directory, expected, destination = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
files = sorted(directory.rglob('*.xml'))
assert files, 'No Android JUnit result files'
cases = [case for file in files for case in ET.parse(file).getroot().iter('testcase')]
summary = {'tests': len(cases), 'failures': sum(x.find('failure') is not None for x in cases), 'errors': sum(x.find('error') is not None for x in cases), 'skipped': sum(x.find('skipped') is not None for x in cases), 'expected': expected, 'cases': [dict(x.attrib) for x in cases]}
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps({key: value for key, value in summary.items() if key != 'cases'}))
assert len(cases) == expected and not (summary['failures'] + summary['errors'] + summary['skipped']), 'Android test coverage/failure gate failed'
