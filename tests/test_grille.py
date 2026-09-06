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
    assert chemin_bougie(o=10, h=12, l=9, c=11) == (9, 12)      # haussiere : bas puis haut
    assert chemin_bougie(o=11, h=12, l=9, c=10) == (12, 9)      # baissiere : haut puis bas


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
