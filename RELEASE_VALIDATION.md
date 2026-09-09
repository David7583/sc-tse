# Release validation / 发布验收

Date: 2026-09-09

- Windows; Python 3.12.7; Chrome. Fresh virtual environment installed from requirements-lock.txt; pip check passed.
- Fresh npm installation of pinned Playwright; package-lock.json included.
- All 27 core modules import from the independent release workspace, with third-party modules from its fresh environment.
- Python AST and frontend JavaScript syntax checks passed.
- 8 API tests passed, including baseline equivalence to the original active chain, edit success, hard-constraint failure, input safety and unchanged original input.
- 12 browser checks passed, including full runs, coordinate edits, actual/required distance display, recovery, invalid input, transport failure, and layout.
- Core Python and frontend Python/JavaScript files match source hashes. The CMD launcher only changes LF to CRLF; normalized content is identical, and its actual dry-run passed. Test adaptations and all hashes are recorded in SOURCE_MANIFEST.json.
- No archived or old Showcase scripts, databases, virtual environments, logs or test screenshots are included in the upload directory.
- Sensitive-pattern scan is a heuristic check, not a comprehensive security certification.
- Only the documented Integration API chain is supported; other internal generic DAG providers are not shipped.
- License selection was explicitly deferred by the project owner.

完整日志与截图保留在发布准备目录的 validation 中，不属于上传内容。
Detailed logs and screenshots remain outside the upload directory.
