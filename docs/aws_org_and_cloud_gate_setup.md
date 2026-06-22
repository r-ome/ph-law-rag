# AWS Organization And Cloud Gate Notes

This records the AWS/Qdrant setup work done before the `ph-law-rag` cloud
runtime gate.

## Final Account Layout

```text
AWS Organization: Rome
├── Root
│   ├── Rome / 800601014452 / management account
│   ├── Archive
│   │   ├── cloud-resume-dev-archived / 184892497114
│   │   └── atc-monitoring-system / 974151821612
│   └── Workloads
│       └── ph-law-rag-dev / 719146260112
```

The separate client account stays outside this personal organization:

```text
client-atc-monitoring-system / 272296663494
```

## Final Local AWS Profiles

```text
rome-management
  -> 800601014452
  -> personal management account
  -> IAM Identity Center / SSO

ph-law-rag-dev
  -> 719146260112
  -> project workload account
  -> IAM Identity Center / SSO

cloud-resume-dev-archived
  -> 184892497114
  -> archived project account
  -> IAM Identity Center / SSO

client-atc-monitoring-system
  -> 272296663494
  -> separate client account
  -> static IAM key
```

`~/.aws/credentials` should only contain the client account profile. Personal AWS
access now uses SSO.

## Commands Used

### Inspect Local AWS Profiles

```bash
aws configure list-profiles
```

Lists local AWS CLI profiles configured in `~/.aws/config` and
`~/.aws/credentials`.

```bash
sed -n '1,220p' ~/.aws/config
```

Prints the local AWS CLI config file. This file contains profile names, SSO
settings, default regions, and output formats. It does not contain secret keys.

```bash
rg -n "^\[.*\]" ~/.aws/credentials
```

Lists only the profile headers in the AWS credentials file without printing
secret access keys.

### Verify Profile Identity

```bash
aws sts get-caller-identity --profile rome-management
```

Shows which AWS account and principal a profile is using. Used to verify
`rome-management` points to the management account.

```bash
aws sts get-caller-identity --profile ph-law-rag-dev
```

Verifies that the project profile points to the new workload account:
`719146260112`.

```bash
export AWS_PROFILE=ph-law-rag-dev
aws sts get-caller-identity
```

Sets the current terminal session to use `ph-law-rag-dev` by default and verifies
that unqualified AWS CLI commands use that account.

### Inspect AWS Organization

```bash
aws organizations describe-organization --profile rome-management
```

Shows the organization ID, management account ID, and enabled organization
features.

```bash
aws organizations list-accounts --profile rome-management
```

Lists all accounts in the organization.

```bash
aws organizations list-roots --profile rome-management
```

Gets the root ID for the organization. In this setup the root ID was:

```text
r-6p1t
```

```bash
aws organizations list-organizational-units-for-parent \
  --profile rome-management \
  --parent-id r-6p1t
```

Lists OUs directly under the organization root.

### Move Archived Accounts

```bash
aws organizations move-account \
  --profile rome-management \
  --account-id 184892497114 \
  --source-parent-id ou-6p1t-fcwvod4d \
  --destination-parent-id ou-6p1t-0j97yjzx
```

Moved the old cloud resume account into the `Archive` OU.

```bash
aws organizations move-account \
  --profile rome-management \
  --account-id 974151821612 \
  --source-parent-id r-6p1t \
  --destination-parent-id ou-6p1t-0j97yjzx
```

Moved the old `atc-monitoring-system` personal account into the `Archive` OU.

```bash
aws organizations delete-organizational-unit \
  --profile rome-management \
  --organizational-unit-id ou-6p1t-fcwvod4d
```

Deleted the now-empty old `CloudResumeDevOrganization` OU.

### Rename Archived Account

```bash
aws organizations enable-aws-service-access \
  --profile rome-management \
  --service-principal account.amazonaws.com
```

Enabled trusted access for AWS Account Management so the management account can
rename member accounts.

```bash
aws account put-account-name \
  --profile rome-management \
  --account-id 184892497114 \
  --account-name cloud-resume-dev-archived
```

Renamed the old `dev` account to `cloud-resume-dev-archived`.

```bash
aws account get-account-information \
  --profile rome-management \
  --account-id 184892497114
```

Verified the renamed account through the Account Management API.

### Create Project Account

```bash
aws organizations create-account \
  --profile rome-management \
  --email jerome.arceo.agapay+phlawragdev@gmail.com \
  --account-name ph-law-rag-dev \
  --role-name OrganizationAccountAccessRole
```

Started creation of a new AWS member account for this project.

```bash
aws organizations describe-create-account-status \
  --profile rome-management \
  --create-account-request-id car-7b87548da55f45ba80dc03a4f8ccb851
```

Polled the asynchronous account-creation request until it returned
`SUCCEEDED`. The new account ID is:

```text
719146260112
```

```bash
aws organizations move-account \
  --profile rome-management \
  --account-id 719146260112 \
  --source-parent-id r-6p1t \
  --destination-parent-id ou-6p1t-notvg5wl
```

Moved the new `ph-law-rag-dev` account from Root into the `Workloads` OU.

```bash
aws organizations list-accounts-for-parent \
  --profile rome-management \
  --parent-id ou-6p1t-notvg5wl
```

Verified that `ph-law-rag-dev` is under `Workloads`.

### Configure SSO Profiles

```bash
aws configure sso
```

Creates or updates an AWS CLI profile backed by IAM Identity Center / SSO. SSO
profiles use temporary browser-authenticated credentials, which are safer for
human CLI work than long-lived IAM access keys.

The `ph-law-rag-dev` profile was added manually to `~/.aws/config`:

```ini
[profile ph-law-rag-dev]
sso_session = rome
sso_account_id = 719146260112
sso_role_name = AdministratorAccess
region = us-east-1
output = json
```

```bash
aws sso login --profile ph-law-rag-dev
```

Logs into the SSO session for the `ph-law-rag-dev` CLI profile.

### Deactivate Legacy IAM Key

The old `iamadmin` static access key was deactivated in the AWS Console, then
deleted after SSO organization-admin commands were confirmed to work.

```bash
aws organizations list-accounts --profile rome-management
```

Confirmed SSO admin access works before removing the legacy IAM key.

```bash
aws sts get-caller-identity --profile legacy-iamadmin
```

Confirmed the legacy static key no longer works after deactivation.

## Qdrant Cloud

A Qdrant Cloud free-tier cluster was created. Qdrant Cloud is outside AWS and is
used as the managed vector store.

Runtime settings needed later:

```bash
export QDRANT_URL=https://YOUR_QDRANT_CLUSTER_URL:6333
export QDRANT_API_KEY=YOUR_QDRANT_API_KEY
export QDRANT_COLLECTION=ph_law-titan1024
```

`ph_law-titan1024` is a fresh collection name for Titan v2 embeddings so the app
does not accidentally reuse an old 768-dim local/Ollama collection.

## Bedrock Titan v2

The AWS Console showed that the old Bedrock Model Access page is retired for
serverless foundation models. Titan v2 can be used when first invoked.

```bash
aws bedrock list-foundation-models \
  --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'titan-embed-text-v2')].modelId"
```

Verifies the project account can see Titan Text Embeddings v2. Output included:

```text
amazon.titan-embed-text-v2:0
amazon.titan-embed-text-v2:0:8k
```

```bash
aws bedrock-runtime invoke-model \
  --region us-east-1 \
  --model-id amazon.titan-embed-text-v2:0 \
  --body '{"inputText":"test embedding"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/titan-embed-response.json
```

Invokes Titan v2 embeddings and writes the JSON response to a temporary file.

```bash
python - <<'PY'
import json
data = json.load(open("/tmp/titan-embed-response.json"))
print(data.keys())
print(len(data["embedding"]))
PY
```

Verifies the response shape and embedding dimension. The expected dimension is:

```text
1024
```

## Next Gate

Before any CDK/ECS work, the app must pass the local zero-Ollama gate:

```bash
export AWS_PROFILE=ph-law-rag-dev
cp .env.cloud-gate.example .env.cloud-gate
# Fill in QDRANT_API_KEY and ANTHROPIC_API_KEY in .env.cloud-gate.
export RAGLAB_ENV_FILE=.env.cloud-gate
```

`EMBEDDING_BACKEND=bedrock` is the only embedding switch in the cloud-gate
profile. `Settings` derives `amazon.titan-embed-text-v2:0` and dimension `1024`
from that backend and fails loudly if model/dim are explicitly mismatched.

Then:

```bash
ollama stop
raglab show-config
raglab reindex
raglab ask "What are the requisites of self-defense under the Revised Penal Code?"
curl localhost:8000/health
```

The gate passes when:

- `raglab reindex` writes 1024-dim vectors to Qdrant Cloud.
- `raglab ask` returns a grounded answer.
- `/health` returns `status: ok` with `ollama: null`.
- Nothing in the request path depends on local Ollama.
