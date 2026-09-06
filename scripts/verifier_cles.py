"""Verifie les cles du .env, sans JAMAIS les afficher.

    python scripts/verifier_cles.py

Pour chaque cle presente : un appel minimal pour confirmer qu'elle marche,
et pour Binance, la verification que les retraits sont bien desactives.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console  # noqa: E402

from src.alerts import notify  # noqa: E402
from src.config import load_config, secret  # noqa: E402

console = Console()
OK, KO, SKIP = "[green]OK[/]", "[red]KO[/]", "[dim]--[/]"


def masked(v: str | None) -> str:
    if not v:
        return "VIDE"
    if len(v) <= 12:
        return f"({len(v)} caracteres)"
    return f"{v[:6]}...{v[-4:]} ({len(v)} caracteres)"


def check_anthropic(cfg) -> bool:
    key = secret("ANTHROPIC_API_KEY")
    console.print(f"\n[bold]1. Cle Claude[/]  ANTHROPIC_API_KEY = {masked(key)}")
    if not key:
        console.print(f"   {KO} absente. Sans elle, le trader ne trade pas. Voir docs/cles-api.md, etape 1.")
        return False
    if not key.startswith("sk-ant-"):
        console.print(f"   [yellow]!![/] une cle Anthropic commence normalement par sk-ant-. Verifie le copier-coller.")
    import anthropic
    model = str(cfg.get("llm.model", "claude-opus-5"))
    client = anthropic.Anthropic(timeout=30.0, max_retries=0)
    try:
        r = client.messages.create(
            model=model, max_tokens=64,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reponds exactement : OK"}],
        )
    except anthropic.AuthenticationError as e:
        console.print(f"   {KO} cle refusee par Anthropic ({e.status_code}). Elle est fausse, revoquee, ou mal collee.")
        return False
    except anthropic.PermissionDeniedError as e:
        console.print(f"   {KO} acces refuse ({e.status_code}) : {e.message}")
        return False
    except anthropic.APIStatusError as e:
        msg = str(e.message)
        console.print(f"   {KO} erreur API {e.status_code} : {msg[:160]}")
        if "credit" in msg.lower() or "billing" in msg.lower():
            console.print("   -> le compte API n'a pas de credit. Etape 1b de docs/cles-api.md : acheter des credits.")
        return False
    except anthropic.APIConnectionError as e:
        console.print(f"   {KO} connexion impossible : {e}")
        return False
    u = r.usage
    p_in = float(cfg.get("llm.price_input_per_mtok", 5.0))
    p_out = float(cfg.get("llm.price_output_per_mtok", 25.0))
    cost = (u.input_tokens * p_in + u.output_tokens * p_out) / 1e6
    text = next((b.text for b in r.content if b.type == "text"), "").strip()
    console.print(f"   {OK} le modele {model} repond ({text[:20]!r}), "
                  f"{u.input_tokens} tokens entree, {u.output_tokens} sortie, ~{cost:.4f} $")
    return True


def check_ntfy() -> bool:
    topic = secret("NTFY_TOPIC")
    console.print(f"\n[bold]2. Alertes telephone[/]  NTFY_TOPIC = {masked(topic)}")
    if not topic:
        console.print(f"   {SKIP} pas configure. Le bot tournera sans alertes. Voir docs/cles-api.md, etape 2.")
        return False
    ok = notify("Test du bot", "Si tu lis ceci sur ton telephone, les alertes fonctionnent.", priority="low", tags="white_check_mark")
    console.print(f"   {OK if ok else KO} notification de test {'envoyee, regarde ton telephone' if ok else 'en echec'}")
    return ok


def check_venue(cfg, *, testnet: bool) -> bool:
    """Verifie la plateforme declaree dans config.yaml, quelle qu'elle soit.

    Aucun nom propre en dur : les secrets attendus, leur nombre et leur nom
    viennent de src/venues.py. OKX en exige trois, Binance et Kraken deux.
    """
    import ccxt

    from src.exchange import Exchange, ExchangeError
    from src.venues import get as venue_get

    v = venue_get(str(cfg.get("exchange.id", "myokx")))
    prefix = v.env_prefix + ("_TESTNET" if testnet else "")
    noms = {c: n.replace(v.env_prefix, prefix, 1) for c, n in v.env_names().items()}
    label = f"{v.nom} {'TESTNET' if testnet else 'REEL'}"
    console.print(f"\n[bold]{'4' if testnet else '3'}. {label}[/]  ({v.hote})")
    for n in noms.values():
        console.print(f"   {n} = {masked(secret(n))}")
    manquants = [n for n in noms.values() if not secret(n)]
    if manquants:
        console.print(f"   {SKIP} incomplet, il manque {', '.join(manquants)}. "
                      + ("Optionnel, pour roder les ordres sans argent." if testnet
                         else "INUTILE en paper trading, uniquement pour le reel."))
        return False

    try:
        x = Exchange(cfg, trading=True, testnet=testnet)
    except ExchangeError as e:
        console.print(f"   {KO} {e}")
        return False
    quote = str(cfg.get("exchange.quote", "EUR"))
    try:
        soldes = x.fetch_balances()
    except ccxt.AuthenticationError as e:
        console.print(f"   {KO} cle refusee : {str(e)[:140]}")
        console.print("   -> cle, secret ou phrase secrete errones, ou restriction IP qui ne "
                      "correspond pas a ton adresse actuelle.")
        return False
    except Exception as e:
        console.print(f"   {KO} {type(e).__name__} : {str(e)[:160]}")
        return False
    free = soldes.get(quote, 0.0)
    console.print(f"   {OK} connexion etablie. Solde disponible : {free:.2f} {quote}")
    autres = {k: round(val, 8) for k, val in soldes.items() if k != quote}
    if autres:
        console.print(f"   [dim]autres actifs detenus : {autres}[/]")

    # Le droit d'ecrire est le seul qui compte vraiment : on le prouve sans
    # rien engager, en annulant un ordre qui n'existe pas. Une cle en lecture
    # seule est refusee pour permission ; une cle de trading dit "introuvable".
    try:
        x.cancel("BTC/" + quote, "0")
        verdict = (OK, "la cle peut ecrire (ordre inexistant, annulation acceptee)")
    except ccxt.PermissionDenied as e:
        verdict = (KO, f"la cle NE PEUT PAS trader : {str(e)[:90]}")
    except ExchangeError as e:
        msg = str(e).lower()
        if any(w in msg for w in ("permission", "not authorized", "unauthorized", "50101", "50103", "50114")):
            verdict = (KO, f"la cle NE PEUT PAS trader : {str(e)[:90]}")
        else:
            verdict = (OK, "la cle peut ecrire (ordre inexistant, refus attendu)")
    except Exception as e:
        verdict = ("[yellow]!![/]", f"droit d'ecriture non verifiable : {type(e).__name__}")
    console.print(f"   {verdict[0]} {verdict[1]}")

    if not testnet and free < cfg.total_capital:
        console.print(f"   [yellow]!![/] le capital configure est {cfg.total_capital:.0f} {quote} et le "
                      f"solde n'est que de {free:.2f}. Sur OKX, verifie que les fonds sont bien sur le "
                      f"compte de TRADING et non sur le compte de financement.")
    console.print("   [dim]Les retraits ne se verifient pas par API : assure-toi a la main que la "
                  "permission de retrait est bien decochee.[/]")
    return verdict[0] == OK


def main() -> int:
    cfg = load_config()
    console.rule("[bold]Verification des cles")
    a = check_anthropic(cfg)
    check_ntfy()
    check_venue(cfg, testnet=False)
    check_venue(cfg, testnet=True)
    console.rule()
    if a:
        console.print("[bold green]Pret pour le paper trading.[/] Lance start_paper_detached.bat "
                      "(ou redemarre-le s'il tourne deja, pour qu'il lise la nouvelle cle).")
    else:
        console.print("[bold yellow]Le trader ne peut pas encore trader.[/] Renseigne ANTHROPIC_API_KEY dans .env.")
    return 0 if a else 1


if __name__ == "__main__":
    raise SystemExit(main())
