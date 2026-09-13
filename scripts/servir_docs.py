"""Sert docs/ en local, SANS mise en cache.

    python scripts/servir_docs.py [port]

http.server ne pose aucun en-tete Cache-Control. Le navigateur applique alors sa
regle heuristique — il garde un fichier pendant un dixieme de son age — et une
feuille de style vieille de plusieurs jours reste en cache pendant des heures.
En verification visuelle c'est un piege parfait : on modifie jetons.css, on
recharge, rien ne change, et on cherche l'erreur dans le fichier qu'on vient
d'ecrire alors que le navigateur sert l'ancien.

Ce serveur repond no-store sur tout. Il ne sert qu'a regarder le site pendant
qu'on le travaille ; GitHub Pages, lui, pose ses propres en-tetes.
"""
from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


class SansCache(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        #  Une ligne par requete noierait la sortie ; seules les erreurs comptent.
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    srv = ThreadingHTTPServer(("127.0.0.1", port),
                              partial(SansCache, directory=str(DOCS)))
    print(f"docs/ sur http://127.0.0.1:{port}/  (sans cache)")
    sys.stdout.flush()
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
