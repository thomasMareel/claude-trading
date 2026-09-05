# Créer les clés : mode d'emploi pas à pas

Trois choses à mettre dans le fichier `.env` à la racine du projet. Une seule
est nécessaire pour démarrer le paper trading : la clé Claude. Les autres
viennent après.

| Étape | Clé | Nécessaire pour | Temps |
|---|---|---|---|
| 1 | `ANTHROPIC_API_KEY` | que Claude trade, dès maintenant | 10 min |
| 2 | `NTFY_TOPIC` | recevoir les alertes sur le téléphone | 5 min |
| 3 | `BINANCE_API_KEY` / `SECRET` | le passage en réel, plus tard | 15 min |
| 4 | `BINANCE_TESTNET_*` | roder les ordres réels sans argent, avant le 3 | 5 min |
| 5 | connexion `claude` | que le bouton « Demander à Claude » soit traité | 2 min |

Après chaque étape, une commande vérifie tout sans afficher les clés :

```bash
.venv\Scripts\python.exe scripts\verifier_cles.py
```

Je ne manipule jamais ces clés. Vous les créez, vous les collez, le fichier
`.env` est ignoré par git et ne quitte pas votre machine.

---

## 1. La clé Claude

### Ce que c'est

Un **compte API** chez Anthropic, distinct de l'abonnement Claude que vous
utilisez pour discuter. Il se recharge en crédits prépayés et se facture à
l'usage. Le bot fera environ six appels par jour.

| Poste | Estimation |
|---|---|
| Un appel (effort `medium`) | 0,05 à 0,10 $ |
| Par jour | 0,30 à 0,60 $ |
| Par mois | 10 à 18 $ |
| Crédit à charger pour commencer | 20 $ |

Pour réduire, passer `llm.effort` à `low` dans `config.yaml` divise le coût
par deux environ, au prix d'un raisonnement plus court.

### Les étapes

1. Aller sur **https://console.anthropic.com** et se connecter. Vous pouvez
   utiliser la même adresse mail que votre compte Claude, ce sera quand même
   un compte API séparé.
2. Dans les réglages, section **Billing** : acheter des crédits. 20 $
   suffisent pour le premier mois. Sans crédit, la clé existe mais chaque
   appel est refusé.
3. Toujours dans les réglages, section **Limits** : poser une **limite de
   dépense mensuelle**, par exemple 25 $. C'est votre ceinture de sécurité :
   même en cas de bug, la facture ne peut pas dépasser ce montant.
4. Section **API Keys** : cliquer **Create Key**, la nommer `crypto-claude`.
   La clé s'affiche **une seule fois**. La copier tout de suite.
5. Ouvrir `C:\Claude\Crypto\.env` avec le Bloc-notes et coller la clé :

   ```
   ANTHROPIC_API_KEY=sk-ant-api03-la-suite-de-votre-cle
   ```

   Pas d'espace, pas de guillemets.
6. Vérifier :

   ```bash
   .venv\Scripts\python.exe scripts\verifier_cles.py
   ```

   La ligne `1. Cle Claude` doit afficher `OK` avec le nom du modèle.
7. **Redémarrer le bot** pour qu'il lise la nouvelle clé : fermer la fenêtre
   noire si elle est ouverte, puis relancer `start_paper_detached.bat`. Le
   prochain cycle montrera le raisonnement de Claude dans le journal.

### Si la clé fuit

Retourner dans **API Keys**, supprimer la clé, en créer une autre, remplacer
dans `.env`, redémarrer le bot. Une clé volée permet de dépenser vos crédits,
rien d'autre : elle ne donne accès ni à votre compte Binance ni à vos
conversations.

---

## 2. Les alertes sur le téléphone

Le bot vous prévient quand un stop se déclenche, quand un objectif est
atteint, quand un cycle échoue, et surtout quand quelque chose de grave se
produit. Sans cela, un incident reste dans un fichier que personne ne lit.

On utilise **ntfy**, gratuit et sans compte.

1. Installer l'application **ntfy** sur votre téléphone (Play Store ou App
   Store, éditeur Philipp Heckel).
2. L'ouvrir, appuyer sur **+** pour s'abonner à un sujet. Le nom du sujet est
   le seul secret : toute personne qui le connaît peut lire vos alertes.
   Choisissez quelque chose de long et d'imprévisible, par exemple
   `crypto-claude-` suivi de douze caractères au hasard :

   ```
   crypto-claude-k8Qz2mW9pL4x
   ```

3. Coller ce même nom dans `.env` :

   ```
   NTFY_TOPIC=crypto-claude-k8Qz2mW9pL4x
   ```

4. Vérifier avec `scripts\verifier_cles.py` : une notification de test doit
   arriver sur le téléphone dans les secondes qui suivent.

---

## 3. Les clés Binance

**Pas maintenant.** Elles ne servent qu'au passage en réel, après la phase
de paper trading. Le bot en paper n'a besoin d'aucune clé Binance : les prix
sont publics.

Quand le moment viendra :

### Prérequis

- Un compte Binance vérifié (identité validée).
- La double authentification activée. Binance l'exige pour créer une clé.
- Le capital converti en **USDT sur le compte Spot** (pas le compte Funding).
  Acheter pour 100 € d'USDT depuis l'application suffit.

### Les étapes

1. Sur binance.com, menu du profil, **API Management**, puis **Create API**.
2. Choisir **System generated**, donner un nom : `crypto-claude`.
3. Passer les vérifications de sécurité.
4. Cliquer **Edit restrictions** et régler exactement ceci :

   | Option | Réglage | Pourquoi |
   |---|---|---|
   | Enable Reading | **cochée** | lire les soldes |
   | Enable Spot & Margin Trading | **cochée** | passer les ordres spot |
   | Enable Withdrawals | **JAMAIS** | une clé volée ne pourrait alors rien sortir du compte |
   | Enable Futures | décochée | le bot n'en fait pas |
   | Permits Universal Transfer | décochée | idem |

5. Dans **IP access restrictions**, choisir **Restrict access to trusted IPs
   only** et ajouter votre adresse IP publique. Pour la connaître, ouvrir
   https://ifconfig.me dans un navigateur. Si votre box change d'adresse, le
   bot s'arrêtera avec une erreur d'authentification claire : il suffira de
   mettre l'adresse à jour sur Binance.
6. Copier la **API Key** et la **Secret Key**. La seconde ne s'affiche qu'une
   fois. Coller dans `.env` :

   ```
   BINANCE_API_KEY=votre-cle
   BINANCE_API_SECRET=votre-secret
   ```

7. Vérifier avec `scripts\verifier_cles.py`. Le script confirme la connexion,
   le solde USDT, et **vérifie lui-même que les retraits sont désactivés**.
   S'il affiche `retraits ACTIVES`, retourner sur Binance immédiatement.

---

## 4. Le testnet Binance

Un bac à sable avec de la fausse monnaie, pour exécuter de vrais ordres sur
de vrais serveurs Binance sans risquer un centime. C'est l'étape qui valide
le chemin des ordres réels avant d'y mettre les 100 €.

1. Aller sur **https://testnet.binance.vision** et se connecter avec un
   compte GitHub (en créer un gratuitement si besoin).
2. Cliquer **Generate HMAC_SHA256 Key**, donner un nom, copier la clé et le
   secret.
3. Coller dans `.env` :

   ```
   BINANCE_TESTNET_API_KEY=...
   BINANCE_TESTNET_API_SECRET=...
   ```

4. Dans `config.yaml`, mettre `engine.use_testnet: true`.
5. Le testnet fournit des fonds fictifs automatiquement. Vérifier avec
   `scripts\verifier_cles.py`.

Le passage en réel proprement dit, avec le fichier `LIVE_ARMED`, est décrit
dans le `README.md`. Il ne doit pas être abrégé.

---

## 5. Le pont : que Claude traite vos demandes GitHub

Le bouton **Demander à Claude** de la page ouvre une demande sur GitHub. Pour
qu'elle soit traitée, Claude Code doit pouvoir tourner sur ce PC sans vous.
Ce n'est pas une clé à créer, seulement une connexion à rafraîchir.

1. Ouvrir un terminal dans `C:\Claude\Crypto` et lancer :

   ```bash
   claude
   ```

   Si Claude demande de se connecter, suivre le lien affiché avec votre compte
   Claude habituel, puis quitter avec `/exit`. La connexion reste valable
   plusieurs semaines. Quand elle expire, le pont l'écrit dans `logs\pont.log`
   et il suffit de refaire cette étape.
2. Vérifier que GitHub est connecté aussi :

   ```bash
   gh auth status
   ```

3. Lancer le pont une fois à la main pour voir :

   ```bash
   scripts\pont.bat
   ```

   Sans demande ouverte, il écrit « aucune demande » et s'arrête. Avec une
   demande, Claude répond dans le fil GitHub et ouvre au besoin une pull
   request que vous validez d'un clic.
4. Pour qu'il tourne seul, une tâche planifiée Windows toutes les heures
   suffit : Planificateur de tâches, **Créer une tâche de base**, déclencheur
   quotidien répété toutes les heures, action **Démarrer un programme** avec
   `C:\Claude\Crypto\scripts\pont.bat`. Le pont ne fait rien quand il n'y a
   rien à traiter.

Le pont travaille dans une copie de travail séparée : il ne peut pas gêner le
bot. Il ne passe jamais d'ordre, ne lit jamais `.env`, et toute modification
de configuration ou de mandat est proposée pour la fenêtre suivante, jamais
appliquée à la fenêtre en cours.

**Alternative sans PC allumé** : une routine Claude dans le cloud peut faire
le même travail toutes les heures. Elle exige d'installer l'application GitHub
de Claude sur le dépôt, depuis https://claude.ai/code/onboarding?magic=github-app-setup.
Une fois fait, demandez dans Claude Code de créer la routine.
