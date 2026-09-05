"""Exporte les releves pour le tableau de bord et les pousse sur GitHub.

    python scripts/publier.py            # export + commit + push
    python scripts/publier.py --no-push  # export + commit local seulement
    python scripts/publier.py --export   # export seulement, rien dans git

La boucle le fait seule apres chaque cycle ; ce script sert a forcer une
mise a jour ou a verifier l'export a la main.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ROOT, load_config  # noqa: E402
from src.exchange import Exchange  # noqa: E402
from src.export import export_all  # noqa: E402
from src.publish import publish  # noqa: E402
from src.storage import Storage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--export", action="store_true", help="export seulement")
    args = ap.parse_args()

    cfg = load_config()
    st = Storage(cfg.get("storage.db_path"), None)
    prices = Exchange(cfg, trading=False).fetch_prices(cfg.symbols, strict=False)
    out_dir = ROOT / str(cfg.get("site.dir", "docs/data"))
    files = export_all(st, cfg, prices, out_dir)
    st.close()
    for f in files:
        print(f"  {f.relative_to(ROOT)}  {f.stat().st_size:>7} octets")
    if args.export:
        return 0
    ok, msg = publish(ROOT, [str(out_dir.relative_to(ROOT))], "releve manuel", push=not args.no_push)
    print(("OK  " if ok else "KO  ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
