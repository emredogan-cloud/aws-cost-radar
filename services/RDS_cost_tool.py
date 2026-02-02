import os
import sys
from typing import Set, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
from botocore.exceptions import ClientError

try:
    from core.logging import get_logger
    from core.session import AWSSessionManager
except ImportError:
    print("ERROR: 'core' module not found. Please check project structure.")
    sys.exit(1)


@dataclass
class RDSConfig:
    region: str = os.getenv('AWS_REGION', 'us-east-1')
    include_clusters: bool = True
    include_snapshots: bool = True
    include_reserved: bool = True
    include_proxies: bool = True


@dataclass
class CostItem:
    resource_type: str
    resource_id: str
    status: str
    size_gb: float = 0.0
    instance_class: str = ""
    engine: str = ""
    multi_az: bool = False
    encrypted: bool = False
    storage_type: str = ""
    iops: int = 0
    additional_info: Dict = field(default_factory=dict)
    is_orphan: bool = False

    def __str__(self):
        return (f"{self.resource_type:20} | {self.resource_id:40} | "
                f"Status: {self.status:15} | Size: {self.size_gb:8.2f}GB")


class RDSCostAuditor:
    def __init__(self, config: RDSConfig):
        self.config = config
        self.logger = get_logger('RDS_Cost_Auditor', 'INFO')
        session_mgr = AWSSessionManager.get_instance()
        self.client = session_mgr.get_client('rds', region=config.region)
        self.cost_items: List[CostItem] = []
        self.active_instances: Set[str] = set()
        self.active_clusters: Set[str] = set()

    def scan_db_instances(self) -> None:
        self.logger.info("=" * 80)
        self.logger.info("SCANNING RDS DB INSTANCES...")
        self.logger.info("=" * 80)

        try:
            paginator = self.client.get_paginator('describe_db_instances')
            instance_count = 0

            for page in paginator.paginate():
                for instance in page['DBInstances']:
                    instance_count += 1

                    db_id = instance['DBInstanceIdentifier']
                    status = instance['DBInstanceStatus']
                    instance_class = instance.get('DBInstanceClass', 'N/A')
                    engine = instance.get('Engine', 'N/A')
                    allocated_storage = instance.get('AllocatedStorage', 0)
                    storage_type = instance.get('StorageType', 'N/A')
                    iops = instance.get('Iops', 0)
                    multi_az = instance.get('MultiAZ', False)
                    encrypted = instance.get('StorageEncrypted', False)

                    self.active_instances.add(db_id)

                    cost_item = CostItem(
                        resource_type="DB Instance",
                        resource_id=db_id,
                        status=status,
                        size_gb=float(allocated_storage),
                        instance_class=instance_class,
                        engine=engine,
                        multi_az=multi_az,
                        encrypted=encrypted,
                        storage_type=storage_type,
                        iops=iops,
                        additional_info={
                            'availability_zone': instance.get('AvailabilityZone', 'N/A'),
                            'publicly_accessible': instance.get('PubliclyAccessible', False),
                            'backup_retention': instance.get('BackupRetentionPeriod', 0),
                            'auto_minor_version_upgrade': instance.get('AutoMinorVersionUpgrade', False)
                        }
                    )

                    self.cost_items.append(cost_item)

                    self.logger.info(
                        f"  Instance: {db_id:40} | Class: {instance_class:15} | "
                        f"Engine: {engine:12} | Status: {status:15} | "
                        f"Storage: {allocated_storage}GB ({storage_type})" +
                        (f" | IOPS: {iops}" if iops else "") +
                        (f" | Multi-AZ" if multi_az else "")
                    )

            self.logger.info(f"\n✓ Found {instance_count} DB Instances.\n")

        except ClientError as e:
            self.logger.error(f"Instance scan error: {e}")

    def scan_db_clusters(self) -> None:
        if not self.config.include_clusters:
            return

        self.logger.info("=" * 80)
        self.logger.info("SCANNING AURORA CLUSTERS...")
        self.logger.info("=" * 80)

        try:
            paginator = self.client.get_paginator('describe_db_clusters')
            cluster_count = 0

            for page in paginator.paginate():
                for cluster in page['DBClusters']:
                    cluster_count += 1

                    cluster_id = cluster['DBClusterIdentifier']
                    status = cluster['Status']
                    engine = cluster.get('Engine', 'N/A')
                    allocated_storage = cluster.get('AllocatedStorage', 0)
                    storage_encrypted = cluster.get('StorageEncrypted', False)
                    multi_az = cluster.get('MultiAZ', False)

                    members = cluster.get('DBClusterMembers', [])
                    member_count = len(members)

                    self.active_clusters.add(cluster_id)

                    cost_item = CostItem(
                        resource_type="Aurora Cluster",
                        resource_id=cluster_id,
                        status=status,
                        size_gb=float(allocated_storage),
                        engine=engine,
                        encrypted=storage_encrypted,
                        multi_az=multi_az,
                        additional_info={
                            'cluster_members': member_count,
                            'backup_retention': cluster.get('BackupRetentionPeriod', 0),
                            'preferred_backup_window': cluster.get('PreferredBackupWindow', 'N/A'),
                            'deletion_protection': cluster.get('DeletionProtection', False)
                        }
                    )

                    self.cost_items.append(cost_item)

                    self.logger.info(
                        f"  Cluster: {cluster_id:40} | Engine: {engine:12} | "
                        f"Status: {status:15} | Members: {member_count} | "
                        f"Storage: {allocated_storage}GB" +
                        (f" | Multi-AZ" if multi_az else "")
                    )

            self.logger.info(f"\n✓ Found {cluster_count} Aurora Clusters.\n")

        except ClientError as e:
            self.logger.error(f"Cluster scan error: {e}")

    def scan_snapshots(self) -> None:
        if not self.config.include_snapshots:
            return

        self.logger.info("=" * 80)
        self.logger.info("SCANNING DB SNAPSHOTS...")
        self.logger.info("=" * 80)

        snapshot_types = ['manual', 'automated']
        safe_list = self.active_instances.union(self.active_clusters)

        for snap_type in snapshot_types:
            self.logger.info(f"\n--- {snap_type.upper()} Snapshots ---")

            try:
                paginator = self.client.get_paginator('describe_db_snapshots')
                snapshot_count = 0
                orphan_count = 0
                total_orphan_size = 0.0

                for page in paginator.paginate(SnapshotType=snap_type):
                    for snapshot in page['DBSnapshots']:
                        snapshot_count += 1

                        snap_id = snapshot.get('DBSnapshotIdentifier')
                        instance_id = snapshot.get('DBInstanceIdentifier')
                        size = snapshot.get('AllocatedStorage', 0)
                        engine = snapshot.get('Engine', 'N/A')
                        status = snapshot.get('Status', 'N/A')
                        encrypted = snapshot.get('Encrypted', False)
                        create_time = snapshot.get('SnapshotCreateTime')

                        is_orphan = instance_id not in safe_list

                        if is_orphan:
                            orphan_count += 1
                            total_orphan_size += size

                        cost_item = CostItem(
                            resource_type=f"Snapshot ({snap_type})",
                            resource_id=snap_id,
                            status=status,
                            size_gb=float(size),
                            engine=engine,
                            encrypted=encrypted,
                            is_orphan=is_orphan,
                            additional_info={
                                'source_instance': instance_id,
                                'create_time': str(create_time) if create_time else 'N/A'
                            }
                        )

                        self.cost_items.append(cost_item)

                        orphan_marker = " [ORPHAN!]" if is_orphan else ""
                        self.logger.info(
                            f"  Snapshot: {snap_id:50} | Source: {instance_id:30} | "
                            f"Size: {size:6}GB | Status: {status:12}{orphan_marker}"
                        )

                self.logger.info(
                    f"\n✓ {snap_type.upper()}: Found {snapshot_count} snapshots "
                    f"({orphan_count} orphan, {total_orphan_size}GB)\n"
                )

            except ClientError as e:
                self.logger.error(f"Snapshot scan error ({snap_type}): {e}")

    def generate_summary_report(self) -> None:
        self.logger.info("=" * 80)
        self.logger.info("COST REPORT SUMMARY")
        self.logger.info("=" * 80)

        by_type = defaultdict(list)
        for item in self.cost_items:
            by_type[item.resource_type].append(item)

        total_storage = 0.0
        orphan_storage = 0.0

        for resource_type in sorted(by_type.keys()):
            items = by_type[resource_type]
            count = len(items)
            total_size = sum(item.size_gb for item in items)
            orphan_count = sum(1 for item in items if item.is_orphan)
            orphan_size = sum(item.size_gb for item in items if item.is_orphan)

            total_storage += total_size
            orphan_storage += orphan_size

            self.logger.info(
                f"\n{resource_type}:"
                f"\n  Total Resources: {count}"
                f"\n  Total Storage: {total_size:.2f} GB"
            )

            if orphan_count > 0:
                self.logger.warning(
                    f"    ORPHAN Resources: {orphan_count}"
                    f"\n    ORPHAN Storage: {orphan_size:.2f} GB"
                )

        self.logger.info("\n" + "=" * 80)
        self.logger.info("OVERALL SUMMARY:")
        self.logger.info(f"  Total Resources: {len(self.cost_items)}")
        self.logger.info(f"  Total Storage: {total_storage:.2f} GB")

        orphan_count_total = sum(1 for item in self.cost_items if item.is_orphan)
        if orphan_count_total > 0:
            self.logger.warning(
                f"\n    TOTAL ORPHAN RESOURCES: {orphan_count_total}"
                f"\n    TOTAL ORPHAN STORAGE: {orphan_storage:.2f} GB"
                f"\n   RECOMMENDATION: Remove orphan resources to save cost!"
            )
        else:
            self.logger.info("\n  ✓ No orphan resources found.")

        self.logger.info("=" * 80)

    def run_audit(self):
        self.logger.info("\n\n")
        self.logger.info("╔" + "═" * 78 + "╗")
        self.logger.info("║" + " " * 22 + "RDS COST AUDIT STARTING" + " " * 28 + "║")
        self.logger.info("║" + f" Region: {self.config.region:67} ║")
        self.logger.info("║" + f" Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):69}║")
        self.logger.info("╚" + "═" * 78 + "╝")
        self.logger.info("\n")

        self.scan_db_instances()
        self.scan_db_clusters()
        self.scan_snapshots()
        self.generate_summary_report()

        self.logger.info("\n✓ RDS Cost Audit completed!\n")


if __name__ == '__main__':
    config = RDSConfig(
        region=os.getenv('AWS_REGION', 'us-east-1'),
        include_clusters=True,
        include_snapshots=True,
        include_reserved=True,
        include_proxies=True
    )

    auditor = RDSCostAuditor(config)
    auditor.run_audit()
