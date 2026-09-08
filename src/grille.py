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
    espacement: str = "lineaire"    # "lineaire", "geometrique" ou "puissance"
    courbure: float = 1.0           # espacement="puissance" : 1 = lineaire, plus = resserre en haut
    #  L'etude des chutes des 400 jours donne une distribution tres asymetrique :
    #  45 % des chutes s'arretent a -1 %, 10 % a -5 %, 1,8 % a -10 %, aucune sous
    #  -22 %. Des barreaux equidistants placent donc la moitie de l'echelle la ou
    #  le prix ne va presque jamais. Une courbure superieure a 1 resserre les
    #  barreaux en haut et les ecarte en bas, dans la forme meme de cette
    #  distribution. La valeur 1 redonne exactement l'espacement lineaire.
    #  ---- le cliquet : l'objectif devient un plancher, pas une sortie ----
    cliquet_pas: float | None = None    # None = inerte, le moteur ne change pas
    cliquet_retrait: float = 0.001      # de combien le stop se place sous le cran atteint
    cliquet_vue: str = "sondage"        # "sondage" (cloture) ou "carnet" (ordre dormant)
    frais_taker: float = 0.0015         # un stop part au marche, pas au tarif maker
    glissement_stop: float = 0.0005     # un stop ne garantit pas son prix
    #  cliquet_pas commande trois mecanismes avec une seule formule : 0 suit le
    #  prix en continu, une valeur usuelle donne l'escalier demande, une valeur
    #  enorme (10.0) pose un stop fixe qui ne monte jamais — ce dernier sert de
    #  bras de controle, pour attribuer un gain au CLIQUET et non au simple
    #  passage d'une vente limite a un ordre stop."
    objectif_profond: float | None = None   # objectif vise quand l'echelle est pleine
    #  Un objectif unique traite de la meme facon un lot d'un barreau et un lot
    #  qui a mange tout le budget. Or c'est quand la descente est profonde que le
    #  capital est immobilise et que l'attente coute. objectif_profond permet de
    #  relacher la cible a mesure qu'on s'enfonce, donc de recycler le capital
    #  plus tot. Laisse a None, l'objectif ne varie pas : comportement d'origine.
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
        if self.espacement not in ("lineaire", "geometrique", "puissance"):
            raise GrilleError(f"espacement inconnu : {self.espacement}")
        if self.courbure <= 0:
            raise GrilleError(f"courbure doit etre > 0, trouve {self.courbure}")
        if self.cliquet_pas is not None:
            if self.cliquet_pas < 0:
                raise GrilleError(f"cliquet_pas doit etre >= 0, trouve {self.cliquet_pas}")
            if not 0 <= self.cliquet_retrait < 1:
                raise GrilleError(f"cliquet_retrait hors de [0, 1[ : {self.cliquet_retrait}")
            if self.cliquet_vue not in ("sondage", "carnet"):
                raise GrilleError(f"cliquet_vue inconnue : {self.cliquet_vue}")
            if not 0 <= self.frais_taker < 0.05:
                raise GrilleError(f"frais_taker invraisemblables : {self.frais_taker}")
            if not 0 <= self.glissement_stop < 0.05:
                raise GrilleError(f"glissement_stop invraisemblable : {self.glissement_stop}")
            #  Le plancher garanti par le cliquet doit rester un GAIN. Sans cette
            #  garde, un retrait plus large que l'objectif ferait d'une "sortie
            #  reussie" une perte, et le balayage le decouvrirait en silence.
            g_min = (min(self.objectif_net, self.objectif_profond)
                     if self.objectif_profond is not None else self.objectif_net)
            if (1 + g_min - self.cliquet_retrait) * (1 - self.glissement_stop) <= 1:
                raise GrilleError(
                    f"cliquet_retrait {self.cliquet_retrait:.2%} laisse un plancher "
                    f"non rentable face a un objectif de {g_min:.2%} : la sortie perdrait"
                )
        if self.objectif_profond is not None:
            if not 0 < self.objectif_profond < 1:
                raise GrilleError(f"objectif_profond hors de ]0, 1[ : {self.objectif_profond}")
            if self.objectif_profond <= 2 * self.frais:
                raise GrilleError(
                    f"objectif_profond {self.objectif_profond:.2%} ne couvre pas l'aller-retour "
                    f"de frais ({2 * self.frais:.2%}) : la descente perdrait au fond"
                )

    def echelle(self, reference: float, budget: float) -> list[tuple[float, float]]:
        """Les barreaux : (prix cible, mise en euros), du haut vers le bas.

        Les mises suivent une progression geometrique de raison `ratio` et
        somment exactement au budget.
        """
        if reference <= 0 or budget <= 0:
            raise GrilleError("reference et budget doivent etre > 0")
        poids = [self.ratio ** i for i in range(self.paliers)]
        total = sum(poids)
        haut = reference * (1 - self.depart_sous)
        bas = reference * (1 - self.depart_sous - self.profondeur)
        n = self.paliers - 1
        if self.espacement == "puissance":
            #  la profondeur croit comme (i/n)^courbure : ecarts serres en haut,
            #  larges en bas, sans changer ni le premier ni le dernier barreau
            prix = [haut - (haut - bas) * (i / n) ** self.courbure
                    for i in range(self.paliers)]
        elif self.espacement == "geometrique":
            #  Memes extremites que le lineaire, repartition differente au milieu :
            #  les ecarts sont constants en POURCENTAGE et non en euros, ce qui a du
            #  sens pour un prix. Sur une echelle profonde la difference est nette.
            prix = [haut * (bas / haut) ** (i / n) for i in range(self.paliers)]
        else:
            prix = [haut + (bas - haut) * i / n for i in range(self.paliers)]
        return [(p, budget * w / total) for p, w in zip(prix, poids)]

    def objectif_a(self, remplis: int) -> float:
        """L'objectif vise pour un lot de `remplis` barreaux.

        Interpole entre objectif_net au premier barreau et objectif_profond a
        l'echelle pleine. Sans objectif_profond, la cible ne bouge pas.
        """
        if self.objectif_profond is None or self.paliers < 2 or remplis <= 1:
            return self.objectif_net
        f = min(1.0, (remplis - 1) / (self.paliers - 1))
        return self.objectif_net + (self.objectif_profond - self.objectif_net) * f


@dataclass
class Descente:
    """Une descente en cours : ce qui a ete achete, et ce qu'on attend."""

    symbole: str
    reference: float
    budget: float
    reglages: Reglages
    ouverte_le: int | None = None                # horodatage ms du premier achat
    dernier_achat_le: int | None = None          # horodatage ms du dernier achat
    #  Deux horodatages, parce qu'ils repondent a deux questions. ouverte_le date
    #  le cycle, pour sa duree. dernier_achat_le garde l'interdiction de
    #  l'aller-retour intra-bougie : la comparer a ouverte_le ne protegeait que
    #  le PREMIER barreau du lot, si bien qu'un barreau ajoute au bas d'une
    #  bougie haussiere pouvait etre revendu au haut de la meme bougie.
    #  None, pas 0 : un horodatage a zero est un instant valide, et le tester
    #  avec `if not ouverte_le` le confondait avec l'absence d'achat.
    remplis: list[int] = field(default_factory=list)   # indices des barreaux achetes
    cumul_euros: float = 0.0                     # euros REELLEMENT sortis, frais inclus
    cumul_unites: float = 0.0                    # unites NETTES detenues
    abandonnee: bool = False
    stop: float = 0.0                            # prix du stop arme ; 0.0 = pas arme
    crans: int = 0                               # crans montes au-dessus du premier
    arme_le: int | None = None                   # ts du PREMIER armement du lot
    #  stop est un PRIX et non un pourcentage : les deux ne different que si le
    #  prix de revient bouge pendant que le stop est arme, ce qu'un stop arme
    #  interdit justement en annulant les ordres d'achat.

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
        return self.prix_revient * (1 + r.objectif_a(len(self.remplis))) / (1 - r.frais)

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

    def rentabilite(self, prix: float) -> float:
        """Ce que rapporterait une sortie AU MARCHE a ce prix, nette de tout."""
        if not self.cumul_unites:
            return 0.0
        return prix * (1 - self.reglages.frais_taker) / self.prix_revient - 1

    def prix_pour(self, niveau: float) -> float:
        """Le prix auquel une sortie au marche rendrait exactement `niveau`."""
        return self.prix_revient * (1 + niveau) / (1 - self.reglages.frais_taker)

    def armer_ou_monter(self, prix_lu: float, ts: int) -> bool:
        """Arme le stop, ou le monte d'autant de crans que le prix le permet.

        Plusieurs crans peuvent etre franchis dans une seule bougie : n'en
        autoriser qu'un ferait dependre le resultat de la finesse
        d'echantillonnage, defaut que ce depot a deja paye cher.
        """
        r = self.reglages
        if r.cliquet_pas is None or not self.cumul_unites:
            return False
        g0 = r.objectif_a(len(self.remplis))
        rent = self.rentabilite(prix_lu)
        if rent < g0:                       # sous le seuil : on n'arme rien
            return False
        if r.cliquet_pas > 0:
            k = int((rent - g0) // r.cliquet_pas)
            niveau = g0 + k * r.cliquet_pas - r.cliquet_retrait
        else:                               # pas nul : le stop suit le prix en continu
            k = self.crans
            niveau = rent - r.cliquet_retrait
        cand = self.prix_pour(niveau)
        if cand <= self.stop:
            return False
        self.stop, self.crans = cand, max(self.crans, k)
        if self.arme_le is None:
            self.arme_le = ts
        return True

    def barreaux_a_poser(self) -> list[tuple[int, float, float]]:
        """Les ordres d'achat a laisser au carnet : (indice, prix, euros).

        Un stop arme les annule tous. Demonstration qu'aucun achat n'est perdu :
        les mises croissent avec l'indice, le filtre mise_min supprime donc un
        prefixe haut et jamais un suffixe bas ; le prix de revient est donc
        superieur au barreau rempli le plus bas, donc a tout barreau libre ; et
        la garde de construction impose stop > prix_revient. Le stop est donc
        toujours AU-DESSUS de tout barreau libre : un prix qui descend le
        traverse avant eux, et l'ordre stop serait servi le premier.
        """
        if self.abandonnee or self.stop:
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
        self.dernier_achat_le = ts
        return euros, unites

    def vendre(self, prix: float, *, au_marche: bool = False) -> dict[str, float]:
        """Revend tout le lot. Retourne le detail du cycle.

        au_marche=True applique le tarif taker : une sortie declenchee par un
        stop part au marche, jamais au tarif maker d'un ordre limite dormant.
        """
        if not self.cumul_unites:
            raise GrilleError("rien a vendre")
        brut = self.cumul_unites * prix
        recu = brut * (1 - (self.reglages.frais_taker if au_marche else self.reglages.frais))
        gain = recu - self.cumul_euros
        detail = {
            "unites": self.cumul_unites, "prix": prix, "brut": brut, "recu": recu,
            "investi": self.cumul_euros, "gain": gain,
            "gain_pct": gain / self.cumul_euros if self.cumul_euros else 0.0,
            "paliers": len(self.remplis), "prix_revient": self.prix_revient,
        }
        (self.remplis, self.cumul_euros, self.cumul_unites, self.ouverte_le,
         self.dernier_achat_le, self.stop, self.crans,
         self.arme_le) = [], 0.0, 0.0, None, None, 0.0, 0, None
        return detail

    def valeur(self, prix: float) -> float:
        """Valeur de liquidation du lot detenu, frais de sortie deduits.

        Quand le cliquet est actif il n'existe plus aucune sortie au tarif
        maker : valoriser le lot au tarif limite surestimerait la nouvelle
        version dans toute comparaison.
        """
        r = self.reglages
        if r.cliquet_pas is not None:
            return self.cumul_unites * prix * (1 - r.frais_taker) * (1 - r.glissement_stop)
        return self.cumul_unites * prix * (1 - r.frais)


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
    sortie: str = "limite"   # "limite" | "stop" | "trou"
    crans: int = 0           # crans montes par le cliquet avant la sortie
    reference: float = 0.0   # prix de reference de l'echelle qui a servi
    #  La reference est celle EN VIGUEUR PENDANT le cycle. Sans elle, une page
    #  qui veut redessiner l'echelle doit la relire dans la serie des marches, ou
    #  elle tombe sur la reference d'APRES la vente — decalee d'un pour cent en
    #  mediane, de quatre dans le pire cas.


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


def chemin_bougie(o: float, h: float, l: float, c: float) -> tuple[tuple[float, bool], tuple[float, bool]]:
    """Dans quel ordre le prix a-t-il probablement parcouru la bougie ?

    Rend deux jambes (prix, monte). `monte` DIT ce qu'est la jambe, il ne se
    deduit pas du prix : identifier la jambe basse par une egalite de flottants
    (`extreme == h`) etait un defaut silencieux. Sur une bougie plate, haut et
    bas valent le meme nombre, les deux jambes etaient donc lues comme montantes
    et la branche d'achat n'etait jamais atteinte. Mesure faite sur cet
    historique : 1,1 % des bougies horaires sont plates, mais 32 % des bougies
    de cinq minutes et 61 a 67 % de celles a la minute. Plus on affinait le pas
    pour gagner en realisme, plus on perdait d'achats.

    Convention retenue, la plus courante et la plus defendable : une bougie
    haussiere est allee d'abord au plus BAS puis au plus HAUT, une bougie
    baissiere l'inverse. Pour une grille, cette convention est la moins
    flatteuse : sur une bougie baissiere elle fait toucher le haut avant le bas,
    donc elle refuse une vente qui aurait pu avoir lieu apres un achat plus bas
    dans la meme heure.
    """
    return ((l, False), (h, True)) if c >= o else ((h, True), (l, False))


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

    cliquet = reglages.cliquet_pas is not None
    for ts, o, h, l, c in bougies:
        ferme = False

        def sortir(px: float, genre: str, _ts: int = 0) -> None:
            """Solde le lot au marche et ouvre une descente au prix du moment."""
            nonlocal d, cash, ferme
            ouvert = ts if d.ouverte_le is None else d.ouverte_le
            crans, ref_cycle = d.crans, d.reference
            det = d.vendre(px, au_marche=True)
            cash += det["recu"]
            cycles.append(Cycle(
                symbole, ouvert, ts, det["paliers"], det["investi"], det["recu"],
                det["gain"], det["gain_pct"], det["prix_revient"], px,
                (ts - ouvert) / 3_600_000, genre, crans, ref_cycle,
            ))
            if trace:
                journal.append(Evenement(ts, "vente", px, -1, det["recu"],
                                         det["prix_revient"], det["gain"]))
            d = Descente(symbole, c, budget, reglages)
            ferme = True

        #  Un trou de cotation : la bougie OUVRE deja sous le stop. Le stop n'a pas
        #  ete traverse en seance, il a ete saute — c'est le seul cas ou le plancher
        #  garanti est viole, et il se compte a part.
        if (cliquet and reglages.cliquet_vue == "carnet" and d.stop
                and d.arme_le is not None and ts > d.arme_le and o <= d.stop):
            sortir(max(l, o * (1 - reglages.glissement_stop)), "trou")

        for extreme, monte in chemin_bougie(o, h, l, c):
            if ferme:
                break
            #  Un aller-retour dans la meme bougie n'est pas observable : il suppose
            #  que le bas a ete visite avant le haut ET que l'ordre limite de vente a
            #  ete servi au sommet de la meche. Les interdire donne la borne basse.
            trop_tot = (not reglages.vente_meme_bougie
                        and d.dernier_achat_le is not None and ts <= d.dernier_achat_le)
            if monte:
                #  Le cliquet ne lit JAMAIS le haut d'une bougie : ni pour armer, ni
                #  pour monter, ni pour sortir. Un robot qui se reveille a la cloture
                #  ne peut pas savoir qu'une meche est passee par la.
                if not cliquet and d.cumul_unites and h >= d.prix_sortie and not trop_tot:
                    px = d.prix_sortie
                    # lire l'ouverture AVANT de vendre : vendre() remet la descente a zero
                    ouvert = ts if d.ouverte_le is None else d.ouverte_le
                    ref_cycle = d.reference
                    det = d.vendre(px)
                    cash += det["recu"]
                    cycles.append(Cycle(
                        symbole, ouvert, ts, det["paliers"], det["investi"],
                        det["recu"], det["gain"], det["gain_pct"], det["prix_revient"], px,
                        (ts - ouvert) / 3_600_000, "limite", 0, ref_cycle,
                    ))
                    if trace:
                        journal.append(Evenement(ts, "vente", px, -1, det["recu"],
                                                 det["prix_revient"], det["gain"]))
                    d = Descente(symbole, c, budget, reglages)   # on repart du prix du moment
                    d.abandonnee = False
            else:
                #  Un ordre stop dormant au carnet se declenche en seance, sur le bas.
                if (cliquet and reglages.cliquet_vue == "carnet" and d.stop
                        and d.arme_le is not None and ts > d.arme_le and l <= d.stop):
                    sortir(max(l, d.stop * (1 - reglages.glissement_stop)), "stop")
                    break
                #  Acheter D'ABORD, abandonner ENSUITE. Le seuil est sous le
                #  dernier barreau : une bougie qui l'atteint a donc traverse
                #  toute l'echelle en descendant. Poser l'abandon avant la boucle
                #  vidait barreaux_a_poser() et faisait perdre ces achats reels.
                for i, prix, euros in d.barreaux_a_poser():
                    if l <= prix and cash >= euros - 1e-9:
                        d.acheter(i, prix, ts)
                        cash -= euros
                        if trace:
                            journal.append(Evenement(ts, "achat", prix, i, euros,
                                                     d.prix_revient, 0.0))
                if l <= d.seuil_abandon and not d.abandonnee:
                    d.abandonnee = True
                    abandons += 1
                    if trace:
                        journal.append(Evenement(ts, "abandon", d.seuil_abandon, -1, 0.0,
                                                 d.prix_revient, 0.0))

        #  Le cliquet vit a la cloture : c'est le seul instant qu'un robot qui se
        #  reveille une fois par bougie observe reellement. Armer ou monter d'abord,
        #  declencher ensuite — un stop pose a cette cloture est sous elle, il ne
        #  peut donc pas se declencher dans la foulee.
        if cliquet and not ferme and d.cumul_unites:
            trop_tot = (not reglages.vente_meme_bougie
                        and d.dernier_achat_le is not None and ts <= d.dernier_achat_le)
            if not trop_tot and d.armer_ou_monter(c, ts) and trace:
                journal.append(Evenement(ts, "cliquet", d.stop, d.crans, 0.0,
                                         d.prix_revient, 0.0))
            if (reglages.cliquet_vue == "sondage" and d.stop
                    and d.arme_le is not None and ts > d.arme_le and c <= d.stop):
                sortir(max(l, c * (1 - reglages.glissement_stop)), "stop")

        #  Sans cette ligne, l'echelle reste plantee la ou la derniere vente l'a
        #  laissee. Si le marche s'eleve de 20 % sans que rien ne soit achete, le
        #  premier barreau est 20 % sous le cours et n'est jamais rejoint : la
        #  grille s'endort. Un operateur humain, lui, repose son echelle sous le
        #  prix du moment. On ne suit qu'a la HAUSSE et qu'a vide : suivre a la
        #  baisse reviendrait a courir apres le marche en annulant ses achats, et
        #  suivre en portant un lot deplacerait l'echelle sous ses propres achats.
        #  reancrage_min evite de courir apres le bruit : sur des bougies a la
        #  minute, deplacer l'echelle a chaque tick haussier la collerait au prix.
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
