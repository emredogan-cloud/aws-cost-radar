# AWS Cost Radar

A multi-region **AWS resource inventory & “hidden cost” auditor** built with **boto3**.  
It helps you quickly spot **cost-incurring / forgotten resources** across regions (e.g., orphan EBS volumes, snapshots, idle NAT Gateways) and prints a clean CLI summary using **PrettyTable**.

> ✅ **Safe by default:** this project **does not delete** anything — it only reads and reports.

---

## What it scans (current modules)

| Module | Finds / Reports | “Cost signal” examples |
|---|---|---|
| `EC2_cost_tool` | EC2 instances, EBS volumes (in-use + orphan), EBS snapshots, Elastic IPs | Orphan volumes, detached EIPs, snapshot sprawl |
| `NAT_GW_cost_tool` | NAT Gateways + CloudWatch traffic (last 30 days) | Flags **low-traffic NAT Gateways** as “zombie” candidates |
| `RDS_cost_tool` | RDS instances, Aurora clusters, RDS snapshots (manual/automated), cluster snapshots | Detects **orphan snapshots** (snapshot exists but source DB/cluster not found) |
| `KMS_cost_tool` | KMS keys + rotation status applicability | Highlights keys where rotation is **disabled / not applicable** |

---

## Project structure

```text
aws-cost-radar/
├─ core/
│  ├─ session.py        # boto3 Session + typed client factory (singleton)
│  └─ logging.py        # consistent log format
├─ services/
│  ├─ EC2_cost_tool.py
│  ├─ NAT_GW_cost_tool.py
│  ├─ RDS_cost_tool.py
│  └─ KMS_cost_tool.py
└─ utils/
   └─ config.py         # region → pricing “location” mapping (for future Pricing API)
```

---

## Requirements

- Python **3.10+** (tested with 3.12)
- AWS credentials available via one of these:
  - `AWS_PROFILE=...` (recommended)
  - environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
  - AWS SSO (via AWS CLI)
  - IAM Role (EC2/ECS/Lambda)

### Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install boto3 botocore prettytable
```

> Optional (type hints only):  
> `pip install "boto3-stubs[ec2,rds,cloudwatch,kms]"`

---

## Quick start

From the repo root:

### 1) EC2 / EBS / Snapshot / EIP inventory (multi-region)

```bash
python -m services.EC2_cost_tool
```

### 2) NAT Gateway audit (multi-region + traffic)

```bash
python -m services.NAT_GW_cost_tool
```

### 3) RDS audit (controlled by env vars)

You can control regions and worker count:

```bash
# Scan specific regions
export AWS_REGIONS="eu-central-1,us-east-1"
export MAX_WORKERS="5"
python -m services.RDS_cost_tool
```

Scan **all** regions:

```bash
export AWS_REGIONS="ALL"
python -m services.RDS_cost_tool
```

### 4) KMS rotation audit (multi-region)

```bash
python -m services.KMS_cost_tool
```

---

## Output examples (what you’ll see)

- **PrettyTable** grouped by resource type (EC2/EBS/Snapshots/EIPs)
- **NAT Gateway table** with:
  - Region, NAT ID, VPC/Subnet, State, Public IP
  - **Traffic (GB)** last 30 days
  - **Status** (“ZOMBIE” candidate when traffic is very low)
- **RDS summaries**:
  - By region: instances/clusters/snapshots + total storage (GB)
  - By resource type: count + orphan counts
  - Overall summary + orphan warning
- **KMS table**:
  - Key manager (AWS vs Customer), state, rotation status and reason

---

## IAM permissions (minimum)

This project uses **read-only** calls.

Suggested approach:
- Attach **AWS managed policy**: `ReadOnlyAccess` for easiest setup (lab/learning).
- For production: create a tight policy for only what you need.

High-level API coverage:
- EC2: `DescribeInstances`, `DescribeVolumes`, `DescribeSnapshots`, `DescribeAddresses`, `DescribeRegions`
- CloudWatch: `GetMetricStatistics`
- RDS: `DescribeDBInstances`, `DescribeDBClusters`, `DescribeDBSnapshots`, `DescribeDBClusterSnapshots`
- KMS: `ListKeys`, `ListAliases`, `DescribeKey`, `GetKeyRotationStatus`

---

## Notes & limitations

- “Cost Radar” is currently **inventory + cost signals**.  
  It does **not** calculate exact monthly USD cost yet (Pricing API integration is a natural next step).
- CloudWatch NAT metrics can be missing or delayed in some accounts/regions.
- Large accounts may hit API throttling; if so, reduce worker count in modules that support it.

---

## Roadmap (recommended upgrades)

If you want this to look *very strong* on a CV:

1. **Single CLI entrypoint**
   - `python -m aws_cost_radar scan --service ec2 --region eu-central-1 --format json`
2. **Export formats**
   - CSV / JSON output to `reports/`
3. **Real cost estimation**
   - AWS Pricing API integration (at least NAT hourly + data processing, EBS GB-month, snapshot GB-month, EIP when detached)
4. **Tests**
   - `pytest` + `moto` for basic flows
5. **Quality gates**
   - `ruff`, `black`, `mypy`, GitHub Actions CI
6. **Docs**
   - “How to interpret findings” + examples + sample outputs

---

## Contributing

PRs and issues are welcome.  
If you’re using this project in a portfolio, please keep secrets out of git history (never commit `.env` or credentials).

---

## License

See `LICENSE`.
