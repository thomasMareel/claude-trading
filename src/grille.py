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

from collections import deque
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
    #  ---- sur quoi l'echelle s'accroche ----
    moyenne_ref: int = 1            # bougies moyennees pour la reference ; 1 = la derniere cloture
    #  La reference est le prix sous lequel toute l'echelle se pose. Elle vaut,
    #  depuis l'origine, la DERNIERE CLOTURE au moment ou l'echelle se re-ancre.
    #  Une cloture est un instant : elle porte tout le bruit de la bougie, et une
    #  meche suffit a reposer l'echelle un pour cent plus haut. moyenne_ref
    #  remplace cet instant par la moyenne des N dernieres clotures.
    #
    #  UN VAUT EXACTEMENT L'ANCIEN COMPORTEMENT : avec N = 1 la moyenne d'une
    #  seule cloture EST cette cloture, et le moteur repasse au bit pres sur le
    #  chemin d'avant. C'est ce qui rend le parametre sur — et c'est verifie.
    #
    #  Le parametre n'agit QUE par le re-ancrage, donc uniquement si
    #  suivre_hausse est vrai. Avec suivre_hausse = False, l'echelle ne bouge
    #  jamais et moyenne_ref est inerte.
    espacement: str = "lineaire"    # "lineaire", "geometrique" ou "puissance"
    courbure: float = 1.0           # espacement="puissance" : 1 = lineaire, plus = resserre en haut
    #  L'etude des chutes des 400 jours donne une distribution tres asymetrique :
    #  45 % des chutes s'arretent a -1 %, 10 % a -5 %, 1,8 % a -10 %, aucune sous
    #  -22 %. Des barreaux equidistants placent donc la moitie de l'echelle la ou
    #  le prix ne va presque jamais. Une courbure superieure a 1 resserre les
    #  barreaux en haut et les ecarte en bas, dans la forme meme de cette
    #  distribution. La valeur 1 redonne exactement l'espacement lineaire.
    #  ---- l'echelle exprimee en multiples de la volatilite de la paire ----
    echelle_vol: str = ""           # "" inerte | "echelle" | "objectif" | "tout"
    unite_vol: float = 0.0          # l'amplitude a laquelle ce qui est ecrit vaut tel quel
    fenetre_vol: int = 0            # bougies sur lesquelles l'amplitude est mesuree
    facteur_min: float = 0.25       # bornes de l'elargissement, pour qu'il reste une echelle
    facteur_max: float = 4.0
    #  UNE ECHELLE A -2 % NE VEUT PAS DIRE LA MEME CHOSE SUR DEUX MONNAIES.
    #  Mesure faite sur les dix paires du panier : ETH bouge douze fois plus vite
    #  que TRX en amplitude mediane a cinq minutes. Un barreau pose 2 % sous le
    #  cours est donc, sur ETH, a portee d'une matinee, et sur TRX a portee d'un
    #  mois. Poser la meme echelle sur les deux, c'est faire tourner deux
    #  strategies differentes en croyant n'en tester qu'une.
    #
    #  CE QUE CES CHAMPS FONT. Quand echelle_vol n'est pas vide, ce qui est ecrit
    #  dans profondeur, depart_sous et objectif_net n'est plus une fraction du
    #  prix mais une fraction A LA VOLATILITE unite_vol. Sur une paire qui bouge
    #  deux fois plus vite, tout est deux fois plus large. L'hypothese, et elle
    #  est refutable : ainsi exprimees, les dix paires deviennent comparables et
    #  un seul reglage vaut pour toutes. Si c'est faux, la dispersion entre
    #  paires ne se resserrera pas, et il faudra bien des reglages par classe.
    #
    #  TROIS MODES PLUTOT QU'UN, parce que l'echelle et l'objectif ne repondent
    #  pas de la meme chose : l'echelle commande ce qu'on achete, l'objectif
    #  commande la DUREE, seule condition posee. Les separer permet de dire
    #  lequel des deux paie.
    #
    #  LA VOLATILITE EST MESUREE SUR LES fenetre_vol BOUGIES PRECEDENTES, en
    #  moyenne de (haut - bas) / cloture, et elle est FIGEE a la pose de
    #  l'echelle : une echelle qui se redimensionnerait sous ses propres achats
    #  n'aurait plus de prix de sortie stable. Moyenne et non mediane parce
    #  qu'une moyenne glissante se tient a jour en temps constant quand une
    #  mediane glissante rendrait le rejeu quadratique ; la mediane reste le
    #  critere des CLASSES, qui se calculent une fois, hors rejeu.
    #
    #  VIDE VAUT EXACTEMENT L'ANCIEN COMPORTEMENT : facteur_vol rend 1.0 et
    #  aucune multiplication n'a lieu. C'est ce qui rend le champ sur, et c'est
    #  verifie par un test qui rejoue les deux moteurs sur les memes bougies.
    #  ---- les declencheurs : QUAND l'echelle a le droit d'acheter ----
    devi_chute: float = 0.0       # (b) chute exigee avant d'armer, en fraction
    devi_fenetre: int = 0         # (b) sur combien de bougies cette chute se mesure
    filtre_moyenne: int = 0       # (c) bougies de la moyenne mobile ; 0 = inerte
    filtre_sens: str = "sous"     # (c) "sous" ou "dessus" la moyenne
    confirmation: int = 0         # (e) clotures en hausse exigees avant d'acheter
    #  L'ECHELLE, JUSQU'ICI, ACHETE DES QU'ON LA TOUCHE. Elle ne demande jamais
    #  pourquoi le prix est la. Ces trois champs posent chacun une condition
    #  differente sur le DROIT d'acheter, sans toucher a l'echelle elle-meme :
    #
    #    (b) DEVIATION  — n'acheter que si le prix a deja chute d'au moins
    #        devi_chute par rapport a son plus haut des devi_fenetre dernieres
    #        bougies. Idee : ne pas engager le budget sur un simple bruit.
    #    (c) FILTRE DE MOYENNE — n'acheter que du bon cote d'une moyenne mobile.
    #        « sous » achete dans les creux ; « dessus » n'achete qu'en tendance
    #        haussiere. Les deux sens sont balayes parce que rien ne dit lequel
    #        est le bon, et qu'affirmer l'un sans mesurer l'autre serait une
    #        croyance deguisee en reglage.
    #    (e) CONFIRMATION — n'acheter qu'apres confirmation clotures consecutives
    #        en hausse. Idee : attendre que la chute s'arrete plutot que de
    #        l'accompagner.
    #
    #  LES TROIS SE DECIDENT A LA CLOTURE PRECEDENTE, jamais pendant la bougie en
    #  cours. C'est la meme regle que pour la fenetre de volatilite, et pour la
    #  meme raison : un robot qui se reveille a chaque cloture ne sait rien de la
    #  bougie qu'il est en train de traverser. Lire la cloture courante pour
    #  decider d'un achat qui a lieu dans cette meme bougie serait du regard vers
    #  l'avenir, et il serait invisible dans les resultats.
    #
    #  Ils ne bloquent QUE LES ACHATS. Une vente reste toujours possible : un
    #  declencheur qui empecherait de solder un lot ne serait pas un declencheur,
    #  ce serait un piege.
    #  ---- deux facons de ne pas rester bloque ----
    suivre_baisse: bool = False   # redescendre la reference quand le marche baisse a vide
    sortie_jours: int = 0         # solder au marche apres N jours de capital bloque
    #  suivre_baisse EST LE SYMETRIQUE DE suivre_hausse, AVEC UNE DIFFERENCE QUI
    #  CHANGE TOUT : le seuil d'abandon reste FIGE sur l'ancre d'origine. Sans
    #  cela, une echelle qui redescend emporte son abandon avec elle, et la
    #  regle « sous le dernier barreau, on n'ajoute plus rien » ne se declenche
    #  jamais — le bot poursuivrait le prix vers le bas indefiniment, ce qui est
    #  exactement le mode de defaillance que l'abandon existe pour borner.
    #
    #  sortie_jours est la SEULE vente a perte autorisee, et elle demande cent
    #  jours par defaut cote appelant. Elle existe parce qu'un capital bloque
    #  n'est pas une perte comptable mais une perte reelle : il ne travaille plus.
    #  ---- ce que le carnet pouvait reellement absorber ----
    participation_max: float = 0.0   # part du volume de la bougie ; 0 = inerte
    #  LE REJEU SERT UN ORDRE ENTIER, INSTANTANEMENT, DES QUE LE BAS D'UNE BOUGIE
    #  TOUCHE SON PRIX, sans jamais regarder s'il s'est echange quoi que ce soit.
    #  Mesure faite sur 880 jours des dix paires EUR d'OKX : le barreau du bas
    #  d'une echelle a huit barreaux de raison 1,6 et dix mille euros vaut
    #  3 839 EUR, et il n'aurait pu etre absorbe que dans 0,03 % des bougies de
    #  cinq minutes de TRX, contre 25,5 % de celles de BTC. Sur six paires sur
    #  dix, la quasi-totalite des achats de ce rejeu sont donc des achats que le
    #  marche n'aurait pas pu servir.
    #
    #  CE CHAMP NE CORRIGE PAS LE MODELE, IL L'ENCADRE. Quand il est actif, un
    #  ordre plus gros que participation_max fois ce qui s'est echange pendant la
    #  bougie N'EST PAS SERVI DU TOUT : il reste au carnet et pourra l'etre plus
    #  tard. C'est volontairement trop severe — dans la realite il serait servi
    #  EN PARTIE. Le rejeu sans plafond majore donc le rendement, le rejeu avec
    #  plafond le minore, et le vrai est entre les deux. Un encadrement vaut
    #  mieux qu'une correction dont personne ne saurait dire le sens de l'erreur.
    #
    #  Il faut pour cela que les bougies portent un sixieme champ, le volume en
    #  monnaie de base. Les series a cinq champs continuent de fonctionner : sans
    #  volume, aucun plafond ne s'applique et le moteur est celui d'avant.
    #
    #  LA SORTIE AU MARCHE N'EST PAS PLAFONNEE, et c'est ecrit plutot que tu :
    #  un ordre au marche traverse le carnet au lieu d'y dormir, et c'est
    #  glissement_stop qui en porte le cout. La vente LIMITE, elle, est plafonnee
    #  comme les achats, puisqu'elle dort au carnet exactement comme eux.
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
        if not isinstance(self.moyenne_ref, int) or self.moyenne_ref < 1:
            raise GrilleError(
                f"moyenne_ref doit etre un entier >= 1, trouve {self.moyenne_ref!r}")
        if self.espacement not in ("lineaire", "geometrique", "puissance"):
            raise GrilleError(f"espacement inconnu : {self.espacement}")
        if self.courbure <= 0:
            raise GrilleError(f"courbure doit etre > 0, trouve {self.courbure}")
        if not 0 <= self.devi_chute < 1:
            raise GrilleError(f"devi_chute hors de [0, 1[ : {self.devi_chute}")
        if bool(self.devi_chute) != bool(self.devi_fenetre):
            raise GrilleError(
                "devi_chute et devi_fenetre vont ensemble : une chute sans fenetre "
                "ne se mesure sur rien, une fenetre sans chute ne declenche rien")
        if self.filtre_moyenne < 0:
            raise GrilleError(f"filtre_moyenne doit etre >= 0, trouve {self.filtre_moyenne}")
        if self.filtre_sens not in ("sous", "dessus"):
            raise GrilleError(f"filtre_sens inconnu : {self.filtre_sens!r}")
        if self.confirmation < 0:
            raise GrilleError(f"confirmation doit etre >= 0, trouve {self.confirmation}")
        if self.sortie_jours < 0:
            raise GrilleError(f"sortie_jours doit etre >= 0, trouve {self.sortie_jours}")
        if not 0 <= self.participation_max <= 1:
            raise GrilleError(
                f"participation_max doit etre une part dans [0, 1], "
                f"trouve {self.participation_max}")
        if self.echelle_vol not in ("", "echelle", "objectif", "tout"):
            raise GrilleError(f"echelle_vol inconnu : {self.echelle_vol!r}")
        if self.echelle_vol:
            if self.unite_vol <= 0:
                raise GrilleError(
                    f"echelle_vol={self.echelle_vol!r} exige une unite_vol > 0 : "
                    f"sans unite de reference, « deux fois plus large » n'a pas de sens")
            if self.fenetre_vol < 1:
                raise GrilleError(
                    f"echelle_vol={self.echelle_vol!r} exige fenetre_vol >= 1, "
                    f"trouve {self.fenetre_vol}")
            if not 0 < self.facteur_min <= 1 <= self.facteur_max:
                raise GrilleError(
                    f"les bornes de l'elargissement doivent encadrer 1, trouve "
                    f"[{self.facteur_min}, {self.facteur_max}]")
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

    def facteur_vol(self, vol: float) -> float:
        """De combien la volatilite du moment elargit ce qui est ecrit.

        Borne des deux cotes. Sans bornes, une paire endormie — et il y en a :
        86 % des bougies de cinq minutes de TRX sont plates — donnerait un
        facteur proche de zero et une echelle de quelques dixiemes de pour cent,
        qui ne serait plus une grille mais du bruit ; et un jour de krach
        donnerait une echelle plus profonde que la regle d'abandon. Les bornes
        encadrent 1, donc une volatilite egale a l'unite ne change rien.
        """
        if not self.echelle_vol or self.unite_vol <= 0 or vol <= 0:
            return 1.0
        return min(self.facteur_max, max(self.facteur_min, vol / self.unite_vol))

    def echelle(self, reference: float, budget: float,
                vol: float = 0.0) -> list[tuple[float, float]]:
        """Les barreaux : (prix cible, mise en euros), du haut vers le bas.

        Les mises suivent une progression geometrique de raison `ratio` et
        somment exactement au budget.
        """
        if reference <= 0 or budget <= 0:
            raise GrilleError("reference et budget doivent etre > 0")
        depart, profondeur = self.depart_sous, self.profondeur
        if self.echelle_vol in ("echelle", "tout"):
            f = self.facteur_vol(vol)
            depart, profondeur = depart * f, profondeur * f
            #  La garde de construction porte sur ce qui est ECRIT ; une echelle
            #  elargie doit encore tenir sous la reference. On rogne la
            #  PROFONDEUR et jamais le depart : deplacer l'entree changerait la
            #  strategie, raccourcir le bas ne fait que la tronquer, ce qui est
            #  le comportement d'un budget epuise et non d'un autre reglage.
            if depart + profondeur >= 0.95:
                profondeur = max(0.01, 0.95 - depart)
        poids = [self.ratio ** i for i in range(self.paliers)]
        total = sum(poids)
        haut = reference * (1 - depart)
        bas = reference * (1 - depart - profondeur)
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

    def objectif_a(self, remplis: int, vol: float = 0.0) -> float:
        """L'objectif vise pour un lot de `remplis` barreaux.

        Interpole entre objectif_net au premier barreau et objectif_profond a
        l'echelle pleine. Sans objectif_profond, la cible ne bouge pas.
        """
        if self.objectif_profond is None or self.paliers < 2 or remplis <= 1:
            g = self.objectif_net
        else:
            f = min(1.0, (remplis - 1) / (self.paliers - 1))
            g = self.objectif_net + (self.objectif_profond - self.objectif_net) * f
        if self.echelle_vol in ("objectif", "tout"):
            #  Le plancher n'est pas cosmetique : la garde de construction refuse
            #  un objectif qui ne couvre pas l'aller-retour de frais, et un
            #  facteur inferieur a 1 pourrait faire passer dessous une cible
            #  pourtant valide a l'ecriture. On la retient au-dessus des frais
            #  avec la meme marge que la garde, plutot que de laisser le moteur
            #  vendre a perte en silence.
            g = min(0.5, max(2.5 * self.frais, g * self.facteur_vol(vol)))
        return g


@dataclass
class Descente:
    """Une descente en cours : ce qui a ete achete, et ce qu'on attend."""

    symbole: str
    reference: float
    budget: float
    reglages: Reglages
    #  LE PRIX AU MOMENT OU L'ECHELLE EST POSEE. Zero = inerte.
    #
    #  Tant que la reference EST la derniere cloture, aucun barreau ne peut se
    #  retrouver au-dessus du cours et ce champ ne sert a rien. Des que la
    #  reference devient une moyenne (moyenne_ref > 1), elle passe au-dessus du
    #  prix la moitie du temps, et les barreaux du haut se posent AU-DESSUS DU
    #  MARCHE. Le moteur les remplissait alors a leur prix limite : marche plat
    #  a 100, reference 120, il achetait a 117,60 puis 116,35 puis 112,60 et
    #  perdait 1,32 % sans que le prix ait bouge d'un centime.
    #
    #  Un ordre d'achat limite pose au-dessus du marche n'attend pas : il part
    #  au marche, au tarif taker, immediatement. Plutot que d'inventer cette
    #  execution-la et son tarif, on refuse le barreau — exactement comme on
    #  refuse celui dont la mise n'atteint pas le minimum de la plateforme. Son
    #  budget reste en caisse et rien n'est redistribue.
    plafond: float = 0.0
    #  L'AMPLITUDE MOYENNE RECENTE, figee a la pose de l'echelle. Zero = inerte.
    #  Figee, parce qu'une echelle qui se redimensionnerait sous ses propres
    #  achats n'aurait plus de prix de sortie stable : le lot serait achete sur
    #  une echelle et revendu sur une autre, et le journal ne voudrait rien dire.
    volatilite: float = 0.0
    #  LE SEUIL D'ABANDON FIGE, transmis d'une echelle a la suivante quand la
    #  reference redescend a vide. Zero = le seuil se recalcule sous le dernier
    #  barreau, comportement d'origine. Voir suivre_baisse.
    abandon_fige: float = 0.0
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
        return self.reglages.echelle(self.reference, self.budget, self.volatilite)

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
        return (self.prix_revient * (1 + r.objectif_a(len(self.remplis), self.volatilite))
                / (1 - r.frais))

    @property
    def dernier_palier(self) -> float:
        return self.echelle[-1][0]

    @property
    def seuil_abandon(self) -> float:
        if self.abandon_fige > 0:
            return self.abandon_fige
        return self.dernier_palier * (1 - self.reglages.abandon_sous)

    @property
    def barreaux_morts(self) -> list[int]:
        """Les barreaux dont la mise est sous le minimum de la plateforme.

        Ils ne sont jamais poses au carnet. Leur budget reste en caisse : on ne
        le redistribue pas sur les barreaux valides, sans quoi on modifierait en
        douce l'echelle que l'on pretend tester.
        """
        m = self.reglages.mise_min
        pl = self.plafond
        return [i for i, (p, e) in enumerate(self.echelle)
                if (m and e < m) or (pl > 0 and p > pl)]

    @property
    def barreaux_au_dessus(self) -> list[int]:
        """Ceux que le plafond ecarte, et eux seuls : la mesure de l'artefact.

        Comptes a part des barreaux trop petits, parce qu'ils ne disent pas la
        meme chose. Un barreau trop petit dit que le budget est trop mince ; un
        barreau au-dessus du cours dit que la reference a decroche du marche.
        """
        pl = self.plafond
        return [i for i, (p, _) in enumerate(self.echelle) if pl > 0 and p > pl]

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
        g0 = r.objectif_a(len(self.remplis), self.volatilite)
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
        pl = self.plafond
        return [(i, p, e) for i, (p, e) in enumerate(self.echelle)
                if i not in self.remplis and e >= m and not (pl > 0 and p > pl)]

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
    sortie: str = "limite"   # "limite" | "stop" | "trou" | "delai"
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
    trace: bool = True, prechauffe: list[float] | None = None,
    prechauffe_vol: list[float] | None = None,
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
    #  LA FENETRE D'AMPLITUDE. Elle couvre les N bougies qui PRECEDENT celle en
    #  cours : elle est mise a jour en fin de tour, jamais avant les decisions.
    #  Une fenetre qui inclurait la bougie courante lirait son haut et son bas
    #  avant d'y poser des ordres, ce qui est du regard vers l'avenir a l'echelle
    #  d'une bougie — assez pour elargir l'echelle juste avant la meche qui la
    #  remplit. Le prechauffage joue le meme role que celui de la moyenne : sans
    #  lui, la fenetre grandirait de 1 a N au debut de chaque bloc et chaque N
    #  se comporterait differemment la ou justement on les compare.
    n_vol = reglages.fenetre_vol if reglages.echelle_vol else 0
    fen_vol: deque[float] = deque(
        (list(prechauffe_vol)[-n_vol:] if (n_vol and prechauffe_vol) else []),
        maxlen=n_vol or 1)
    somme_vol = sum(fen_vol)
    vol = somme_vol / len(fen_vol) if fen_vol else 0.0
    d = Descente(symbole, ref, budget, reglages, plafond=bougies[0][1],
                 volatilite=vol)
    #  La fenetre glissante des clotures, amorcee par la reference de depart : a
    #  la premiere bougie il n'existe aucune cloture precedente, et une moyenne
    #  sur rien n'a pas de valeur. La somme est tenue a jour plutot que recalculee
    #  — une moyenne sur huit mille six cents bougies recalculee a chaque pas
    #  ferait du rejeu un quadratique.
    #  LE PRECHAUFFAGE. Une moyenne sur N bougies exige N bougies AVANT la
    #  premiere. Sans elle, la fenetre grandit de 1 a N pendant les premiers
    #  pas et chaque N se comporte differemment au debut de chaque bloc — un
    #  artefact qui vaudrait vingt-huit jours sur cent pour la plus longue.
    #  L'appelant fournit donc les clotures qui precedent ; a defaut, la fenetre
    #  est amorcee par la reference de depart, ce qui est l'ancien comportement.
    n_moy = reglages.moyenne_ref
    amorce = list(prechauffe)[-n_moy:] if prechauffe else [ref]
    fen_ref: deque[float] = deque(amorce, maxlen=n_moy)
    somme_ref = sum(fen_ref)
    cash = budget
    cycles: list[Cycle] = []
    equity: list[tuple[int, float]] = []
    deploiement: list[tuple[int, float, float, int, bool]] = []
    journal: list[Evenement] = []
    suivi: list[tuple[int, float, float, float, float]] = []
    abandons = 0
    pic, creux, somme_eng, heures_eng = float("-inf"), 0.0, 0.0, 0

    cliquet = reglages.cliquet_pas is not None
    #  L'ETAT DES TROIS DECLENCHEURS. Tout est decide a la cloture PRECEDENTE :
    #  autorise_achat vaut True tant qu'aucun declencheur n'est actif, si bien
    #  que le moteur sans declencheur ne paie pas un test de plus par barreau.
    n_dev = reglages.devi_fenetre if reglages.devi_chute else 0
    n_moy_f = reglages.filtre_moyenne
    actifs = bool(n_dev or n_moy_f or reglages.confirmation)
    #  Un maximum glissant par deque monotone : sans elle, refaire max() sur
    #  576 bougies a chaque pas rendrait le rejeu quadratique — le meme piege
    #  que la moyenne de reference, deja evite une fois.
    haut_mono: deque[tuple[int, float]] = deque()
    fen_flt: deque[float] = deque(maxlen=n_moy_f or 1)
    somme_flt = 0.0
    hausses = 0
    prec = None
    autorise_achat = not actifs

    def avancer(k: int, cl: float) -> None:
        """Fait entrer une cloture dans les trois fenetres des declencheurs."""
        nonlocal somme_flt, hausses, prec
        if n_dev:
            while haut_mono and haut_mono[-1][1] <= cl:
                haut_mono.pop()
            haut_mono.append((k, cl))
            while haut_mono[0][0] <= k - n_dev:
                haut_mono.popleft()
        if n_moy_f:
            if len(fen_flt) == n_moy_f:
                somme_flt -= fen_flt[0]
            fen_flt.append(cl)
            somme_flt += cl
        hausses = hausses + 1 if (prec is not None and cl > prec) else 0
        prec = cl

    def decider() -> bool:
        """Les trois conditions, lues sur l'etat des fenetres. Toutes doivent passer."""
        if n_dev and prec is not None and prec > haut_mono[0][1] * (1 - reglages.devi_chute):
            return False                     # (b) la chute exigee n'a pas eu lieu
        if n_moy_f and len(fen_flt) == n_moy_f:
            m = somme_flt / n_moy_f
            if (prec > m) if reglages.filtre_sens == "sous" else (prec < m):
                return False                 # (c) mauvais cote de la moyenne
        if hausses < reglages.confirmation:
            return False                     # (e) le rebond n'est pas confirme
        return True

    #  LE PRECHAUFFAGE DES DECLENCHEURS. Sans lui, une moyenne mobile de sept
    #  jours reste sans avis sur les 2 016 premieres bougies de chaque tranche —
    #  dix-sept pour cent d'un bloc de quarante jours — et un maximum glissant
    #  se calcule sur une fenetre qui grandit. Chaque valeur de fenetre se
    #  comporterait donc differemment au DEBUT de chaque bloc, precisement la ou
    #  on les compare. Le depot a deja paye cette erreur sur moyenne_ref ; les
    #  clotures qui precedent sont fournies par l'appelant et servent aux trois
    #  fenetres a la fois. Les indices sont negatifs, donc la fenetre glissante
    #  expulse l'amorce exactement quand il le faut.
    if actifs and prechauffe:
        amorce = list(prechauffe)[-max(n_dev, n_moy_f, 1):]
        for j, x in enumerate(amorce):
            avancer(j - len(amorce), x)
        autorise_achat = decider()

    for idx, (ts, o, h, l, c, *extra) in enumerate(bougies):
        ferme = False
        #  CE QUE CETTE BOUGIE POUVAIT ENCORE ABSORBER, en euros. Un compteur et
        #  non un test par ordre : plusieurs barreaux peuvent etre touches dans
        #  la meme bougie, et les tester un a un contre le volume ENTIER les
        #  autoriserait tous, chacun paraissant petit alors que leur somme
        #  depasse tout ce qui s'est echange. Infini quand le plafond est inerte
        #  ou que la serie ne porte pas de volume : le moteur est alors celui
        #  d'avant, sans un test de plus dans la boucle chaude.
        capacite = (reglages.participation_max * extra[0] * c
                    if (reglages.participation_max and extra) else float("inf"))

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
            d = Descente(symbole, c, budget, reglages, plafond=c, volatilite=vol)
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
                if (not cliquet and d.cumul_unites and h >= d.prix_sortie
                        and not trop_tot
                        and d.cumul_unites * d.prix_sortie <= capacite):
                    #  La vente limite est plafonnee comme les achats : elle dort
                    #  au carnet exactement comme eux, et le lot entier est le
                    #  plus gros ordre de toute la strategie — la somme de tous
                    #  les barreaux remplis. C'est donc lui qui souffre le plus
                    #  du carnet mince, et le plafonner est ce qui rend
                    #  l'encadrement honnete des deux cotes.
                    px = d.prix_sortie
                    # lire l'ouverture AVANT de vendre : vendre() remet la descente a zero
                    ouvert = ts if d.ouverte_le is None else d.ouverte_le
                    ref_cycle = d.reference
                    det = d.vendre(px)
                    capacite -= det["brut"]
                    cash += det["recu"]
                    cycles.append(Cycle(
                        symbole, ouvert, ts, det["paliers"], det["investi"],
                        det["recu"], det["gain"], det["gain_pct"], det["prix_revient"], px,
                        (ts - ouvert) / 3_600_000, "limite", 0, ref_cycle,
                    ))
                    if trace:
                        journal.append(Evenement(ts, "vente", px, -1, det["recu"],
                                                 det["prix_revient"], det["gain"]))
                    #  on repart du prix du moment
                    d = Descente(symbole, c, budget, reglages, plafond=c,
                                 volatilite=vol)
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
                for i, prix, euros in (d.barreaux_a_poser() if autorise_achat else ()):
                    #  Un barreau trop gros pour ce qui s'echange n'est pas
                    #  servi : il reste au carnet, et la bougie suivante le
                    #  reproposera. Refuse en entier plutot que servi en partie
                    #  — une execution partielle demanderait de tenir un reste
                    #  par barreau, donc un autre moteur ; et ce refus est le
                    #  cote SEVERE de l'encadrement, celui qui minore.
                    if l <= prix and cash >= euros - 1e-9 and euros <= capacite:
                        d.acheter(i, prix, ts)
                        cash -= euros
                        capacite -= euros
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
        #  L'ANCRE : la cloture du moment, ou la moyenne des N dernieres.
        #  Elle inclut la bougie courante, exactement comme la cloture qu'elle
        #  remplace : aucune information future n'entre ici.
        if n_moy > 1:
            if len(fen_ref) == n_moy:
                somme_ref -= fen_ref[0]
            fen_ref.append(c)
            somme_ref += c
            ancre = somme_ref / len(fen_ref)
        else:
            ancre = c

        if (reglages.suivre_hausse and not d.engagee
                and ancre > d.reference * (1 + reglages.reancrage_min)):
            #  Une echelle qui remonte repart a neuf : son abandon n'a plus de
            #  raison d'etre fige sur une ancre que le marche a laissee derriere.
            d = Descente(symbole, ancre, budget, reglages, plafond=c, volatilite=vol)
        elif (reglages.suivre_baisse and not d.engagee
                and ancre < d.reference * (1 - reglages.reancrage_min)):
            #  L'ABANDON SUIT L'ANCRE D'ORIGINE, PAS LA NOUVELLE. On transmet le
            #  seuil courant : il ne descend donc jamais, et la regle « sous le
            #  dernier barreau, on n'ajoute plus rien » finit par mordre. Sans
            #  cette transmission, chaque re-ancrage a la baisse emporterait son
            #  abandon vers le bas et le bot poursuivrait le prix sans fin.
            d = Descente(symbole, ancre, budget, reglages, plafond=c, volatilite=vol,
                         abandon_fige=d.seuil_abandon)

        #  LA SORTIE APRES N JOURS DE CAPITAL BLOQUE, la seule vente a perte
        #  autorisee. Elle est lue A LA CLOTURE et sort AU MARCHE : un lot qu'on
        #  solde parce qu'il dort depuis cent jours ne trouve pas un acheteur
        #  poli a son prix de revient, il traverse le carnet.
        if (reglages.sortie_jours and not ferme and d.cumul_unites
                and d.ouverte_le is not None
                and ts - d.ouverte_le >= reglages.sortie_jours * 86_400_000):
            sortir(c * (1 - reglages.glissement_stop), "delai")

        #  La fenetre avance MAINTENANT, decisions prises : la bougie qui vient
        #  d'etre jouee entre dans la volatilite de la suivante, et pas dans la
        #  sienne. Somme tenue a jour plutot que recalculee, pour la meme raison
        #  que la moyenne des clotures : une fenetre de huit mille bougies
        #  resommee a chaque pas ferait du rejeu un quadratique.
        if n_vol:
            if len(fen_vol) == n_vol:
                somme_vol -= fen_vol[0]
            a = (h - l) / c if c > 0 else 0.0
            fen_vol.append(a)
            somme_vol += a
            vol = somme_vol / len(fen_vol)

        #  LES TROIS DECLENCHEURS, decides a CETTE cloture pour la bougie
        #  SUIVANTE. C'est la meme regle que la fenetre de volatilite : un robot
        #  qui se reveille a chaque cloture ne sait rien de la bougie qu'il
        #  traverse. Les evaluer ici, apres les achats, est ce qui l'interdit.
        if actifs:
            avancer(idx, c)
            autorise_achat = decider()

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
