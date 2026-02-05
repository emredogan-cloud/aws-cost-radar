# AWS Cost Radar

[![aws-cost-radar CI](https://github.com/emredogan-cloud/aws-cost-radar/actions/workflows/main.yaml/badge.svg)](https://github.com/emredogan-cloud/aws-cost-radar/actions/workflows/main.yaml)

A multi-region **AWS resource inventory & “hidden cost” auditor** built with **boto3**.

It helps you quickly spot **cost-incurring / forgotten resources** across regions (e.g., orphan EBS volumes, snapshot sprawl, idle NAT Gateways) and produces:

- a clean **CLI table** (PrettyTable)
- **CSV + JSON reports** under `./reports/`

> ✅ **Safe by default:** this project is **read-only** — it does not delete or modify resources.

---

## What it scans (current coverage)

| Area | What it finds | Common “cost signal” |
|---|---|---|
| EC2 / EBS | Instances, EBS volumes (in-use + **orphan**), EBS snapshots, Elastic IPs | Orphan volumes, detached EIPs, snapshot sprawl |
| NAT Gateway | NAT Gateways + CloudWatch traffic (last 30 days) | **Low-traffic NAT** = “zombie” candidate |
| RDS | RDS instances, Aurora clusters, DB/cluster snapshots | **Orphan snapshots** (snapshot exists, source DB/cluster not found) |
| KMS | KMS keys + rotation applicability/status | Rotation disabled / not applicable signals |

---

## Project structure

```text
aws-cost-radar/
├─ main.py                 # ✅ main entrypoint (runs scans + exports reports)
├─ core/
│  ├─ session.py           # boto3 Session + typed client factory (singleton)
│  └─ logging.py           # consistent log format
├─ services/
│  ├─ EC2_cost_tool.py
│  ├─ NAT_GW_cost_tool.py
│  ├─ RDS_cost_tool.py
│  └─ KMS_cost_tool.py
├─ utils/
│  └─ config.py
└─ reports/                # ✅ generated outputs (CSV + JSON)
```

> Tip: It’s best to add `reports/` to `.gitignore` so you don’t commit generated files.

---

## Requirements

- Python **3.10+** (works great on 3.12)
- AWS credentials available via one of these:
  - **AWS_PROFILE** (recommended)
  - environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
  - AWS SSO (via AWS CLI)
  - IAM Role (EC2/ECS/Lambda)

---

## Install

```bash
git clone https://github.com/emredogan-cloud/aws-cost-radar.git
cd aws-cost-radar

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

pip install boto3 botocore prettytable python-dotenv
```

> Optional (type hints only):  
> `pip install "boto3-stubs[ec2,rds,cloudwatch,kms]"`

---

## Quick start

### Run everything (recommended)

```bash
python3 main.py
```

This will:
- scan supported services across regions (depending on your configuration)
- print CLI tables
- write **CSV + JSON** files into `./reports/`

### Run a single module (debug / dev)

```bash
python -m services.EC2_cost_tool
python -m services.NAT_GW_cost_tool
python -m services.RDS_cost_tool
python -m services.KMS_cost_tool
```

---

## Reports (CSV + JSON)

All outputs are written under:

```text
./reports/
```

Typical files include:
- `*.csv` (easy to open in Excel / Google Sheets)
- `*.json` (automation / post-processing friendly)

If you want to keep the repo clean:

```bash
echo "reports/" >> .gitignore
```

---

## Configuration

### Regions (RDS module)

The RDS module supports environment configuration:

```bash
# Scan specific regions
export AWS_REGIONS="eu-central-1,us-east-1"

# Or scan all regions
export AWS_REGIONS="ALL"

# Thread count (default: 5)
export MAX_WORKERS="5"

python -m services.RDS_cost_tool
```

### Credentials (recommended)

Use profiles:

```bash
export AWS_PROFILE="your-profile"
python3 main.py
```

---

## IAM permissions (minimum)

This project uses **read-only** calls.

Easiest for learning/lab:
- AWS managed policy: `ReadOnlyAccess`

Minimal API coverage (high level):
- EC2: `DescribeInstances`, `DescribeVolumes`, `DescribeSnapshots`, `DescribeAddresses`, `DescribeRegions`
- CloudWatch: `GetMetricStatistics`
- RDS: `DescribeDBInstances`, `DescribeDBClusters`, `DescribeDBSnapshots`, `DescribeDBClusterSnapshots`
- KMS: `ListKeys`, `ListAliases`, `DescribeKey`, `GetKeyRotationStatus`

---

## Notes & limitations

- “Cost Radar” currently focuses on **inventory + cost signals**.  
  Exact USD/month calculation (Pricing API) can be added as a future enhancement.
- Large accounts may hit API throttling. If you see throttling:
  - lower worker count (where supported),
  - prefer fewer regions,
  - retry with backoff (future improvement).
- NAT CloudWatch metrics can be missing/delayed in some regions.

---

## Roadmap (recommended upgrades)


1. **Single CLI** (`argparse` / `typer`)
   - `python main.py scan --service ec2 --regions ALL --format json`
2. **Real cost estimation**
   - Pricing API integration for EBS GB-month, snapshot GB-month, NAT hourly + processing, detached EIP, etc.
3. **Tests**
   - `pytest` + `moto` for collectors
4. **CI**
   - GitHub Actions + `ruff` + `mypy`
5. **Docs**
   - “How to interpret findings” + screenshots + sample reports

---

## License

See `LICENSE`.
