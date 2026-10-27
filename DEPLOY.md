# Déploiement de la plateforme

Ce guide déploie le backend avec AWS SAM, puis le front-end avec Amplify Hosting, et décrit les étapes qui restent manuelles. Comptez une heure la première fois, dont l'essentiel en attente (inscription Comtrade, formulaire de cas d'usage Anthropic si demandé, premier chargement).

| Étape | Outil | Section |
|---|---|---|
| Installer les outils et configurer le compte AWS | Manuel | 1, 2 |
| Clé UN Comtrade, accès à Claude sur Bedrock | Manuel | 3 |
| Tests locaux | `pytest` | 4 |
| DynamoDB, S3, Lambda, Step Functions, EventBridge Scheduler, API Gateway, Cognito | **AWS SAM** | 5 |
| Premier chargement, utilisateurs, front-end Amplify, alerte de coût | Manuel | 6 |
| Développement local du front-end | `npm` | 6.4 |

Contenu du dépôt :

```
amplify.yml                    paramètres de build pour Amplify Hosting
frontend/                      tableau de bord React (Vite) : configuration, heatmap, rapport
backend/
├── template.yaml              infrastructure complète (SAM)
├── openapi.yaml               documentation de l'API
├── statemachine/report.asl.json   workflow du rapport (Step Functions)
├── functions/
│   ├── ingestion/             API Comtrade -> S3 (brut) -> calcul des écarts -> DynamoDB
│   ├── api/                   GET /anomalies, POST /reports, GET /reports/{id}
│   ├── explain/               étape 1 du rapport : contexte (DynamoDB, 3 pages Wikipédia) et explication par Claude Haiku 5.5 sur Bedrock
│   └── pdf/                   étape 2 : graphique, sources et PDF dans S3
├── events/                    entrées de test pour les invocations manuelles
└── tests/                     tests unitaires : règle d'anomalie, contexte du rapport
```

---

## 1. Installer les outils

Toutes les commandes sont écrites pour Linux ou WSL (Ubuntu).

| Outil | Version | Rôle | Vérification |
|---|---|---|---|
| AWS CLI | 2.x | Parler à AWS depuis le terminal | `aws --version` |
| AWS SAM CLI | 1.x récent | Construire et déployer le template | `sam --version` |
| Python | 3.14 | Même version que les Lambda, pour `sam build` et les tests | `python3.14 --version` |
| Docker | optionnel | `sam build --use-container` si Python 3.14 manque localement | `docker --version` |
| Git | — | Dépôt partagé, utilisé aussi par Amplify | `git --version` |

**AWS CLI**, si elle n'est pas déjà installée :

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
```

**SAM CLI**, avec l'installateur officiel :

```bash
curl -L "https://github.com/aws/aws-sam-cli/releases/latest/download/aws-sam-cli-linux-x86_64.zip" -o sam.zip
unzip sam.zip -d sam-installation && sudo ./sam-installation/install
sam --version
```

À défaut de droits administrateur : `pipx install aws-sam-cli`.

**Python 3.14** n'est pas fourni par Ubuntu 24.04 (qui a la 3.12). Le plus simple est de l'installer avec uv, qui place `python3.14` dans `~/.local/bin`, où `sam build` le trouve :

```bash
uv python install 3.14
python3.14 --version
```

À défaut, installez Docker Desktop avec l'intégration WSL et ajoutez `--use-container` à chaque `sam build`.

Les Lambda tournent en Python 3.14 : c'est la version la plus récente proposée par Lambda en disponibilité générale, maintenue jusqu'en juin 2029. La 3.12 ne reçoit plus que des correctifs de sécurité et la 3.13 vient de quitter elle aussi la phase de correction de bogues. La 3.15 n'existe sur Lambda qu'en préversion, non couverte par le SLA.

---

## 2. Configurer le compte AWS

### 2.1 Identifiants

Avec un utilisateur IAM et des clés d'accès :

```bash
aws configure
# AWS Access Key ID:     ...
# AWS Secret Access Key: ...
# Default region name:   us-east-1
# Default output format: json
```

Avec AWS IAM Identity Center (SSO), utilisez plutôt `aws configure sso`, puis ajoutez `--profile <nom>` aux commandes ou `export AWS_PROFILE=<nom>`.

Vérification :

```bash
aws sts get-caller-identity
```

### 2.2 Région

Ce guide utilise **us-east-1 (Virginie du Nord)** : tous les services du template y existent et Claude y est disponible sur Bedrock. Utilisez la même région pour toute l'équipe et pour chaque commande.

### 2.3 Droits nécessaires

`sam deploy` crée des rôles IAM pour chaque fonction. L'utilisateur qui déploie doit donc pouvoir créer des rôles et des piles CloudFormation. Sur un compte de cours restreint (AWS Academy, Learner Lab), la création de rôles est souvent interdite : vérifiez-le avant toute chose, car le déploiement échouera sinon.

---

## 3. Préparatifs manuels

### 3.1 Clé de l'API UN Comtrade

Ce qui ne passe pas par SAM : CloudFormation ne sait pas créer un paramètre chiffré (`SecureString`), et la clé ne doit jamais se trouver dans le dépôt.

1. Créer un compte sur le [portail de UN Comtrade](https://comtradeplus.un.org/).
2. Dans **Mon comtrade Free**, aller dans **Mon portail API**.
3. Appuyer sur **Obtain an API key** et copier la **Primary key**.
4. Tester la clé :

```bash
curl -s -H "Ocp-Apim-Subscription-Key: <votre clé>" \
  "https://comtradeapi.un.org/data/v1/get/C/M/HS?reporterCode=32&period=202501&cmdCode=1001&flowCode=X&partnerCode=24" | head
```

La réponse doit contenir `"count":1` et la valeur 22998881.93 (Argentine vers Angola, blé, janvier 2025, identique au fichier de DNEXT).

5. La stocker dans Parameter Store :

```bash
aws ssm put-parameter \
  --name /group16-trade-anomaly/comtrade-key \
  --type SecureString \
  --value "<votre clé>" \
  --region us-east-1
```

Pour la remplacer plus tard, relancez la même commande avec `--overwrite`.

### 3.2 Accès à Claude sur Amazon Bedrock

Bedrock n'a plus de page d'activation des modèles : les modèles sont activés automatiquement dans le compte à leur première invocation. Deux cas peuvent toutefois bloquer la première utilisation de Claude :

- **Formulaire de cas d'usage Anthropic** : pour un premier usage des modèles Anthropic, Bedrock peut demander de décrire le cas d'usage avant de donner accès au modèle.
- **Modèle distribué par AWS Marketplace** : la première invocation doit alors être faite par un utilisateur disposant des droits AWS Marketplace ; le modèle est ensuite activé pour tout le compte, y compris pour le rôle de la Lambda.

Le plus simple est donc de faire cette première invocation vous-même, depuis la console, avec votre utilisateur et non avec la Lambda :

1. Console AWS, région us-east-1, service **Amazon Bedrock**, menu **Model catalog**.
2. Ouvrir **Claude Haiku 5.5**, puis **Open in playground**.
3. Envoyer un message court. Si Bedrock affiche le formulaire de cas d'usage, le remplir (par exemple : projet de cours HES-SO, explication d'anomalies dans des statistiques de commerce international) et renvoyer le message.
4. Une réponse de Claude confirme que le modèle est actif pour le compte.

Le contexte externe du rapport vient de l'API publique de Wikipédia, qui ne demande ni clé ni inscription : il n'y a rien à préparer de ce côté.

Le template appelle Claude par l'API InvokeModel de Bedrock, avec le profil d'inférence global `global.anthropic.claude-haiku-5-5` et l'action IAM `bedrock:InvokeModel`. Le profil global laisse Bedrock traiter la requête dans la région qui a de la capacité, sans surcoût ; le préfixe `us.` garderait le traitement aux États-Unis, pour 10 % de plus. Le nouveau point d'accès Messages de Bedrock (client `AnthropicBedrockMantle`, identifiant `anthropic.claude-haiku-5-5`) répond « The model does not exist » pour Haiku 5.5 sur notre compte, alors que le même modèle répond par InvokeModel : d'où ce choix. Pour changer de modèle, il suffit de modifier le paramètre `ModelId` au déploiement. Un administrateur du compte peut restreindre l'accès aux modèles par des politiques IAM ou des Service Control Policies : sur un compte de cours, c'est une cause possible de refus (voir section 8).

---

## 4. Tests locaux

Depuis la racine du dépôt :

```bash
uv venv --python 3.14 && source .venv/bin/activate
uv pip install pytest
python -m pytest backend/tests -q
```

Les tests ne font aucun appel réseau. Ils vérifient :

- **la règle d'anomalie** ([test_gaps.py](backend/tests/test_gaps.py)) : les exemples de la slide 7 du sujet, le seuil de 35 % exclusif, les valeurs nulles (FOB nul, CIF nul, les deux), l'agrégation trimestrielle (chaque côté cumule tous ses mois déclarés), les flux déclarés d'un seul côté (signalés, avec la valeur du pays qui a déclaré), les quantités nulles ou absentes, et le fait que les lignes d'export et d'import ne sont jamais additionnées ;
- **la conversion des données de l'API Comtrade** en lignes équivalentes à celles du fichier Excel de DNEXT (codes ISO3, mois AAAA-MM, quantité en tonnes) ;
- **le contexte du rapport** ([test_report_context.py](backend/tests/test_report_context.py)) : le choix de l'historique et des autres partenaires, la requête Wikipédia, le plafond de 400 000 caractères, et la poursuite du rapport quand Wikipédia ne répond pas (API simulée).

---

## 5. Déploiement avec SAM

### 5.1 Construire

```bash
cd backend
sam validate --lint
sam build                    # ou : sam build --use-container
```

`sam build` installe les dépendances de chaque fonction (le SDK Anthropic pour `explain`, matplotlib et fpdf2 pour `pdf`) dans `.aws-sam/`, qui n'est pas versionné.

### 5.2 Premier déploiement

```bash
sam deploy --guided
```

Réponses conseillées :

| Question | Réponse |
|---|---|
| Stack Name | Entrée (`group16-trade-anomaly`, lu dans `samconfig.toml`) |
| AWS Region | Entrée (`us-east-1`) |
| Parameter ComtradeKeyParameterName | Entrée (valeur par défaut `group16-trade-anomaly/comtrade-key`, le nom de l'étape 3.1 sans la barre initiale) |
| Parameter ModelId | Entrée (`global.anthropic.claude-haiku-5-5`) |
| Parameter AllowedOrigins | Entrée pour l'instant (`http://localhost:5173`), à compléter à l'étape 6.4 |
| Parameter IngestionSchedule | Entrée (chaque lundi à 06:00 UTC) |
| Confirm changes before deploy | `Y` |
| Allow SAM CLI IAM role creation | `Y` |
| Disable rollback | `N` |
| Save arguments to configuration file | `Y` |
| SAM configuration file / environment | Entrée (`samconfig.toml`, `default`) |

SAM affiche la liste des ressources à créer ; confirmez avec `y`. Le déploiement prend quelques minutes.

**Versionnez `backend/samconfig.toml`** : il contient les réponses ci-dessus, et permet à chacun de redéployer avec un simple `sam deploy`.

**Nommage.** Tout ce que le groupe crée commence par `group16-`, pour qu'on le reconnaisse dans la console d'un compte partagé. Le template nomme chaque ressource à partir du nom de la pile : gardez donc `group16-trade-anomaly`. Les rôles IAM générés par SAM (`group16-trade-anomaly-IngestionFunctionRole-…`) suivent la même règle. Seule exception : le bucket `aws-sam-cli-managed-default-…`, que SAM crée lui-même pour y déposer le code des fonctions.

### 5.3 Récupérer les sorties

```bash
sam list stack-outputs --stack-name group16-trade-anomaly
```

| Sortie | Usage |
|---|---|
| `ApiUrl` | URL de base de l'API, pour le front-end et les tests |
| `UserPoolId`, `UserPoolClientId` | Configuration de Cognito dans le front-end |
| `IngestionFunctionName` | Premier chargement (section 6.1) |
| `ReportStateMachineArn` | Suivi des rapports dans la console Step Functions |
| `GapsTableName`, `RawBucketName`, `ReportsBucketName` | Vérifications et nettoyage |

Pour la suite, gardez-les dans des variables :

```bash
out() { aws cloudformation describe-stacks --stack-name group16-trade-anomaly \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
API=$(out ApiUrl); POOL=$(out UserPoolId); CLIENT=$(out UserPoolClientId); INGEST=$(out IngestionFunctionName)
```

### 5.4 Ce que SAM a créé

| Ressource | Nom | Rôle |
|---|---|---|
| Table DynamoDB | `group16-trade-anomaly-gaps` | Écarts précalculés (`pk` = exportateur#produit#fréquence, `sk` = période#partenaire) et poids des partenaires par année |
| Bucket S3 « raw » | `group16-trade-anomaly-raw-<compte>` | Réponse brute de l'API, supprimée dès que les écarts sont écrits |
| Bucket S3 « reports » | `group16-trade-anomaly-reports-<compte>` | Rapports PDF, téléchargés par lien signé valable 15 minutes |
| Lambda d'ingestion + planification EventBridge Scheduler | `group16-trade-anomaly-ingestion`, `group16-trade-anomaly-weekly-ingestion` | Rechargement complet chaque semaine (2019 à l'année en cours) et recalcul de tous les écarts |
| Machine à états Step Functions (standard) et ses deux Lambda | `group16-trade-anomaly-report`, `group16-trade-anomaly-explain`, `group16-trade-anomaly-pdf` | Deux étapes : une Lambda réunit le contexte (historique, autres partenaires, trois pages Wikipédia) et fait rédiger l'explication par Claude, puis une Lambda produit le PDF ; le nom d'exécution est l'identifiant du rapport |
| Lambda de l'API | `group16-trade-anomaly-api` | Lecture de la heatmap, demande de rapport, statut du rapport |
| API HTTP API Gateway | `group16-trade-anomaly-api` | Trois routes, toutes protégées par un jeton Cognito |
| Groupe d'utilisateurs Cognito et son client | `group16-trade-anomaly-users`, `group16-trade-anomaly-frontend` | Comptes créés par un administrateur uniquement |

---

## 6. Étapes manuelles après le déploiement

### 6.1 Premier chargement des données

Chaque lundi, la planification recharge tout l'historique depuis 2019 et recalcule tous les écarts. Pour ne pas attendre le premier lundi, lancez la même opération à la main, en invocation asynchrone pour que Lambda relance automatiquement en cas d'erreur :

```bash
aws lambda invoke --function-name "$INGEST" --invocation-type Event \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' /dev/stdout
```

Suivi en direct :

```bash
sam logs --stack-name group16-trade-anomaly -n IngestionFunction --tail
```

La dernière ligne attendue ressemble à `{"years": [2019, …, 2026], "rows": …, "cells": …, "anomalies": …}`. Avec le périmètre de DNEXT, le calcul produit environ 230 000 cellules.

**Notez la durée affichée à la fin du journal** (`REPORT … Duration`). La limite d'une Lambda est de 15 minutes et le recalcul complet devrait en prendre entre 5 et 10. Au-delà de 12 minutes environ, la récupération devra être découpée par année avant que l'historique ne s'allonge. Pour un test rapide, une seule année peut être passée : `--payload '{"years": [2025]}'`.

En cas d'échec, la réponse brute reste dans le bucket « raw » et les relances la relisent au lieu de rappeler l'API.

### 6.2 Créer un utilisateur

```bash
aws cognito-idp admin-create-user --user-pool-id "$POOL" \
  --username prenom.nom@hes-so.ch --user-attributes Name=email,Value=prenom.nom@hes-so.ch Name=email_verified,Value=true \
  --message-action SUPPRESS
aws cognito-idp admin-set-user-password --user-pool-id "$POOL" \
  --username prenom.nom@hes-so.ch --password '<au moins 12 caractères>' --permanent
```

### 6.3 Tester l'API de bout en bout

Obtenir un jeton :

```bash
TOKEN=$(aws cognito-idp initiate-auth --client-id "$CLIENT" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=prenom.nom@hes-so.ch,PASSWORD='<mot de passe>' \
  --query AuthenticationResult.IdToken --output text)
```

Heatmap du maïs américain, par trimestre :

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$API/anomalies?exporter=USA&hs4=1005&freq=Q" | head -c 600
```

Demander le rapport d'une cellule rouge (blé américain vers le Canada, mars 2025), puis relancer la seconde commande jusqu'à obtenir le statut `generated`. Seule une anomalie a un rapport, y compris un flux déclaré d'un seul côté (cellule rouge hachurée) : pour une cellule cohérente, l'API répond 400, et 404 pour une cellule absente.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d @events/report-request.json "$API/reports"
curl -s -H "Authorization: Bearer $TOKEN" "$API/reports/USA-CAN-1001-M-2025-03"
```

Une fois le statut à `generated`, le champ `downloadUrl` ouvre le PDF dans un navigateur. Sa section « Sources » liste les pages Wikipédia consultées, et les numéros entre crochets du texte y renvoient. Le journal de l'étape d'explication indique combien de pages ont été lues (`sam logs --stack-name group16-trade-anomaly -n ExplainFunction`). Un statut `failed` renvoie l'erreur et sa cause ; le détail de chaque étape se lit dans la console Step Functions, où l'exécution peut aussi être relancée depuis l'étape en échec (bouton **Redrive**).

### 6.4 Front-end

Le dossier `frontend/` contient le tableau de bord de la slide 8 du sujet : choix de l'exportateur, du produit et de la fréquence, puis une heatmap des dix principaux partenaires par période. Un clic sur une cellule rouge lance la génération du rapport, affiche son statut puis le lien de téléchargement. La connexion passe par Cognito ; l'inscription est masquée, puisque les comptes sont créés par un administrateur (6.2).

#### En local

Il faut Node.js 20 ou plus récent (`node --version`).

```bash
cd frontend
cp .env.example .env.local     # puis y reporter ApiUrl, UserPoolId et UserPoolClientId (section 5.3)
npm install
npm run dev                    # http://localhost:5173
```

`http://localhost:5173` est déjà autorisé par défaut dans `AllowedOrigins`.

#### Sur Amplify Hosting

Le dépôt se connecte tel quel à Amplify : le fichier `amplify.yml` à la racine décrit le build du dossier `frontend/`.

1. Pousser le dépôt sur GitHub.
2. Console AWS, région us-east-1, **AWS Amplify**, **Create new app**, source **GitHub**, autoriser l'accès au dépôt.
3. Choisir le dépôt et la branche `main`, cocher **My app is a monorepo** et indiquer `frontend` comme dossier racine. Amplify détecte `amplify.yml` ; ne pas modifier les paramètres de build proposés. Dans **App name**, remplacer le nom du dépôt par `group16-trade-anomaly-frontend`.
4. Dans **Advanced settings**, **Environment variables**, ajouter les sorties de la section 5.3 :

| Variable | Valeur |
|---|---|
| `VITE_API_URL` | `ApiUrl` |
| `VITE_USER_POOL_ID` | `UserPoolId` |
| `VITE_USER_POOL_CLIENT_ID` | `UserPoolClientId` |

5. **Save and deploy**, puis noter le domaine attribué, du type `https://main.d1abcd2efgh3.amplifyapp.com`.
6. Autoriser ce domaine dans l'API : dans `backend/samconfig.toml`, compléter la ligne `parameter_overrides` avec
   `AllowedOrigins=\"http://localhost:5173,https://main.d1abcd2efgh3.amplifyapp.com\"`, puis :

```bash
cd backend && sam deploy
```

Chaque `git push` sur `main` redéploie ensuite le front-end automatiquement. Les variables `VITE_*` sont lues au moment du build : après les avoir modifiées dans la console, relancer un déploiement (**Redeploy this version**).

### 6.5 Alerte de coût (conseillée)

Console AWS, **Billing and Cost Management**, **Budgets**, **Create budget**, modèle **Monthly cost budget**, nommé `group16-trade-anomaly-budget`, avec un montant bas (par exemple 10 USD) et votre adresse e-mail. Vous serez prévenus avant toute surprise, notamment sur les appels au modèle.

---

## 7. Mettre à jour et supprimer

| Action | Commande |
|---|---|
| Redéployer après une modification | `cd backend && sam build && sam deploy` |
| Synchroniser en continu pendant le développement | `sam sync --stack-name group16-trade-anomaly --watch` (à ne pas utiliser sur l'environnement de démonstration) |
| Relancer le recalcul complet sans attendre lundi | `aws lambda invoke --function-name "$INGEST" --invocation-type Event --cli-binary-format raw-in-base64-out --payload '{}' /dev/stdout` (pas pendant que le recalcul du lundi tourne) |
| Tout supprimer | Vider les deux buckets, puis `sam delete --stack-name group16-trade-anomaly` |

Vider un bucket : `aws s3 rm "s3://$(out ReportsBucketName)" --recursive`, et de même pour `RawBucketName`. CloudFormation refuse de supprimer un bucket qui contient encore des fichiers. Le paramètre de la clé Comtrade et l'application Amplify, créés à la main, se suppriment aussi à la main.

---

## 8. Dépannage

| Symptôme | Cause probable | Correction |
|---|---|---|
| `sam deploy` échoue sur `iam:CreateRole` | Compte sans droit de créer des rôles | Voir 2.3, demander un autre compte ou les droits |
| Ingestion : `ParameterNotFound` | Clé absente ou mauvais nom | Refaire 3.1 dans la bonne région ; le nom du paramètre commence par `/` |
| Ingestion : erreur 401 de Comtrade | Clé invalide ou abonnement « comtrade - v1 » manquant | Vérifier la clé avec le `curl` de 3.1 |
| Ingestion : erreur 429 répétée | Quota de 500 appels par jour atteint | Attendre le lendemain ; un chargement complet n'en consomme que 16 |
| Ingestion : `Task timed out after 900 seconds` | Le recalcul complet dépasse 15 minutes | Découper la récupération par année (voir 6.1) |
| Rapport `failed` à l'étape Explain avec `AccessDenied` | Modèle jamais invoqué depuis la console (formulaire Anthropic ou activation Marketplace en attente), ou accès restreint par une politique IAM ou SCP du compte | Refaire 3.2 avec votre utilisateur ; sur un compte de cours, demander à l'administrateur si Bedrock est autorisé |
| Rapport `failed` à l'étape Explain avec `NotFoundError` « The model 'anthropic.claude-haiku-5-5' does not exist » | Ancien identifiant, appelé par le point d'accès Messages (Mantle), qui ne sert pas Haiku 5.5 sur ce compte | Redéployer avec `ModelId=global.anthropic.claude-haiku-5-5` (valeur par défaut du template et de `samconfig.toml`) |
| Rapport `failed` à l'étape Explain avec `ValidationException` « on-demand throughput isn't supported » | Identifiant du modèle passé sans préfixe de profil d'inférence | Utiliser `global.` ou `us.` devant l'identifiant |
| Rapport `failed` avec `… is not an anomaly` | Workflow lancé directement (console, CLI) sur une cellule cohérente ; l'API refuse déjà ces demandes | Choisir une cellule rouge, par exemple celle de `events/report-request.json` |
| Rapport `failed` avec `No cell for …` | Cellule absente de DynamoDB (période ou partenaire erroné) | Vérifier la requête avec `GET /anomalies` |
| PDF : « Aucune page Wikipédia n'a pu être consultée » | Wikipédia injoignable, ou limite de débit des appels anonymes (erreur 429) atteinte malgré trois tentatives | Rien à corriger : le rapport repose alors sur les seules données. Relancer la demande plus tard pour obtenir les sources |
| API : `401 Unauthorized` | Jeton expiré (1 heure) ou d'un autre groupe d'utilisateurs | Refaire `initiate-auth` |
| Navigateur : erreur CORS | Domaine du front-end absent de `AllowedOrigins` | Voir 6.4, étape 6 |
| Amplify : page blanche après déploiement | Variables `VITE_*` absentes au moment du build | Les ajouter (6.4, étape 4) puis redéployer |
| Front-end : « User does not exist » | Compte non créé, ou créé dans un autre groupe d'utilisateurs | Refaire 6.2 avec le `UserPoolId` de la pile |
| Comment voir les journaux d'une fonction | — | `sam logs --stack-name group16-trade-anomaly -n <ApiFunction / ExplainFunction / …> --tail` |
