# Architecture technique AWS — Plateforme de détection d'anomalies commerciales

Projet HES-SO CloudSys · groupe 16 · `cldsys-trade_anomaly`

Ce document décrit la mise en place des services AWS : ce qui est déployé, comment
(fichiers de configuration), et par quel processus. Il ne traite pas la logique du code
applicatif (voir le code source et `DEPLOY.md` pour cet aspect) mais l'infrastructure qui
l'exécute.

## 1. Vue d'ensemble des services

| Domaine | Service AWS | Rôle |
|---|---|---|
| Calcul | AWS Lambda (5 fonctions) | Ingestion, API, orchestration du rapport (Explain, PDF) |
| Orchestration | AWS Step Functions (Standard) | Enchaîne les deux étapes du rapport IA |
| Planification | Amazon EventBridge Scheduler | Déclenche l'ingestion chaque semaine |
| Données | Amazon DynamoDB | Table unique des écarts précalculés (mode à la demande) |
| Stockage objet | Amazon S3 (2 buckets) | Réponse brute Comtrade (reprise), rapports PDF |
| API | Amazon API Gateway (HTTP API) | 3 routes, CORS, autorisateur JWT |
| Identité | Amazon Cognito (User Pool) | Comptes créés par admin, émission des jetons JWT |
| IA générative | Amazon Bedrock (Claude Haiku 5.5) | Rédaction de l'explication structurée |
| Secrets | AWS Systems Manager Parameter Store | Clé API UN Comtrade (`SecureString`) |
| Hébergement front-end | AWS Amplify Hosting | Build + CDN du dashboard React, CI/CD sur `git push` |
| Infra as code | AWS SAM (CloudFormation) | Déploiement reproductible de tout le backend |
| Observabilité | Amazon CloudWatch Logs + historique Step Functions | Journaux, retry, relecture (Redrive) |

Tout le backend est **serverless** : aucune ressource n'est allumée en permanence, tout est
facturé à l'usage (invocations Lambda, lecture/écriture DynamoDB en PAY_PER_REQUEST, stockage
S3 au Go). Il n'y a ni VPC, ni cluster, ni conteneur à administrer.

## 2. Infrastructure as Code : `backend/template.yaml`

Le template SAM (`AWSTemplateFormatVersion: 2010-09-09`, `Transform: AWS::Serverless-2016-10-31`)
décrit l'intégralité du backend en une seule pile CloudFormation nommée par `samconfig.toml`
(`group16-trade-anomaly`). Chaque ressource est préfixée par `${AWS::StackName}`, de sorte que
plusieurs groupes peuvent déployer la même plateforme dans le même compte sans collision de noms.

### 2.1 Paramètres (`Parameters`)

| Paramètre | Défaut | Rôle |
|---|---|---|
| `ComtradeKeyParameterName` | `group16-trade-anomaly/comtrade-key` | Nom (sans `/` initial) du paramètre SSM qui contient la clé UN Comtrade |
| `ModelId` | `global.anthropic.claude-haiku-5-5` | Identifiant du modèle Claude invoqué sur Bedrock (profil d'inférence global) |
| `AllowedOrigins` | `http://localhost:5173` | Liste d'origines CORS autorisées par l'API (complétée avec le domaine Amplify après le premier déploiement du front) |
| `IngestionSchedule` | `cron(0 6 ? * MON *)` | Expression EventBridge du rechargement hebdomadaire |

Ces paramètres permettent de changer de modèle, d'environnement ou de domaine front sans
modifier le template : `sam deploy` suffit, avec de nouveaux `--parameter-overrides`.

### 2.2 Valeurs globales (`Globals.Function`)

Toutes les fonctions héritent par défaut de `Runtime: python3.14`, `Handler: app.handler`,
`Timeout: 30`, `MemorySize: 512` et de la variable d'environnement `GAPS_TABLE`. Chaque fonction
ne redéfinit que ce qui diffère (timeout, mémoire, variables supplémentaires), ce qui garde le
template court et évite les incohérences entre fonctions.

### 2.3 Stockage

- **`GapsTable`** (DynamoDB, `PAY_PER_REQUEST`) : clé composite `pk` (HASH) / `sk` (RANGE), tous
  deux de type chaîne. C'est la seule base de données de la plateforme.
  - Écarts : `pk = "{exporter}#{hs4}#{freq}"`, `sk = "{period}#{partner}"`.
  - Poids des partenaires (pour sélectionner les 10 premiers) : `pk = "{exporter}#{hs4}#WEIGHTS"`.
  - Pas d'index secondaire : chaque lecture de la heatmap est une seule `Query` sur `pk`.
- **`RawBucket`** (S3, `{stack}-raw`) : copie brute de la réponse Comtrade, afin de pouvoir
  reprendre le calcul des écarts sans rappeler l'API externe en cas d'échec. Vidé automatiquement
  dès que l'ingestion réussit.
- **`ReportsBucket`** (S3, `{stack}-reports`) : les PDF générés, sous la clé
  `reports/{reportId}.pdf`. Les deux buckets ont les quatre options de blocage d'accès public
  activées (`PublicAccessBlockConfiguration`) : aucun objet n'est jamais public, les PDF sont
  servis par URL pré-signée (`generate_presigned_url`, 900 s).

### 2.4 Ingestion

**`IngestionFunction`** (mémoire 2048 Mo, timeout 900 s — le maximum Lambda) :
- Appelle l'API publique UN Comtrade, écrit la réponse brute dans `RawBucket`, calcule les
  écarts (règle du mirror-matching) et les écrit dans `GapsTable`.
- Politiques : `DynamoDBCrudPolicy`, `S3CrudPolicy` (bucket brut), `SSMParameterReadPolicy`
  (lecture de la clé Comtrade uniquement — pas d'accès SSM générique).
- `EventInvokeConfig.MaximumRetryAttempts: 2` : en invocation asynchrone, Lambda relance
  automatiquement en cas d'échec.
- Déclenchement : événement `ScheduleV2` (EventBridge Scheduler), expression `IngestionSchedule`
  — chaque lundi 06:00 UTC par défaut. Aucun déclenchement par dépôt de fichier : les données
  proviennent d'une API externe interrogée directement, pas d'un fichier déposé dans S3.

### 2.5 Rapport par IA : `ExplainFunction`, `PdfFunction`, `ReportStateMachine`

**`ExplainFunction`** (mémoire 1024 Mo, timeout 300 s) réunit en une seule fonction la collecte
du contexte (historique DynamoDB, pages Wikipédia, jusqu'à ~400 Ko de texte) et l'appel au
modèle : ce choix évite de faire transiter les pages Wikipédia par l'état Step Functions, limité
à 256 Ko de charge utile — seule la sortie structurée du modèle (`Explanation`, validée par
Pydantic) quitte la fonction.
- Politiques : `DynamoDBReadPolicy` (lecture seule) et une politique IAM en ligne autorisant
  `bedrock:InvokeModel` sur `"*"` (un profil d'inférence global peut router l'appel vers
  n'importe quelle région Bedrock).

**`PdfFunction`** (mémoire 1024 Mo, timeout 120 s) construit le graphique (Matplotlib) et le PDF
(fpdf2), puis l'écrit dans `ReportsBucket` via `S3WritePolicy`.

**`ReportStateMachine`** (`AWS::Serverless::StateMachine`, type `STANDARD` — nécessaire car les
noms d'exécution ne sont uniques que pour ce type) orchestre les deux fonctions, définies dans
`backend/statemachine/report.asl.json` :

```
Explain  --(Retry: erreurs Lambda, 3 tentatives, backoff x2 ;
             States.TaskFailed, 2 tentatives)-->  BuildPdf  --(Retry: erreurs Lambda, 3 tentatives)--> End
```

Les ARN des deux fonctions sont injectés par `DefinitionSubstitutions` au moment du déploiement
(`${ExplainFunctionArn}`, `${PdfFunctionArn}`), ce qui évite de les coder en dur dans le JSON de
la machine à états.

### 2.6 API et authentification

- **`UserPool`** (Cognito) : connexion par e-mail, `AllowAdminCreateUserOnly: true` (pas
  d'auto-inscription — les comptes sont créés par un administrateur via la CLI), mot de passe
  d'au moins 12 caractères.
- **`UserPoolClient`** : sans secret (`GenerateSecret: false`, adapté à une SPA), flux
  `ALLOW_USER_SRP_AUTH`, `ALLOW_USER_PASSWORD_AUTH`, `ALLOW_REFRESH_TOKEN_AUTH`.
- **`HttpApi`** (API Gateway, protocole HTTP, moins cher et plus simple qu'une REST API) :
  CORS limité à `AllowedOrigins`, méthodes `GET/POST/OPTIONS`, en-têtes `authorization` et
  `content-type`. Un autorisateur JWT (`CognitoAuthorizer`) est défini par défaut sur toutes les
  routes : le jeton est vérifié par API Gateway lui-même, avant toute invocation Lambda — une
  requête non authentifiée ne coûte donc ni Lambda, ni Step Functions, ni Bedrock.
- **`ApiFunction`** : une seule fonction Lambda sert les trois routes (`GET /anomalies`,
  `POST /reports`, `GET /reports/{reportId}`), distinguées par `event["routeKey"]`.
  Politiques : lecture DynamoDB, lecture S3 (`ReportsBucket`), `StepFunctionsExecutionPolicy`
  (démarrer une exécution) et une politique en ligne pour `states:DescribeExecution`, restreinte
  à l'ARN de `ReportStateMachine`.

### 2.7 Sorties (`Outputs`)

`ApiUrl`, `UserPoolId`, `UserPoolClientId`, `IngestionFunctionName`, `ReportStateMachineArn`,
`GapsTableName`, `RawBucketName`, `ReportsBucketName` — récupérées par
`sam list stack-outputs` ou `aws cloudformation describe-stacks`, et consommées par le front-end
(`VITE_API_URL`, `VITE_USER_POOL_ID`, `VITE_USER_POOL_CLIENT_ID`) et par les opérations manuelles
(premier chargement, création d'utilisateur, nettoyage).

## 3. Fichiers de configuration

| Fichier | Rôle |
|---|---|
| `backend/template.yaml` | Infrastructure complète (ressources, politiques IAM, événements) |
| `backend/samconfig.toml` | Paramètres de build/déploiement SAM, **versionné** pour que toute l'équipe redéploie la même pile avec `sam deploy` seul |
| `backend/openapi.yaml` | Documentation de l'API (contrat), indépendante du déploiement |
| `backend/statemachine/report.asl.json` | Définition Amazon States Language du workflow de rapport |
| `backend/functions/*/requirements.txt` | Dépendances Python par fonction, installées par `sam build` dans `.aws-sam/` (non versionné) |
| `amplify.yml` | Paramètres de build Amplify Hosting pour le dossier `frontend/` |
| `frontend/.env.example` | Modèle des variables d'environnement Vite (`VITE_API_URL`, `VITE_USER_POOL_ID`, `VITE_USER_POOL_CLIENT_ID`) |
| `.gitignore` | Exclut `.aws-sam/`, `node_modules/`, `.env.local`, etc. |

### 3.1 `backend/samconfig.toml`

```toml
[default.global.parameters]
stack_name = "group16-trade-anomaly"
region = "us-east-1"

[default.deploy.parameters]
resolve_s3 = true
capabilities = "CAPABILITY_IAM"
confirm_changeset = true
parameter_overrides = "ComtradeKeyParameterName=\"...\" ModelId=\"...\" AllowedOrigins=\"...\" IngestionSchedule=\"...\""
```

`capabilities = CAPABILITY_IAM` autorise SAM à créer les rôles IAM de chaque fonction (un rôle
par fonction, avec uniquement les actions listées dans ses `Policies`). `resolve_s3 = true`
délègue à SAM la création/gestion du bucket de déploiement (artefacts de build). Modifier
`parameter_overrides` puis lancer `sam deploy` (sans `--guided`) suffit à faire évoluer la
configuration (changer de modèle Bedrock, ajouter un domaine Amplify aux origines CORS, etc.).

### 3.2 `amplify.yml`

```yaml
applications:
  - appRoot: frontend
    frontend:
      phases:
        preBuild: {commands: [nvm install 20, nvm use 20, npm ci]}
        build:    {commands: [npm run build]}
      artifacts: {baseDirectory: dist, files: ["**/*"]}
      cache:     {paths: [node_modules/**/*]}
```

Le dépôt est un monorepo (`My app is a monorepo`, racine `frontend/`) : Amplify Hosting
détecte ce fichier à la racine du dépôt et construit uniquement le dossier front-end. Les
variables `VITE_*` sont injectées comme variables d'environnement de l'application Amplify
(console, **Environment variables**), lues au moment du `npm run build` — une modification
nécessite donc un nouveau build (**Redeploy this version**), pas seulement un redémarrage.

### 3.3 Secret hors SAM : la clé UN Comtrade

CloudFormation ne sait pas créer un paramètre `SecureString` (chiffré) avec une valeur fournie au
déploiement sans l'exposer en clair dans le template ou son historique. La clé est donc gérée
**hors SAM**, directement par l'opérateur :

```bash
aws ssm put-parameter --name /trade-anomaly/comtrade-key --type SecureString \
  --value "<clé>" --region us-east-1
```

`IngestionFunction` la lit via `SSMParameterReadPolicy`, strictement limitée au nom de ce
paramètre (pas d'accès SSM générique dans le rôle de la fonction).

## 4. Déploiement

### 4.1 Backend (AWS SAM)

```bash
cd backend
sam validate --lint
sam build                 # ou sam build --use-container si Python 3.14 est absent localement
sam deploy --guided       # une fois ; ensuite : sam deploy
```

`sam build` installe les dépendances de chaque fonction (SDK Anthropic pour `explain`,
Matplotlib + fpdf2 pour `pdf`) dans `.aws-sam/`. `sam deploy --guided` écrit les réponses dans
`backend/samconfig.toml` (versionné), de sorte que tout redéploiement ultérieur, par n'importe
quel membre de l'équipe, se réduit à `sam build && sam deploy`. Les mises à jour suivantes créent
un changeset CloudFormation (`confirm_changeset = true`), visible avant application.

Boucle de développement : `sam sync --stack-name <stack> --watch` resynchronise le code modifié
sans repasser par un changeset complet (à éviter sur un environnement de démonstration, car il
peut appliquer des changements directement).

### 4.2 Étapes manuelles (hors SAM)

1. **Clé UN Comtrade** dans Parameter Store (section 3.3) — jamais dans le dépôt.
2. **Première invocation de Claude sur Bedrock** depuis la console (Model catalog → Open in
   playground), par un utilisateur humain : un compte récent peut exiger le formulaire de cas
   d'usage Anthropic ou l'activation via AWS Marketplace avant qu'une Lambda ne puisse invoquer
   le modèle.
3. **Premier chargement des données** : invocation manuelle asynchrone de `IngestionFunction`
   (`aws lambda invoke --invocation-type Event`), pour ne pas attendre le premier lundi.
4. **Création des utilisateurs** Cognito (`admin-create-user` + `admin-set-user-password`) —
   l'auto-inscription est désactivée par conception.
5. **Alerte de coût** (`Billing and Cost Management → Budgets`), recommandée pour détecter toute
   dérive, notamment sur les appels au modèle.

### 4.3 Front-end (AWS Amplify Hosting)

1. Dépôt poussé sur GitHub ; Amplify s'y connecte directement (pas de build local à livrer).
2. Console Amplify → **Create new app** → GitHub → dépôt + branche `main` → case **monorepo**,
   dossier racine `frontend`. Amplify détecte `amplify.yml` automatiquement.
3. Variables d'environnement `VITE_API_URL`, `VITE_USER_POOL_ID`, `VITE_USER_POOL_CLIENT_ID`
   renseignées depuis les sorties de la pile SAM (section 2.7).
4. **Save and deploy** : build et déploiement initial ; domaine attribué du type
   `https://main.<id>.amplifyapp.com`.
5. Ce domaine est ajouté à `AllowedOrigins` dans `samconfig.toml`, puis `sam deploy` est relancé
   — sans cela, le navigateur reçoit une erreur CORS.
6. Chaque `git push` sur `main` redéploie ensuite le front-end automatiquement (CI/CD intégré à
   Amplify Hosting, sans pipeline à construire).

### 4.4 Développement local du front-end

```bash
cd frontend
cp .env.example .env.local   # ApiUrl, UserPoolId, UserPoolClientId
npm install
npm run dev                  # http://localhost:5173, déjà autorisé par défaut dans AllowedOrigins
```

### 4.5 Suppression

```bash
aws s3 rm "s3://<ReportsBucketName>" --recursive
aws s3 rm "s3://<RawBucketName>" --recursive
sam delete --stack-name group16-trade-anomaly
```

CloudFormation refuse de supprimer un bucket non vide ; les buckets doivent donc être vidés
avant `sam delete`. Le paramètre SSM et l'application Amplify, créés manuellement, se suppriment
manuellement.

## 5. Sécurité et permissions (IAM)

Chaque fonction Lambda a son **propre rôle**, généré par SAM à partir de ses seules `Policies`
déclarées (principe du moindre privilège) :

| Fonction | Permissions |
|---|---|
| `IngestionFunction` | CRUD sur `GapsTable` ; CRUD sur `RawBucket` ; lecture du paramètre SSM de la clé Comtrade uniquement |
| `ExplainFunction` | Lecture seule sur `GapsTable` ; `bedrock:InvokeModel` (`Resource: "*"`, nécessaire pour un profil d'inférence global) |
| `PdfFunction` | Écriture sur `ReportsBucket` uniquement |
| `ApiFunction` | Lecture `GapsTable` ; lecture `ReportsBucket` ; démarrage + lecture du statut de `ReportStateMachine` (ARN explicite, pas de `*`) |

Aucune fonction n'a de droits sur les ressources d'une autre que celles qu'elle utilise
réellement. Aucun secret (clé Comtrade, identifiants) n'est présent dans le code ou dans
CloudFormation : la clé est lue à l'exécution depuis SSM, et l'accès à Bedrock se fait par le
rôle IAM de la fonction (SigV4), sans clé API Anthropic.

Côté périmètre public : API Gateway vérifie le JWT Cognito avant toute invocation Lambda ; les
deux buckets S3 bloquent tout accès public ; les PDF ne sont accessibles que par URL pré-signée
de 15 minutes, générée après vérification du jeton.

## 6. Observabilité

Aucun service d'observabilité dédié n'a été ajouté : les briques managées suffisent.

- **Amazon CloudWatch Logs** : chaque fonction Lambda journalise automatiquement
  (`sam logs --stack-name <stack> -n <ApiFunction|ExplainFunction|...> --tail`).
- **Historique d'exécution Step Functions** (console) : chaque étape du rapport, ses entrées,
  sorties et erreurs sont consultables individuellement ; une exécution en échec peut être
  relancée depuis l'étape défaillante (**Redrive**).
- **Budgets AWS** : alerte e-mail au-delà d'un seuil mensuel fixé par l'équipe, en particulier
  pour surveiller le coût des appels à Bedrock.

## 7. Limites connues de l'infrastructure actuelle

- **Timeout Lambda de 15 minutes** : le rechargement complet (2019 → année courante) prend
  aujourd'hui 5 à 10 minutes ; au-delà d'environ 12 minutes, `IngestionFunction` devra découper
  la récupération par année (paramètre `years` déjà supporté par la fonction).
- **Quota UN Comtrade** : 500 appels/jour ; un chargement complet en consomme 16.
- **Pas de CDN dédié pour l'API** : seul le front-end bénéficie du CDN d'Amplify Hosting ;
  l'API HTTP est directement exposée par API Gateway (suffisant au trafic actuel).
- **Pas de rejeu automatique du modèle** : un refus ou une erreur de Bedrock fait échouer
  l'étape `Explain` sans modèle de repli ; l'échec est visible et peut être relancé manuellement.
