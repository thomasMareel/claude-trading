"""Le moteur de grille : arithmetique, execution, et le mode de defaillance.

Chaque test verifie une propriete que la strategie DOIT avoir, pas une
valeur observee. Un test qui passe ne dit pas que la strategie gagne, il
dit que le moteur calcule ce qu'il annonce.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from src.grille import Descente, GrilleError, Reglages, chemin_bougie, rejouer, resume  # noqa: E402

R = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02, frais=0.001, abandon_sous=0.15)
H = 3_600_000


def bougie(ts, o, h, l, c):
    return (ts, o, h, l, c)


# ------------------------------------------------------------------ echelle
def test_les_mises_sommes_au_budget_et_croissent():
    ech = R.echelle(reference=100.0, budget=1000.0)
    assert len(ech) == 5
    assert sum(e for _, e in ech) == pytest.approx(1000.0)
    mises = [e for _, e in ech]
    assert mises == sorted(mises), "les mises doivent croitre vers le bas"
    assert mises[-1] / mises[0] == pytest.approx(2.0 ** 4)


def test_les_prix_descendent_jusqu_a_la_profondeur_voulue():
    ech = R.echelle(100.0, 1000.0)
    prix = [p for p, _ in ech]
    assert prix[0] == pytest.approx(100.0)
    assert prix[-1] == pytest.approx(90.0)          # 10 % de profondeur
    ecarts = [prix[i] - prix[i + 1] for i in range(len(prix) - 1)]
    assert all(e == pytest.approx(ecarts[0]) for e in ecarts), "pas regulier"


def test_les_grosses_mises_en_bas_tirent_le_prix_de_revient_vers_le_bas():
    """C'est le coeur de la strategie : le revient doit finir bien plus pres
    du dernier palier que de la moyenne arithmetique des paliers."""
    d = Descente("BTC/EUR", 100.0, 1000.0, R)
    for i, (p, _) in enumerate(d.echelle):
        d.acheter(i, p)
    milieu = (100.0 + 90.0) / 2
    assert d.prix_revient < milieu
    assert d.prix_revient == pytest.approx(92.3, abs=0.3)


# ------------------------------------------------------------------ objectif net
@pytest.mark.parametrize("frais", [0.0, 0.001, 0.002, 0.004])
def test_la_vente_a_l_objectif_rend_exactement_le_gain_net_promis(frais):
    r = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02, frais=frais)
    d = Descente("BTC/EUR", 100.0, 1000.0, r)
    for i, (p, _) in enumerate(d.echelle):
        d.acheter(i, p)
    det = d.vendre(d.prix_sortie)
    assert det["gain_pct"] == pytest.approx(0.02, abs=1e-9), "2 % NET, quels que soient les frais"


def test_un_objectif_qui_ne_couvre_pas_les_frais_est_refuse():
    with pytest.raises(GrilleError, match="aller-retour"):
        Reglages(objectif_net=0.0015, frais=0.001)


def test_reglages_absurdes_refuses():
    for kw in ({"profondeur": 0}, {"profondeur": 1.5}, {"paliers": 1}, {"ratio": 0.9},
               {"objectif_net": 0}, {"frais": 0.9}):
        with pytest.raises(GrilleError):
            Reglages(**kw)


# ------------------------------------------------------------------ execution
def test_une_descente_complete_puis_un_rebond_boucle_un_cycle():
    b = [bougie(0, 100, 100, 89, 90)]                       # touche tous les paliers
    b.append(bougie(H, 90, 95, 90, 95))                     # rebondit : vente
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert len(r["cycles"]) == 1
    c = r["cycles"][0]
    assert c.paliers == 5
    assert c.gain_pct == pytest.approx(0.02, abs=1e-9)
    assert r["lot_restant"] == 0


def test_le_prix_de_sortie_baisse_a_mesure_que_l_on_achete_plus_bas():
    d = Descente("BTC/EUR", 100.0, 1000.0, R)
    sorties = []
    for i, (p, _) in enumerate(d.echelle):
        d.acheter(i, p)
        sorties.append(d.prix_sortie)
    assert sorties == sorted(sorties, reverse=True), "acheter plus bas doit rapprocher la sortie"
    assert sorties[-1] < sorties[0]


def test_un_ordre_limite_est_execute_a_son_prix_sans_glissement():
    b = [bougie(0, 100, 100, 97.4, 98)]      # ne touche que les 2 premiers paliers
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    d = r["descente_en_cours"]
    assert d.remplis == [0, 1]
    prix = [p for p, _ in d.echelle]
    attendu = sum(e for _, e in d.echelle[:2])
    assert d.cumul_euros == pytest.approx(attendu)
    unites = sum(e / prix[i] for i, (_, e) in enumerate(d.echelle[:2])) * (1 - R.frais)
    assert d.cumul_unites == pytest.approx(unites), "execute au prix du palier, pas au bas de la bougie"


def test_le_budget_borne_les_achats():
    """Une chute qui touche tous les paliers ne peut pas depenser plus que le budget."""
    b = [bougie(0, 100, 100, 50, 55)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert r["investi_bloque"] <= 1000.0 + 1e-9
    assert r["cash_final"] >= -1e-9


# ------------------------------------------------------------------ abandon
def test_sous_le_seuil_d_abandon_on_n_achete_plus_mais_on_peut_vendre():
    r0 = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
                  frais=0.001, abandon_sous=0.05)
    d = Descente("BTC/EUR", 100.0, 1000.0, r0)
    assert d.dernier_palier == pytest.approx(90.0)
    assert d.seuil_abandon == pytest.approx(85.5)
    b = [bougie(0, 100, 100, 84, 85)]                       # traverse le seuil
    r = rejouer("BTC/EUR", b, r0, 1000.0, reference=100.0)
    assert r["abandons"] == 1
    assert r["descente_en_cours"].abandonnee
    assert r["descente_en_cours"].barreaux_a_poser() == []   # plus aucun ordre pose


def test_le_mode_de_defaillance_est_reproduit_la_baisse_durable_bloque_tout():
    """La propriete la plus importante a tester : dans une baisse continue,
    la grille accumule, ne vend jamais, et immobilise le capital."""
    b = [bougie(i * H, 100 - i, 100 - i, 99 - i, 99 - i) for i in range(60)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    s = resume(r)
    assert s["cycles"] == 0, "aucune vente dans une baisse continue"
    assert s["bloque_pct"] > 0.9, "le capital est immobilise"
    assert s["drawdown_max"] < -0.2, "et il perd"
    assert r["abandons"] == 1


# ------------------------------------------------------------------ chemin de bougie
def test_le_chemin_dans_la_bougie_est_prudent():
    assert chemin_bougie(o=10, h=12, l=9, c=11) == ((9, False), (12, True))   # haussiere
    assert chemin_bougie(o=11, h=12, l=9, c=10) == ((12, True), (9, False))   # baissiere


def test_une_bougie_plate_garde_sa_jambe_basse():
    """Le defaut le plus couteux du moteur : la jambe etait identifiee par
    `extreme == h`, donc sur une bougie plate les deux jambes passaient pour
    montantes et aucun achat ne pouvait se declencher. 32 % des bougies de
    cinq minutes et 61 a 67 % de celles a la minute sont plates : plus on
    affinait le pas pour gagner en realisme, plus on perdait d'achats."""
    jambes = chemin_bougie(o=100, h=100, l=100, c=100)
    assert [m for _, m in jambes] == [False, True], "une jambe basse, une haute"

    b = [bougie(0, 100, 100, 100, 100)] * 1
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert r["descente_en_cours"].remplis == [0], "le barreau pose a 100 doit etre achete"


def test_une_suite_de_bougies_plates_declenche_bien_les_achats():
    #  echelle R : 100, 97.5, 95, 92.5, 90. Une descente plate de 100 a 95
    #  traverse donc exactement les trois premiers barreaux.
    b = [bougie(i * H, 100 - i, 100 - i, 100 - i, 100 - i) for i in range(6)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert r["descente_en_cours"].remplis == [0, 1, 2],         "une descente en marches plates doit remplir les barreaux traverses"


def test_une_bougie_baissiere_ne_permet_pas_de_vendre_apres_avoir_achete_plus_bas():
    """Convention defavorable assumee : sur une bougie baissiere le haut est
    visite AVANT le bas, donc l'achat du bas ne peut pas etre revendu dans la
    meme heure. Sans cela le backtest serait flatteur."""
    d0 = Descente("BTC/EUR", 100.0, 1000.0, R)
    d0.acheter(0, 100.0)
    sortie_initiale = d0.prix_sortie
    b = [bougie(0, 99, sortie_initiale + 1, 90, 91)]           # baissiere, touche haut et bas
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert r["cycles"] == []


# ------------------------------------------------------------------ resume
def test_le_resume_compte_ce_qui_sert_a_departager_deux_reglages():
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 95, 90, 95)]
    s = resume(rejouer("BTC/EUR", b, R, 1000.0, reference=100.0))
    for k in ("cycles", "gain_cumule", "perf_pct", "drawdown_max", "bloque_pct",
              "abandons", "duree_moyenne_h", "cycles_par_mois", "gain_moyen"):
        assert k in s
    assert s["cycles"] == 1
    assert s["gain_cumule"] == pytest.approx(1000.0 * 0.02, abs=1e-6)


def test_la_duree_d_un_cycle_est_reelle_et_non_nulle():
    """vendre() remet la descente a zero : lire ouverte_le APRES donnait
    systematiquement une duree de zero heure sur tous les cycles."""
    b = [bougie(0, 100, 100, 89, 90)]                       # achats a l'heure 0
    b += [bougie(i * H, 90, 91, 89, 90) for i in range(1, 10)]   # on patiente
    b.append(bougie(10 * H, 90, 99, 90, 99))               # rebond : vente a l'heure 10
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert len(r["cycles"]) == 1
    c = r["cycles"][0]
    assert c.ouvert_le == 0 and c.ferme_le == 10 * H
    assert c.heures == pytest.approx(10.0)


# ------------------------------------------------------------------ journal
def test_le_journal_raconte_exactement_ce_que_les_cycles_resument():
    """Un graphique se lit plus vite qu'un tableau, donc il ment plus vite.
    Chaque vente dessinee doit etre la vente comptee, au meme instant, au
    meme prix, pour le meme gain."""
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 95, 90, 95),
         bougie(2 * H, 95, 95, 84, 85), bougie(3 * H, 85, 99, 85, 99)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    ventes = [e for e in r["journal"] if e.genre == "vente"]
    assert len(ventes) == len(r["cycles"]) >= 2
    for e, c in zip(ventes, r["cycles"]):
        assert e.ts == c.ferme_le
        assert e.prix == pytest.approx(c.prix_sortie)
        assert e.gain == pytest.approx(c.gain)
        assert e.euros == pytest.approx(c.recu)
        assert e.revient == pytest.approx(c.prix_revient)


def test_chaque_achat_du_journal_correspond_a_un_euro_reellement_sorti():
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 95, 90, 95)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    achats = [e for e in r["journal"] if e.genre == "achat"]
    attendus = sum(c.paliers for c in r["cycles"]) + len(r["descente_en_cours"].remplis)
    assert len(achats) == attendus == 5
    assert sum(e.euros for e in achats) == pytest.approx(
        sum(c.investi for c in r["cycles"]) + r["investi_bloque"])
    assert [e.palier for e in achats] == [0, 1, 2, 3, 4], "du haut vers le bas"


def test_le_prix_de_revient_du_journal_descend_a_chaque_achat():
    """C'est le mecanisme meme de la strategie : il doit se voir a l'oeil."""
    b = [bougie(0, 100, 100, 89, 90)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    revients = [e.revient for e in r["journal"] if e.genre == "achat"]
    assert revients == sorted(revients, reverse=True)
    assert revients[-1] < revients[0]


def test_le_journal_est_chronologique():
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 95, 90, 95),
         bougie(2 * H, 95, 95, 84, 85), bougie(3 * H, 85, 99, 85, 99)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    ts = [e.ts for e in r["journal"]]
    assert ts == sorted(ts)


def test_l_abandon_est_inscrit_au_journal_une_seule_fois():
    r0 = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
                  frais=0.001, abandon_sous=0.05)
    b = [bougie(0, 100, 100, 84, 85), bougie(H, 85, 86, 83, 84)]
    r = rejouer("BTC/EUR", b, r0, 1000.0, reference=100.0)
    ab = [e for e in r["journal"] if e.genre == "abandon"]
    assert len(ab) == 1 == r["abandons"], "un abandon par descente, pas un par bougie"
    assert ab[0].prix == pytest.approx(85.5)


def test_le_suivi_donne_une_ligne_par_bougie_et_de_quoi_redessiner_l_echelle():
    """On n'exporte pas les 14 barreaux heure par heure : la reference suffit
    a les reconstruire, et divise le poids du fichier par autant."""
    b = [bougie(i * H, 100, 100, 99, 100) for i in range(5)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    assert len(r["suivi"]) == len(b) == len(r["equity"])
    ts, ref, revient, sortie, seuil = r["suivi"][0]
    assert ts == 0 and ref == pytest.approx(100.0)
    assert seuil == pytest.approx(90.0 * 0.85)
    assert [p for p, _ in R.echelle(ref, 1000.0)][-1] == pytest.approx(90.0)
    assert sortie == pytest.approx(revient * 1.02 / 0.999)


# ------------------------------------------------------------------ plancher
MIN = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
               frais=0.001, abandon_sous=0.15, mise_min=100.0)


def test_un_barreau_sous_le_minimum_n_est_jamais_pose():
    """Une plateforme refuse un ordre trop petit, elle ne l'agrandit pas.
    Le rejeu doit refuser de la meme facon, sinon il compte des operations
    qui n'auraient jamais eu lieu."""
    d = Descente("BTC/EUR", 100.0, 1000.0, MIN)
    mises = [e for _, e in d.echelle]                    # 32.3, 64.5, 129, 258, 516
    assert mises[0] < 100 < mises[2]
    assert d.barreaux_morts == [0, 1]
    assert [i for i, _, _ in d.barreaux_a_poser()] == [2, 3, 4]


def test_le_budget_des_barreaux_morts_reste_en_caisse():
    """On ne le redistribue pas : redistribuer changerait en douce l'echelle
    que l'on pretend tester."""
    b = [bougie(0, 100, 100, 89, 90)]
    r = rejouer("BTC/EUR", b, MIN, 1000.0, reference=100.0)
    d = r["descente_en_cours"]
    assert d.remplis == [2, 3, 4]
    ech = MIN.echelle(100.0, 1000.0)
    assert d.cumul_euros == pytest.approx(sum(e for _, e in ech[2:]))
    assert r["cash_final"] == pytest.approx(sum(e for _, e in ech[:2]))


def test_le_plancher_ne_change_rien_quand_il_est_a_zero():
    """Regression : le comportement par defaut doit rester celui du papier."""
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 95, 90, 95)]
    a = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    z = rejouer("BTC/EUR", b, Reglages(**{**R.__dict__, "mise_min": 0.0}), 1000.0, reference=100.0)
    assert resume(a) == resume(z)


def test_un_plancher_absurde_est_refuse():
    with pytest.raises(GrilleError, match="mise_min"):
        Reglages(mise_min=-1.0)


# ------------------------------------------------------------------ suivi de hausse
SUIT = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
                frais=0.001, suivre_hausse=True)
FIXE = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
                frais=0.001, suivre_hausse=False)


def test_sans_suivi_une_hausse_a_vide_endort_la_grille_pour_toujours():
    """Le defaut que le suivi corrige. Le cours part AU-DESSUS de l'echelle,
    donc rien n'est achete ; il monte de 30 %, puis retombe de 15 %. Sans
    suivi, l'echelle est restee en bas : la rechute ne la rejoint jamais."""
    b = [bougie(i * H, 110 + i, 111 + i, 109 + i, 110 + i) for i in range(30)]      # 110 -> 139
    b += [bougie((30 + i) * H, 139 - i, 140 - i, 138 - i, 139 - i) for i in range(19)]  # -> 121
    r = rejouer("BTC/EUR", b, FIXE, 1000.0, reference=100.0)
    assert r["descente_en_cours"].reference == pytest.approx(100.0)
    assert r["cycles"] == [] and not r["descente_en_cours"].remplis


def test_avec_suivi_la_reference_monte_avec_le_marche_et_la_grille_reste_vivante():
    b = [bougie(i * H, 110 + i, 111 + i, 109 + i, 110 + i) for i in range(30)]
    b += [bougie((30 + i) * H, 139 - i, 140 - i, 138 - i, 139 - i) for i in range(19)]
    r = rejouer("BTC/EUR", b, SUIT, 1000.0, reference=100.0)
    achats = [e for e in r["journal"] if e.genre == "achat"]
    assert achats, "la rechute qui suit doit declencher des achats"
    assert max(e.prix for e in achats) > 130.0, "l'echelle a bien suivi le marche"


def test_le_suivi_ne_bouge_jamais_l_echelle_sous_un_lot_deja_achete():
    """Deplacer la reference en portant un lot recalculerait les barreaux sous
    ses propres achats, donc rachterait les memes niveaux indefiniment."""
    b = [bougie(0, 100, 100, 99, 99),          # rempli le barreau 0 a 100
         bougie(H, 99, 120, 99, 120)]          # forte hausse, mais lot en main
    r = rejouer("BTC/EUR", b, SUIT, 1000.0, reference=100.0)
    d = r["descente_en_cours"]
    if d.engagee:
        assert d.reference == pytest.approx(100.0)


def test_le_suivi_ne_descend_jamais_la_reference():
    b = [bougie(0, 100, 100, 100, 100)] + [bougie(i * H, 95, 95, 95, 95) for i in range(1, 6)]
    r = rejouer("BTC/EUR", b, SUIT, 1000.0, reference=100.0)
    assert r["descente_en_cours"].reference >= 100.0 or r["descente_en_cours"].engagee


def test_le_suivi_est_desactive_par_defaut():
    """Regression : le comportement historique ne doit pas changer tout seul."""
    assert Reglages().suivre_hausse is False
    b = [bougie(i * H, 110 + i, 111 + i, 109 + i, 110 + i) for i in range(20)]
    defaut = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02, frais=0.001)
    assert resume(rejouer("BTC/EUR", b, defaut, 1000.0, reference=100.0)) ==            resume(rejouer("BTC/EUR", b, FIXE, 1000.0, reference=100.0))


# ------------------------------------------------------------- aller-retour intra-bougie
def test_l_aller_retour_dans_la_meme_bougie_peut_etre_interdit():
    """Une bougie haussiere permet d'acheter au bas puis de vendre au haut dans
    la meme heure. C'est indemontrable a partir d'une bougie horaire : il faut
    pouvoir mesurer le resultat sans ces cycles."""
    r0 = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02, frais=0.001)
    b = [bougie(0, 90, 120, 89, 119)]                  # haussiere : bas puis haut
    avec = rejouer("BTC/EUR", b, r0, 1000.0, reference=100.0)
    assert len(avec["cycles"]) == 1 and avec["cycles"][0].heures == 0.0

    sans = rejouer("BTC/EUR", b, Reglages(**{**r0.__dict__, "vente_meme_bougie": False}),
                   1000.0, reference=100.0)
    assert sans["cycles"] == [], "l'aller-retour instantane doit disparaitre"
    assert sans["descente_en_cours"].engagee, "mais le lot reste detenu, il n'est pas efface"


def test_interdire_l_aller_retour_ne_repousse_la_vente_que_d_une_bougie():
    r0 = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
                  frais=0.001, vente_meme_bougie=False)
    b = [bougie(0, 90, 120, 89, 100), bougie(H, 100, 120, 100, 119)]
    r = rejouer("BTC/EUR", b, r0, 1000.0, reference=100.0)
    assert len(r["cycles"]) == 1
    assert r["cycles"][0].ferme_le == H, "vendue a la bougie suivante, pas jamais"


def test_l_aller_retour_est_autorise_par_defaut():
    assert Reglages().vente_meme_bougie is True


# ------------------------------------------------------------- mode rapide
def test_le_mode_rapide_rend_exactement_le_meme_resume():
    """Un balayage sur des bougies a la minute ne peut pas garder six cent
    mille lignes par simulation. Il doit donc pouvoir s'en passer SANS que le
    resultat change d'un centime, sinon on comparerait deux mesures."""
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 95, 90, 95),
         bougie(2 * H, 95, 95, 84, 85), bougie(3 * H, 85, 99, 85, 99)]
    plein = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0, trace=True)
    creux = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0, trace=False)
    assert resume(plein) == resume(creux)
    assert plein["equity_finale"] == creux["equity_finale"]
    assert [c.gain for c in plein["cycles"]] == [c.gain for c in creux["cycles"]]


def test_le_mode_rapide_ne_garde_aucune_serie_par_bougie():
    b = [bougie(i * H, 100, 101, 99, 100) for i in range(50)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0, trace=False)
    assert r["equity"] == [] and r["deploiement"] == [] and r["journal"] == [] and r["suivi"] == []
    assert r["agregats"]["bougies"] == 50


def test_le_drawdown_calcule_au_fil_de_l_eau_vaut_celui_calcule_sur_la_serie():
    b = [bougie(0, 100, 100, 89, 90)] + \
        [bougie(i * H, 90 - i, 90 - i, 89 - i, 89 - i) for i in range(1, 40)]
    r = rejouer("BTC/EUR", b, R, 1000.0, reference=100.0)
    pic, dd = float("-inf"), 0.0
    for _, v in r["equity"]:
        pic = max(pic, v)
        if pic > 0:
            dd = min(dd, v / pic - 1)
    assert r["agregats"]["drawdown_max"] == pytest.approx(dd)


# ------------------------------------------------------------- reancrage minimal
def test_un_seuil_de_reancrage_empeche_de_courir_apres_le_bruit():
    """Sans seuil, l'echelle se recolle au prix a chaque bougie haussiere et ne
    laisse jamais un creux se former. depart_sous ecarte le premier barreau du
    prix, sans quoi il se remplirait des la premiere bougie et figerait tout."""
    colle = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02,
                     frais=0.001, depart_sous=0.03, suivre_hausse=True, reancrage_min=0.0)
    sourd = Reglages(**{**colle.__dict__, "reancrage_min": 0.05})
    #  derive de +2 % : au-dessus du seuil nul, sous le seuil de 5 %
    fin = [bougie(i * H, 100 + i * .01, 100.005 + i * .01, 99.995 + i * .01, 100 + i * .01)
           for i in range(200)]
    a = rejouer("BTC/EUR", fin, colle, 1000.0, reference=100.0)
    b = rejouer("BTC/EUR", fin, sourd, 1000.0, reference=100.0)
    assert not a["descente_en_cours"].engagee and not b["descente_en_cours"].engagee
    assert a["descente_en_cours"].reference == pytest.approx(101.99, abs=.02), "colle au prix"
    assert b["descente_en_cours"].reference == pytest.approx(100.0), "sous le seuil : ne bouge pas"


def test_le_seuil_de_reancrage_est_nul_par_defaut():
    assert Reglages().reancrage_min == 0.0
    with pytest.raises(GrilleError, match="reancrage_min"):
        Reglages(reancrage_min=-0.1)


# ------------------------------------------------------------- espacement
def test_les_deux_espacements_partagent_leurs_extremites():
    """Seule la repartition du milieu change : sans cela on comparerait deux
    echelles differentes et non deux facons de repartir la meme."""
    lin = Reglages(profondeur=0.40, paliers=9, ratio=1.3, objectif_net=0.02, frais=0.001)
    geo = Reglages(**{**lin.__dict__, "espacement": "geometrique"})
    pl = [p for p, _ in lin.echelle(100.0, 1000.0)]
    pg = [p for p, _ in geo.echelle(100.0, 1000.0)]
    assert pl[0] == pytest.approx(pg[0]) == pytest.approx(100.0)
    assert pl[-1] == pytest.approx(pg[-1]) == pytest.approx(60.0)
    assert pg[1:-1] != pytest.approx(pl[1:-1])


def test_l_espacement_geometrique_a_des_ecarts_constants_en_pourcentage():
    geo = Reglages(profondeur=0.40, paliers=9, ratio=1.3, objectif_net=0.02,
                   frais=0.001, espacement="geometrique")
    p = [x for x, _ in geo.echelle(100.0, 1000.0)]
    ratios = [p[i + 1] / p[i] for i in range(len(p) - 1)]
    assert all(r == pytest.approx(ratios[0]) for r in ratios)
    lin = Reglages(**{**geo.__dict__, "espacement": "lineaire"})
    q = [x for x, _ in lin.echelle(100.0, 1000.0)]
    ecarts = [q[i] - q[i + 1] for i in range(len(q) - 1)]
    assert all(e == pytest.approx(ecarts[0]) for e in ecarts), "le lineaire, lui, est constant en euros"


def test_un_espacement_inconnu_est_refuse():
    with pytest.raises(GrilleError, match="espacement"):
        Reglages(espacement="logarithmique")


# ------------------------------------------------------------- objectif variable
def test_sans_objectif_profond_la_cible_ne_bouge_pas():
    r = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.03, frais=0.001)
    assert [r.objectif_a(k) for k in range(6)] == [0.03] * 6


def test_l_objectif_se_relache_a_mesure_que_la_descente_s_enfonce():
    r = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.03,
                 objectif_profond=0.01, frais=0.001)
    vus = [r.objectif_a(k) for k in range(1, 6)]
    assert vus[0] == pytest.approx(0.03), "un seul barreau : objectif plein"
    assert vus[-1] == pytest.approx(0.01), "echelle pleine : objectif du fond"
    assert vus == sorted(vus, reverse=True)


def test_un_objectif_relache_fait_sortir_plus_tot_et_rend_moins():
    """Le compromis a mesurer : on recycle le capital plus vite, mais chaque
    cycle profond rapporte moins."""
    fixe = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.03, frais=0.001)
    lache = Reglages(**{**fixe.__dict__, "objectif_profond": 0.01})
    d1 = Descente("BTC/EUR", 100.0, 1000.0, fixe)
    d2 = Descente("BTC/EUR", 100.0, 1000.0, lache)
    for i, (p, _) in enumerate(d1.echelle):
        d1.acheter(i, p); d2.acheter(i, p)
    assert d2.prix_sortie < d1.prix_sortie
    assert d2.vendre(d2.prix_sortie)["gain_pct"] == pytest.approx(0.01)
    assert d1.vendre(d1.prix_sortie)["gain_pct"] == pytest.approx(0.03)


def test_un_objectif_profond_sous_les_frais_est_refuse():
    with pytest.raises(GrilleError, match="objectif_profond"):
        Reglages(objectif_net=0.03, objectif_profond=0.0015, frais=0.001)


# ================================================================== cliquet
#  L'objectif cesse d'etre une sortie et devient un plancher : au seuil, un stop
#  se pose juste dessous, et il remonte a chaque cran de rentabilite franchi.
CLIQ = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02, frais=0.001,
                cliquet_pas=0.01, cliquet_retrait=0.002,
                frais_taker=0.0015, glissement_stop=0.0005)


def descente_amorcee(r=CLIQ):
    """Une descente avec le premier barreau achete a 100."""
    d = Descente("BTC/EUR", 100.0, 1000.0, r)
    d.acheter(0, 100.0, 0)
    return d


def test_le_cliquet_est_inerte_par_defaut():
    """Le filet des tests existants est le seul instrument qui distingue une
    regression d'un changement voulu : le defaut ne doit rien changer."""
    assert Reglages().cliquet_pas is None
    b = [bougie(0, 100, 100, 89, 90), bougie(H, 90, 99, 90, 99)]
    sans = Reglages(profondeur=0.10, paliers=5, ratio=2.0, objectif_net=0.02, frais=0.001)
    a = rejouer("BTC/EUR", b, sans, 1000.0, reference=100.0)
    assert resume(a) == resume(rejouer("BTC/EUR", b, R, 1000.0, reference=100.0))
    assert all(c.sortie == "limite" and c.crans == 0 for c in a["cycles"])


def test_sous_le_seuil_le_stop_ne_s_arme_pas():
    d = descente_amorcee()
    assert d.armer_ou_monter(101.0, H) is False
    assert d.stop == 0.0 and d.arme_le is None


def test_au_seuil_le_stop_se_pose_juste_dessous_au_lieu_de_vendre():
    d = descente_amorcee()
    seuil = d.prix_pour(0.02)
    assert d.armer_ou_monter(seuil, H) is True
    assert d.crans == 0 and d.arme_le == H
    assert d.rentabilite(d.stop) == pytest.approx(0.02 - 0.002)


def test_le_stop_monte_d_un_cran_par_pas_franchi_et_jamais_ne_descend():
    d = descente_amorcee()
    d.armer_ou_monter(d.prix_pour(0.02), H)
    hauts = [d.stop]
    for niveau in (0.03, 0.05, 0.041, 0.02):
        d.armer_ou_monter(d.prix_pour(niveau), H)
        hauts.append(d.stop)
    assert hauts == sorted(hauts), "un cliquet ne redescend jamais"
    assert d.crans == 3, "0.05 est trois pas au-dessus de 0.02"
    assert d.rentabilite(d.stop) == pytest.approx(0.05 - 0.002)


def test_une_seule_bougie_peut_monter_plusieurs_crans():
    """N'en autoriser qu'un ferait dependre le resultat de la finesse
    d'echantillonnage, defaut que ce moteur a deja paye cher."""
    d = descente_amorcee()
    d.armer_ou_monter(d.prix_pour(0.09), H)
    assert d.crans == 7 and d.rentabilite(d.stop) == pytest.approx(0.09 - 0.002)


def test_un_stop_arme_annule_tous_les_ordres_d_achat():
    d = descente_amorcee()
    assert d.barreaux_a_poser(), "avant armement, les barreaux sont poses"
    d.armer_ou_monter(d.prix_pour(0.02), H)
    assert d.barreaux_a_poser() == []


def test_le_stop_est_toujours_au_dessus_de_tout_barreau_libre():
    """C'est ce qui rend l'annulation des achats correcte et non arbitraire."""
    d = descente_amorcee()
    d.armer_ou_monter(d.prix_pour(0.02), H)
    libres = [p for i, (p, _) in enumerate(d.echelle) if i not in d.remplis]
    assert d.stop > max(libres)


def test_un_pas_enorme_donne_un_stop_fixe_qui_ne_monte_jamais():
    """Bras de controle : il isole ce que coute le passage a un ordre stop,
    pour que le gain du CLIQUET ne lui soit pas attribue par defaut."""
    fixe = Reglages(**{**CLIQ.__dict__, "cliquet_pas": 10.0})
    d = descente_amorcee(fixe)
    d.armer_ou_monter(d.prix_pour(0.02), H)
    depart = d.stop
    d.armer_ou_monter(d.prix_pour(0.50), H)
    assert d.stop == depart and d.crans == 0


def test_un_pas_nul_fait_suivre_le_prix_en_continu():
    suiveur = Reglages(**{**CLIQ.__dict__, "cliquet_pas": 0.0})
    d = descente_amorcee(suiveur)
    d.armer_ou_monter(d.prix_pour(0.02), H)
    d.armer_ou_monter(d.prix_pour(0.0345), H)
    assert d.rentabilite(d.stop) == pytest.approx(0.0345 - 0.002)


def test_une_sortie_au_stop_paie_le_tarif_taker_et_non_le_maker():
    d = descente_amorcee()
    maker = descente_amorcee().vendre(110.0)["recu"]
    taker = d.vendre(110.0, au_marche=True)["recu"]
    assert taker < maker
    assert taker == pytest.approx(maker * (1 - 0.0015) / (1 - 0.001))


def test_un_retrait_qui_rendrait_la_sortie_perdante_est_refuse():
    with pytest.raises(GrilleError, match="cliquet_retrait"):
        Reglages(objectif_net=0.02, cliquet_pas=0.01, cliquet_retrait=0.03)


def test_le_cliquet_capture_une_forte_remontee_que_la_vente_ferme_coupe():
    """Le but meme du mecanisme : ne pas sortir a 2 % quand le marche en offre 12."""
    b = [bougie(0, 100, 100, 99, 99)]                       # achat du barreau 0 a 100
    b += [bougie(i * H, 100 + i * 2, 100 + i * 2, 99 + i * 2, 100 + i * 2) for i in range(1, 8)]
    b.append(bougie(8 * H, 114, 114, 104, 104))             # rechute : le stop cede
    ferme = rejouer("BTC/EUR", b, Reglages(profondeur=0.10, paliers=5, ratio=2.0,
                                           objectif_net=0.02, frais=0.001), 1000.0, reference=100.0)
    cliq = rejouer("BTC/EUR", b, CLIQ, 1000.0, reference=100.0)
    assert len(ferme["cycles"]) == 1 and ferme["cycles"][0].gain_pct == pytest.approx(0.02)
    assert len(cliq["cycles"]) == 1
    c = cliq["cycles"][0]
    assert c.sortie == "stop" and c.crans >= 5
    assert c.gain_pct > 0.03, "bien au-dela des 2 % de la vente ferme"
    #  Mais nettement moins que le dernier cran atteint ne le promettait : le
    #  robot ne lit que les clotures, donc une bougie qui plonge de 114 a 104 le
    #  sort a 104 et non pres de son stop. C'est le cout du sondage, et il doit
    #  rester visible plutot que d'etre lisse par une hypothese d'execution.
    assert c.gain_pct < 0.02 + c.crans * CLIQ.cliquet_pas,         "le sondage a la cloture rend une partie de la montee"


def test_le_cliquet_paie_le_retrait_quand_le_cours_retombe_aussitot():
    """Le cout certain du mecanisme, et il doit se voir : au seuil sans suite,
    on encaisse moins que la vente ferme."""
    b = [bougie(0, 100, 100, 99, 99),
         bougie(H, 99, 103, 99, 102.4),                     # arme au-dessus du seuil
         bougie(2 * H, 102.4, 102.4, 100, 100)]             # retombe : le stop cede
    cliq = rejouer("BTC/EUR", b, CLIQ, 1000.0, reference=100.0)
    assert len(cliq["cycles"]) == 1
    c = cliq["cycles"][0]
    assert c.sortie == "stop" and c.crans == 0
    assert c.gain_pct < 0.02, "moins que l'objectif de la vente ferme"


def test_le_journal_inscrit_chaque_deplacement_du_stop():
    b = [bougie(0, 100, 100, 99, 99)]
    b += [bougie(i * H, 100 + i * 2, 100 + i * 2, 99 + i * 2, 100 + i * 2) for i in range(1, 6)]
    r = rejouer("BTC/EUR", b, CLIQ, 1000.0, reference=100.0)
    cliquets = [e for e in r["journal"] if e.genre == "cliquet"]
    assert len(cliquets) >= 2
    assert [e.prix for e in cliquets] == sorted(e.prix for e in cliquets)
    assert [e.palier for e in cliquets] == sorted(e.palier for e in cliquets)


# ------------------------------------------------------------- espacement en puissance
def test_une_courbure_de_un_redonne_exactement_le_lineaire():
    """Le defaut doit rester inerte : sans cela, ajouter le mode changerait en
    silence tous les reglages deja mesures."""
    assert Reglages().courbure == 1.0
    lin = Reglages(profondeur=0.10, paliers=8, ratio=1.5, objectif_net=0.02,
                   depart_sous=0.02, frais=0.001)
    pui = Reglages(**{**lin.__dict__, "espacement": "puissance", "courbure": 1.0})
    a = [p for p, _ in lin.echelle(100.0, 1000.0)]
    b = [p for p, _ in pui.echelle(100.0, 1000.0)]
    assert a == pytest.approx(b)


def test_une_courbure_superieure_a_un_resserre_les_barreaux_en_haut():
    """C'est ce que reclame la distribution des chutes : 45 % s'arretent a -1 %,
    1,8 % a -10 %. Des barreaux equidistants gaspillent la moitie de l'echelle."""
    pui = Reglages(profondeur=0.10, paliers=8, ratio=1.5, objectif_net=0.02,
                   depart_sous=0.02, frais=0.001, espacement="puissance", courbure=2.0)
    p = [x for x, _ in pui.echelle(100.0, 1000.0)]
    ecarts = [p[i] - p[i + 1] for i in range(len(p) - 1)]
    assert ecarts == sorted(ecarts), "les ecarts doivent s'elargir vers le bas"
    assert ecarts[-1] > 3 * ecarts[0]


def test_la_courbure_ne_deplace_ni_le_premier_ni_le_dernier_barreau():
    for k in (1.0, 1.5, 2.5, 4.0):
        r = Reglages(profondeur=0.10, paliers=8, ratio=1.5, objectif_net=0.02,
                     depart_sous=0.02, frais=0.001, espacement="puissance", courbure=k)
        p = [x for x, _ in r.echelle(100.0, 1000.0)]
        assert p[0] == pytest.approx(98.0)
        assert p[-1] == pytest.approx(88.0)


def test_une_courbure_absurde_est_refusee():
    with pytest.raises(GrilleError, match="courbure"):
        Reglages(espacement="puissance", courbure=0)
