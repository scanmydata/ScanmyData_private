import asyncio
import json
import sys
from pathlib import Path

# Ensure workspace root is importable
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from e3.checks.aade_playwright_fetch_e1 import run as aade_run

async def main():
    username = 'ww750663u147'
    password = 'doyr1'
    year = '2025'
    out = Path('tmp_test_aade_amka.pdf')
    try:
        res = await aade_run(username, password, year, str(out), headless=True, name=None)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
