"""Install a narrowly scoped router into the existing sibling backend; never rewrite other code."""

import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("backend", type=Path)
args = parser.parse_args()
backend = args.backend.resolve()
app_path = backend / "src/sales_intelligence/api/app.py"
target = backend / "src/sales_intelligence/api/warehouse.py"
template = Path(__file__).resolve().parents[1] / "integrations/backend/warehouse.py"
app = app_path.read_text()
import_line = "from sales_intelligence.api.warehouse import router as warehouse_router"
include_line = (
    '    app.include_router(warehouse_router, prefix="/api/v1", '
    "dependencies=[Depends(authenticate)])"
)
import_anchor = "from sales_intelligence.api.jobs import router as jobs_router"
include_anchor = (
    '    app.include_router(jobs_router, prefix="/api/v1", dependencies=[Depends(authenticate)])'
)
if target.exists() and target.read_text() != template.read_text():
    raise SystemExit("Existing warehouse router differs; review it before installing")
if import_anchor not in app or include_anchor not in app:
    raise SystemExit("Backend conventions changed; review the router mount before installing")
if import_line not in app:
    app = app.replace(import_anchor, import_anchor + "\n" + import_line, 1)
if include_line not in app:
    app = app.replace(include_anchor, include_anchor + "\n" + include_line, 1)
target.write_text(template.read_text())
app_path.write_text(app)
print("Installed warehouse router using existing session and authenticate dependencies")
