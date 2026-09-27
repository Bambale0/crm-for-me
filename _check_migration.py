import sys
import traceback

_err = open("_tb.txt", "w", encoding="utf-8")
sys.stderr = _err

from alembic.config import Config
from alembic import command

cfg = Config("alembic.ini")
try:
    command.upgrade(cfg, "head", sql=True)
except BaseException:
    traceback.print_exc(file=_err)
    _err.flush()
    sys.exit(1)
