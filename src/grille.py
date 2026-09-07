"""Le moteur de grille : la strategie de descente, en logique pure.

Aucune entree-sortie, aucun reseau, aucune base. Uniquement des nombres et
des decisions. C'est ce qui permet de la rejouer sur quatre cents jours
d'historique en quelques secondes, et de la tester sans rien engager.

LA STRATEGIE, telle que l'utilisateur la pratiquait a la main :
  1. On choisit un prix de reference et un budget.
  2. On pose une echelle d'ordres d'achat SOUS ce prix, avec des mises
     CROISSANTES : les grosses arrivent en bas.
  3. Chaque palier touche est achete. Le prix de revient moyen descend
     alors beaucoup plus vite que le marche, justement parce que les
     grosses mises sont en bas.
  4. On revend TOUT le lot d'un coup des que le prix repasse au-dessus du
     prix de revient moyen plus l'objectif. Il n'est donc pas necessaire
     que le cours retrouve le premier achat : un simple rebond suffit.
  5. Une fois vendu, on recommence a partir du prix du moment.

CE QUE LE MOTEUR AJOUTE AU TABLEAU D'ORIGINE :
  - des paliers en POURCENTAGE, valables a n'importe quel niveau de cours,
    la ou des pas fixes finissaient par produire des prix negatifs ;
  - les frais dans l'objectif : 2 % vise sont 2 % NETS dans la poche ;
  - une regle d'abandon ecrite d'avance, sous le dernier palier ;
  - des ordres LIMITES, donc au tarif maker et sans glissement de prix.

CE QU'IL NE CORRIGE PAS, et qu'aucun reglage ne corrigera : dans une baisse
durable, la grille accumule et ne vend jamais. C'est son mode de defaillance
structurel. Seuls le budget et la regle d'abandon le bornent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


class GrilleError(ValueError):
    pass


@dataclass(frozen=True)
class Reglages:
    """Les parametres d'une descente. Immuables une fois la descente ouverte."""

    profondeur: float = 0.15        # de combien l'echelle descend, en fraction
    paliers: int = 10               # nombre de barreaux
    ratio: float = 1.8              # progression des mises d'un barreau au suivant
    depart_sous: float = 0.0        # premier barreau, sous le prix de reference
    objectif_net: float = 0.02      # gain vise, NET de frais
    frais: float = 0.001            # taux maker, par ordre
    abandon_sous: float = 0.15      # sous le dernier barreau, on n'ajoute plus rien
    mise_min: float = 0.0           # taille minimale d'un ordre, en quote
    suivre_hausse: bool = False     # remonter la reference quand le marche monte a vide
    reancrage_min: float = 0.0      # hausse minimale avant de deplacer la reference
    vente_meme_bougie: bool = True  # autoriser l'aller-retour dans la meme bougie
    #  Une plateforme REFUSE un ordre trop petit, elle ne l'agrandit pas : c'est
    #  exactement ce que fait la couche de risque du systeme reel (src/risk.py,
    #  floor = max(min_order_value, min_notional)). Avec une progression forte,
    #  les premiers barreaux valent quelques centimes et ne seraient donc jamais
    #  poses. Laisser mise_min a zero fait tourner la strategie telle qu'elle est
    #  sur le papier ; la porter au plancher reel dit ce qui aurait ete executable.

    def __post_init__(self) -> None:
        if not 0 < self.profondeur < 1:
            raise GrilleError(f"profondeur doit etre dans ]0, 1[, trouve {self.profondeur}")
        if self.paliers < 2:
            raise GrilleError(f"il faut au moins 2 paliers, trouve {self.paliers}")
        if self.ratio < 1:
            raise GrilleError(f"ratio doit etre >= 1 (mises croissantes), trouve {self.ratio}")
        if not 0 < self.objectif_net < 1:
            raise GrilleError(f"objectif_net doit etre dans ]0, 1[, trouve {self.objectif_net}")
        if not 0 <= self.frais < 0.05:
            raise GrilleError(f"frais invraisemblables : {self.frais}")
        if self.objectif_net <= 2 * self.frais:
            raise GrilleError(
                f"objectif_net {self.objectif_net:.2%} ne couvre pas l'aller-retour "
                f"de frais ({2 * self.frais:.2%}) : la descente perdrait a chaque cycle"
            )
        if self.depart_sous < 0 or self.depart_sous + self.profondeur >= 1:
            raise GrilleError("depart_sous + profondeur doit rester sous 1")
        if self.mise_min < 0:
            raise GrilleError(f"mise_min doit etre >= 0, trouve {self.mise_min}")
        if not 0 <= self.reancrage_min < 1:
            raise GrilleError(f"reancrage_min doit etre dans [0, 1[, trouve {self.reancrage_min}")

    def echelle(self, reference: float, budget: float) -> list[tuple[float, float]]:
        """Les barreaux : (prix cible, mise en euros), du haut vers le bas.

        Les mises suivent une progression geometrique de raison `ratio` et
        somment exactement au budget.
        """
        if reference <= 0 or budget <= 0:
            raise GrilleError("reference et budget doivent etre > 0")
        pas = self.profondeur / (self.paliers - 1)
        poids = [self.ratio ** i for i in range(self.paliers)]
        total = sum(poids)
        return [
            (reference * (1 - self.depart_sous - pas * i), budget * w / total)
            for i, w in enumerate(poids)
        ]


@dataclass
class Descente:
    """Une descente en cours : ce qui a ete achete, et ce qu'on attend."""

    symbole: str
    reference: float
    budget: float
    reglages: Reglages
    ouverte_le: int | None = None                # horodatage ms du premier achat
    #  None, pas 0 : un horodatage a zero est un instant valide, et le tester
    #  avec `if not ouverte_le` le confondait avec l'absence d'achat.
    remplis: list[int] = field(default_factory=list)   # indices des barreaux achetes
    cumul_euros: float = 0.0                     # euros REELLEMENT sortis, frais inclus
    cumul_unites: float = 0.0                    # unites NETTES detenues
    abandonnee: bool = False

    # ---------------------------------------------------------------- vues
    @property
    def echelle(self) -> list[tuple[float, float]]:
        return self.reglages.echelle(self.reference, self.budget)

    @property
    def engagee(self) -> bool:
        return bool(self.remplis)

    @property
    def prix_revient(self) -> float:
        """Ce que coute une unite, tout compris. Zero si rien n'est achete."""
        return self.cumul_euros / self.cumul_unites if self.cumul_unites else 0.0

    @property
    def prix_sortie(self) -> float:
        """Le prix auquel revendre TOUT le lot pour empocher l'objectif NET.

        On veut : unites x prix x (1 - frais) = cumul_euros x (1 + objectif).
        D'ou prix = revient x (1 + objectif) / (1 - frais).
        """
        if not self.cumul_unites:
            return 0.0
        r = self.reglages
        return self.prix_revient * (1 + r.objectif_net) / (1 - r.frais)

    @property
    def dernier_palier(self) -> float:
        return self.echelle[-1][0]

    @property
    def seuil_abandon(self) -> float:
        return self.dernier_palier * (1 - self.reglages.abandon_sous)

    @property
    def barreaux_morts(self) -> list[int]:
        """Les barreaux dont la mise est sous le minimum de la plateforme.

        Ils ne sont jamais poses au carnet. Leur budget reste en caisse : on ne
        le redistribue pas sur les barreaux valides, sans quoi on modifierait en
        douce l'echelle que l'on pretend tester.
        """
        m = self.reglages.mise_min
        return [i for i, (_, e) in enumerate(self.echelle) if e < m] if m else []

    def barreaux_a_poser(self) -> list[tuple[int, float, float]]:
        """Les ordres d'achat a laisser au carnet : (indice, prix, euros)."""
        if self.abandonnee:
            return []
        m = self.reglages.mise_min
        return [(i, p, e) for i, (p, e) in enumerate(self.echelle)
                if i not in self.remplis and e >= m]

    # ---------------------------------------------------------------- mutations
    def acheter(self, indice: int, prix: float, ts: int = 0) -> tuple[float, float]:
        """Un barreau est touche. Retourne (euros sortis, unites obtenues).

        Les frais sont preleves sur l'actif recu : on obtient donc un peu
        moins d'unites que le rapport euros/prix. Le book reflete ce que
        l'on detient VRAIMENT, sinon la revente echouerait pour solde
        insuffisant."""
        if indice in self.remplis:
            raise GrilleError(f"barreau {indice} deja rempli")
        euros = self.echelle[indice][1]
        unites = euros / prix * (1 - self.reglages.frais)
        self.remplis.append(indice)
        self.cumul_euros += euros
        self.cumul_unites += unites
        if self.ouverte_le is None:
            self.ouverte_le = ts
        return euros, unites

    def vendre(self, prix: float) -> dict[str, float]:
        """Revend tout le lot. Retourne le detail du cycle."""
        if not self.cumul_unites:
            raise GrilleError("rien a vendre")
        brut = self.cumul_unites * prix
        recu = brut * (1 - self.reglages.frais)
        gain = recu - self.cumul_euros
        detail = {
            "unites": self.cumul_unites, "prix": prix, "brut": brut, "recu": recu,
            "investi": self.cumul_euros, "gain": gain,
            "gain_pct": gain / self.cumul_euros if self.cumul_euros else 0.0,
            "paliers": len(self.remplis), "prix_revient": self.prix_revient,
        }
        self.remplis, self.cumul_euros, self.cumul_unites, self.ouverte_le = [], 0.0, 0.0, None
        return detail

    def valeur(self, prix: float) -> float:
        """Valeur de liquidation du lot detenu, frais de sortie deduits."""
        return self.cumul_unites * prix * (1 - self.reglages.frais)


# ====================================================================== rejeu
@dataclass
class Cycle:
    """Un aller-retour complet, pour le journal du backtest."""
    symbole: str
    ouvert_le: int
    ferme_le: int
    paliers: int
    investi: float
    recu: float
    gain: float
    gain_pct: float
    prix_revient: float
    prix_sortie: float
    heures: float


@dataclass
class Evenement:
    """Un achat, une vente ou un abandon, situe sur la courbe de prix.

    Le resume chiffre dit combien de cycles ont eu lieu ; il ne dit pas OU ils
    ont eu lieu. Ce journal permet de poser chaque decision sur le graphique du
    prix, seul moyen de voir la difference entre un cycle boucle en trois heures
    et un cycle qui traine deux cents jours.
    """

    ts: int
    genre: str          # "achat" | "vente" | "abandon"
    prix: float
    palier: int         # indice du barreau achete ; -1 pour une vente ou un abandon
    euros: float        # engage a l'achat, recu a la vente, 0 a l'abandon
    revient: float      # prix de revient moyen du lot au moment de l'evenement
    gain: float         # non nul seulement a la vente


def chemin_bougie(o: float, h: float, l: float, c: float) -> tuple[float, float]:
    """Dans quel ordre le prix a-t-il probablement parcouru la bougie ?

    On ne connait que l'ouverture, le haut, le bas et la cloture, pas le
    chemin. Convention retenue, la plus courante et la plus defendable :
    une bougie haussiere est allee d'abord au plus BAS puis au plus HAUT,
    une bougie baissiere l'inverse. Pour une grille, cette convention est
    la moins flatteuse : sur une bougie baissiere elle fait toucher le haut
    avant le bas, donc elle refuse une vente qui aurait pu avoir lieu apres
    un achat plus bas dans la meme heure.
    """
    return (l, h) if c >= o else (h, l)


def rejouer(
    symbole: str, bougies: list[tuple[int, float, float, float, float]],
    reglages: Reglages, budget: float, *, reference: float | None = None,
    trace: bool = True,
) -> dict:
    """Rejoue la strategie sur des bougies (ts, open, high, low, close).

    Regles d'execution, volontairement prudentes :
      - un ordre limite d'achat est touche si le BAS de la bougie atteint
        son prix ; il est execute a son prix exactement, sans glissement,
        puisque c'est un ordre limite (avantage reel du maker) ;
      - la vente se declenche si le HAUT atteint le prix de sortie ;
      - l'ordre des evenements dans la bougie suit chemin_bougie ;
      - sous le seuil d'abandon, plus aucun achat, mais la vente reste
        possible : on attend le rebond sans jamais moyenner davantage.

    trace=False n'enregistre plus rien par bougie. Sur de l'historique a la
    minute, les series par bougie pesent six cent mille lignes par paire et par
    reglage, ce qui interdit tout balayage. Les agregats, eux, sont calcules au
    fil de l'eau dans les deux modes : resume() rend donc exactement les memes
    nombres avec ou sans trace, ce qu'un test verifie.
    """
    if not bougies:
        raise GrilleError("aucune bougie a rejouer")
    ref = reference if reference is not None else bougies[0][1]
    d = Descente(symbole, ref, budget, reglages)
    cash = budget
    cycles: list[Cycle] = []
    equity: list[tuple[int, float]] = []
    deploiement: list[tuple[int, float, float, int, bool]] = []
    journal: list[Evenement] = []
    suivi: list[tuple[int, float, float, float, float]] = []
    abandons = 0
    pic, creux, somme_eng, heures_eng = float("-inf"), 0.0, 0.0, 0

    for ts, o, h, l, c in bougies:
        premier, second = chemin_bougie(o, h, l, c)
        for extreme in (premier, second):
            monte = extreme == h
            if monte:
                #  Un aller-retour dans la meme heure n'est pas observable dans une
                #  bougie horaire : il suppose que le bas a ete visite avant le haut
                #  ET que l'ordre limite de vente a ete servi au sommet de la meche.
                #  Plus l'objectif est petit, plus ces cycles sont nombreux et plus
                #  le resultat repose sur ce pari. Les interdire donne la borne basse.
                trop_tot = (not reglages.vente_meme_bougie
                            and d.ouverte_le is not None and ts <= d.ouverte_le)
                if d.cumul_unites and h >= d.prix_sortie and not trop_tot:
                    px = d.prix_sortie
                    # lire l'ouverture AVANT de vendre : vendre() remet la descente a zero
                    ouvert = ts if d.ouverte_le is None else d.ouverte_le
                    det = d.vendre(px)
                    cash += det["recu"]
                    cycles.append(Cycle(
                        symbole, ouvert, ts, det["paliers"], det["investi"],
                        det["recu"], det["gain"], det["gain_pct"], det["prix_revient"], px,
                        (ts - ouvert) / 3_600_000,
                    ))
                    if trace:
                        journal.append(Evenement(ts, "vente", px, -1, det["recu"],
                                                 det["prix_revient"], det["gain"]))
                    d = Descente(symbole, c, budget, reglages)   # on repart du prix du moment
                    d.abandonnee = False
            else:
                if l <= d.seuil_abandon and not d.abandonnee:
                    d.abandonnee = True
                    abandons += 1
                    if trace:
                        journal.append(Evenement(ts, "abandon", d.seuil_abandon, -1, 0.0,
                                                 d.prix_revient, 0.0))
                for i, prix, euros in d.barreaux_a_poser():
                    if l <= prix and cash >= euros - 1e-9:
                        d.acheter(i, prix, ts)
                        cash -= euros
                        if trace:
                            journal.append(Evenement(ts, "achat", prix, i, euros,
                                                     d.prix_revient, 0.0))
        #  Sans cette ligne, l'echelle reste plantee la ou la derniere vente l'a
        #  laissee. Si le marche s'eleve de 20 % sans que rien ne soit achete, le
        #  premier barreau est 20 % sous le cours et n'est jamais rejoint : la
        #  grille s'endort. Un operateur humain, lui, repose son echelle sous le
        #  prix du moment. On ne suit qu'a la HAUSSE et qu'a vide : suivre a la
        #  baisse reviendrait a courir apres le marche en annulant ses achats, et
        #  suivre en portant un lot deplacerait l'echelle sous ses propres achats.
        #  On lit la cloture, jamais le haut de la bougie : la bougie doit etre
        #  fermee au-dessus de l'ancienne reference pour deplacer l'echelle.
        #  reancrage_min evite de courir apres le bruit : sur des bougies a la
        #  minute, deplacer l'echelle a chaque tick haussier la collerait au prix
        #  et ne laisserait jamais un creux se former.
        if (reglages.suivre_hausse and not d.engagee
                and c > d.reference * (1 + reglages.reancrage_min)):
            d = Descente(symbole, c, budget, reglages)

        valeur = cash + d.valeur(c)
        if valeur > pic:
            pic = valeur
        if pic > 0:
            creux = min(creux, valeur / pic - 1)
        somme_eng += d.cumul_euros
        if d.cumul_euros > 1e-9:
            heures_eng += 1
        if trace:
            equity.append((ts, valeur))
            # combien d'argent travaille reellement, et combien dort : c'est ce qui
            # explique un rendement modeste sur le budget alors que chaque cycle
            # rapporte l'objectif plein sur la somme engagee
            deploiement.append((ts, cash, d.cumul_euros, len(d.remplis), d.abandonnee))
            # de quoi redessiner l'echelle et la cible a n'importe quelle heure : la
            # reference suffit a reconstruire les barreaux, puisqu'ils s'en deduisent
            suivi.append((ts, d.reference, d.prix_revient, d.prix_sortie, d.seuil_abandon))

    fin = bougies[-1][4]
    return {
        "symbole": symbole,
        "cycles": cycles,
        "equity": equity,
        "deploiement": deploiement,
        "journal": journal,
        "suivi": suivi,
        "cash_final": cash,
        "lot_restant": d.cumul_unites,
        "valeur_lot": d.valeur(fin),
        "investi_bloque": d.cumul_euros,
        "equity_finale": cash + d.valeur(fin),
        "abandons": abandons,
        "descente_en_cours": d,
        "budget": budget,
        # calcules au fil de l'eau, donc disponibles meme sans trace
        "agregats": {
            "drawdown_max": creux,
            "engage_moyen": somme_eng / len(bougies) / budget if budget else 0.0,
            "part_temps_engage": heures_eng / len(bougies),
            "bougies": len(bougies),
            "t_debut": bougies[0][0],
            "t_fin": bougies[-1][0],
        },
    }


def resume(r: dict) -> dict:
    """Les chiffres qui decident si un reglage vaut mieux qu'un autre."""
    cy = r["cycles"]
    budget = r["budget"]
    ag = r["agregats"]
    dd = ag["drawdown_max"]
    gains = [c.gain for c in cy]
    duree_h = (ag["t_fin"] - ag["t_debut"]) / 3_600_000
    return {
        "cycles": len(cy),
        "gain_cumule": sum(gains),
        "gain_pct": sum(gains) / budget if budget else 0.0,
        "equity_finale": r["equity_finale"],
        "perf_pct": r["equity_finale"] / budget - 1 if budget else 0.0,
        "drawdown_max": dd,
        "bloque": r["investi_bloque"],
        "bloque_pct": r["investi_bloque"] / budget if budget else 0.0,
        "latent": r["valeur_lot"] - r["investi_bloque"],
        "abandons": r["abandons"],
        "engage_moyen": ag["engage_moyen"],
        "part_temps_engage": ag["part_temps_engage"],
        "duree_moyenne_h": sum(c.heures for c in cy) / len(cy) if cy else 0.0,
        "cycles_par_mois": len(cy) / (duree_h / 730) if duree_h else 0.0,
        "gain_moyen": sum(gains) / len(cy) if cy else 0.0,
    }
