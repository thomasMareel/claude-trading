"""Ce qui differe d'une plateforme a l'autre, rassemble en un seul endroit.

Le reste du code ne doit connaitre AUCUN nom propre de plateforme. Tout ce
qui est specifique vit ici : le nom du parametre d'identifiant d'ordre, son
alphabet autorise, sa longueur, les secrets attendus dans .env.

Lecon apprise a la dure : l'identifiant d'ordre client etait ecrit pour
Binance ("newClientOrderId" a la pose, "origClientOrderId" a la relecture).
Ces noms n'existent nulle part ailleurs. Sur une autre plateforme le
parametre etait simplement ignore, donc la protection contre les ordres en
double apres coupure reseau devenait silencieusement inoperante, exactement
la ou elle compte le plus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Venue:
    """Le contrat d'une plateforme, du point de vue de notre code."""

    id: str                          # identifiant ccxt
    nom: str                         # nom lisible
    hote: str                        # domaine reellement interroge
    env_prefix: str                  # prefixe des variables dans .env
    credentials: tuple[str, ...]     # champs ccxt a renseigner
    cid_pose: str                    # nom du parametre a la CREATION de l'ordre
    cid_relecture: str               # nom du parametre a la RELECTURE de l'ordre
    cid_alphabet: str                # "alnum" ou "alnum_tiret"
    cid_max: int                     # longueur maximale
    post_only: bool                  # ordres strictement maker disponibles
    frais_lisibles: bool             # fetch_order_trades rend les frais reels
    note: str = ""

    def env_names(self) -> dict[str, str]:
        """Nom de la variable .env pour chaque identifiant ccxt requis."""
        suffixes = {"apiKey": "API_KEY", "secret": "API_SECRET", "password": "API_PASSPHRASE"}
        return {c: f"{self.env_prefix}_{suffixes[c]}" for c in self.credentials}

    def client_order_id(self, tag: str) -> str:
        """Identifiant deterministe accepte par CETTE plateforme.

        Le meme tag redonne toujours le meme identifiant : si le reseau coupe
        apres l'envoi, on RETROUVE l'ordre au lieu d'en creer un second.
        """
        if self.cid_alphabet == "alnum":
            cleaned = re.sub(r"[^A-Za-z0-9]", "", tag)
        else:
            cleaned = re.sub(r"[^A-Za-z0-9_-]", "", tag)
        if not cleaned:
            raise ValueError(f"tag {tag!r} vide une fois nettoye pour {self.nom}")
        # On tronque par la GAUCHE : la fin d'un tag porte le symbole et le sens,
        # qui sont ce qui distingue deux ordres du meme cycle.
        return cleaned[-self.cid_max:]


VENUES: dict[str, Venue] = {
    "myokx": Venue(
        id="myokx", nom="OKX Europe", hote="eea.okx.com", env_prefix="OKX",
        credentials=("apiKey", "secret", "password"),
        cid_pose="clientOrderId", cid_relecture="clientOrderId",
        cid_alphabet="alnum", cid_max=32,
        post_only=True, frais_lisibles=True,
        note="Entite maltaise agreee MiCA. Exige une phrase secrete en plus de la cle "
             "et du secret. Ne PAS utiliser l'identifiant 'okx', qui pointe sur le "
             "domaine mondial et renvoie l'erreur 50119 pour un compte europeen.",
    ),
    "kraken": Venue(
        id="kraken", nom="Kraken", hote="api.kraken.com", env_prefix="KRAKEN",
        credentials=("apiKey", "secret"),
        cid_pose="clientOrderId", cid_relecture="clientOrderId",
        cid_alphabet="alnum_tiret", cid_max=18,
        post_only=True, frais_lisibles=True,
        note="Entite irlandaise agreee MiCA. Identifiant d'ordre limite a 18 caracteres.",
    ),
    "bitvavo": Venue(
        id="bitvavo", nom="Bitvavo", hote="api.bitvavo.com", env_prefix="BITVAVO",
        credentials=("apiKey", "secret"),
        cid_pose="clientOrderId", cid_relecture="clientOrderId",
        cid_alphabet="alnum_tiret", cid_max=36,
        post_only=True, frais_lisibles=False,
        note="Entite neerlandaise agreee MiCA. ATTENTION : ccxt ne rend jamais "
             "l'identifiant client a la relecture, et exige un operatorId a chaque "
             "ordre. La protection anti-doublon y est structurellement plus faible.",
    ),
    "binance": Venue(
        id="binance", nom="Binance", hote="api.binance.com", env_prefix="BINANCE",
        credentials=("apiKey", "secret"),
        cid_pose="newClientOrderId", cid_relecture="origClientOrderId",
        cid_alphabet="alnum_tiret", cid_max=36,
        post_only=True, frais_lisibles=True,
        note="SANS licence MiCA : ne sert plus les residents de l'Union europeenne "
             "depuis le 1er juillet 2026. Conserve pour les tests hors ligne seulement.",
    ),
}


def get(venue_id: str) -> Venue:
    if venue_id not in VENUES:
        raise KeyError(
            f"plateforme {venue_id!r} inconnue. Disponibles : {sorted(VENUES)}. "
            f"Pour en ajouter une, decrire son contrat dans src/venues.py."
        )
    return VENUES[venue_id]
