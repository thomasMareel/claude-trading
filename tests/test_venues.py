"""La couche qui rend le code independant de la plateforme.

Le defaut corrige ici etait silencieux : l'identifiant d'ordre client etait
ecrit pour Binance, donc ignore ailleurs, donc la protection anti-doublon
apres coupure reseau ne protegeait plus rien sans que rien ne le signale.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ccxt  # noqa: E402
import pytest  # noqa: E402

from src import venues  # noqa: E402


def test_chaque_plateforme_decrite_existe_dans_ccxt_avec_les_memes_identifiants():
    for v in venues.VENUES.values():
        assert hasattr(ccxt, v.id), f"{v.id} absent de ccxt"
        x = getattr(ccxt, v.id)()
        requis = tuple(k for k, need in x.requiredCredentials.items() if need)
        assert set(v.credentials) == set(requis), f"{v.nom} : credentials {v.credentials} != {requis}"


def test_okx_europe_exige_une_phrase_secrete_et_pointe_sur_le_domaine_europeen():
    v = venues.get("myokx")
    assert "password" in v.credentials          # la phrase secrete, oubliee par les autres
    assert v.hote == "eea.okx.com"
    assert getattr(ccxt, "myokx")().hostname == v.hote
    assert v.env_names() == {
        "apiKey": "OKX_API_KEY", "secret": "OKX_API_SECRET", "password": "OKX_API_PASSPHRASE",
    }


def test_identifiant_deterministe_et_stable():
    v = venues.get("myokx")
    tag = "20260906T160130Z-B-BTC/EUR"
    assert v.client_order_id(tag) == v.client_order_id(tag)      # rejouable
    assert v.client_order_id(tag) != v.client_order_id(tag.replace("-B-", "-S-"))


def test_okx_refuse_les_tirets_la_ou_binance_les_accepte():
    tag = "20260906T160130Z-B-BTC/EUR"
    okx = venues.get("myokx").client_order_id(tag)
    bnb = venues.get("binance").client_order_id(tag)
    assert okx.isalnum(), f"OKX n'accepte que lettres et chiffres, obtenu {okx!r}"
    assert "-" in bnb                                            # Binance les tolere
    assert "/" not in okx and "/" not in bnb


@pytest.mark.parametrize("vid", sorted(venues.VENUES))
def test_longueur_respectee_et_fin_du_tag_conservee(vid):
    v = venues.get(vid)
    tag = "20260906T160130Z-CYCLETRESLONG-B-BTC/EUR"
    cid = v.client_order_id(tag)
    assert 0 < len(cid) <= v.cid_max
    # on tronque par la gauche : le symbole et le sens, en fin de tag, survivent
    assert cid.endswith("BTCEUR")


def test_kraken_tronque_a_dix_huit_caracteres():
    assert len(venues.get("kraken").client_order_id("20260906T160130Z-B-BTC/EUR")) == 18


def test_un_tag_sans_aucun_caractere_valide_est_refuse():
    with pytest.raises(ValueError, match="vide"):
        venues.get("myokx").client_order_id("---///---")


def test_les_noms_de_parametres_different_bien_entre_plateformes():
    assert venues.get("binance").cid_pose == "newClientOrderId"
    assert venues.get("binance").cid_relecture == "origClientOrderId"
    assert venues.get("myokx").cid_pose == "clientOrderId"
    assert venues.get("myokx").cid_relecture == "clientOrderId"


def test_bitvavo_est_signale_comme_ne_rendant_pas_les_frais_reels():
    assert venues.get("bitvavo").frais_lisibles is False
    assert venues.get("myokx").frais_lisibles is True
    assert venues.get("kraken").frais_lisibles is True


def test_binance_porte_la_mention_de_son_retrait_europeen():
    assert "MiCA" in venues.get("binance").note


def test_plateforme_inconnue_donne_un_message_utile():
    with pytest.raises(KeyError, match="inconnue"):
        venues.get("ftx")
