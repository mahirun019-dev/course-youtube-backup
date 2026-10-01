import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
_temp = tempfile.TemporaryDirectory(prefix="course-backup-test-")
os.environ["COURSE_BACKUP_DATA"] = _temp.name
