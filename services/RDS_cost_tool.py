import os
import sys
from typing import Set, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from prettytable import PrettyTable
except ImportError:
    print("ERROR: 'prettytable' module not found. Install with: pip install prettytable")
    sys.exit(1)

try:
    from core.logging import get_logger
    from core.session import AWSSessionManager
except ImportError:
    print("ERROR: 'core' module not found. Please check project structure.")
    sys.exit(1)


@dataclass
class RDSConfig:
    regions: List[str] = field(default_factory=lambda: ['us-east-1'])
    include_clusters: bool = True
    include_snapshots: bool = True
    include_reserved: bool = True
    include_proxies: bool = True
    max_workers: int = 12


@dataclass
class CostItem:
    region: str
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


class RegionRDSAuditor:
    
    def __init__(self, region: str, config: RDSConfig):
        self.region = region
        self.config = config
        self.logger = get_logger(f'RDS_Auditor_{region}', 'ERROR')
        session_mgr = AWSSessionManager.get_instance()
        self.client = session_mgr.get_client('rds', region=region)
        self.cost_items: List[CostItem] = []
        self.active_instances: Set[str] = set()
        self.active_clusters: Set[str] = set()
        self.stats = {
            'db_instances': 0,
            'clusters': 0,
            'manual_snapshots': 0,
            'automated_snapshots': 0,
            'cluster_snapshots': 0
        }

    def scan_db_instances(self) -> None:
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
                        region=self.region,
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

            self.stats['db_instances'] = instance_count

        except ClientError as e:
            if e.response['Error']['Code'] != 'RequestExpired':
                self.logger.error(f"[{self.region}] Instance scan error: {e}")

    def scan_db_clusters(self) -> None:
        if not self.config.include_clusters:
            return

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
                        region=self.region,
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

            self.stats['clusters'] = cluster_count

        except ClientError as e:
            if e.response['Error']['Code'] != 'RequestExpired':
                self.logger.error(f"[{self.region}] Cluster scan error: {e}")

    def scan_snapshots(self) -> None:
        if not self.config.include_snapshots:
            return

        safe_list = self.active_instances.union(self.active_clusters)

        snapshot_types = ['manual', 'automated']
        for snap_type in snapshot_types:
            try:
                paginator = self.client.get_paginator('describe_db_snapshots')
                snapshot_count = 0

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
                        if not snap_id:
                            continue

                        cost_item = CostItem(
                            region=self.region,
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

                if snap_type == 'manual':
                    self.stats['manual_snapshots'] = snapshot_count
                else:
                    self.stats['automated_snapshots'] = snapshot_count

            except ClientError as e:
                if e.response['Error']['Code'] != 'RequestExpired':
                    self.logger.error(f"[{self.region}] Snapshot scan error ({snap_type}): {e}")

        try:
            paginator = self.client.get_paginator('describe_db_cluster_snapshots')
            cluster_snapshot_count = 0

            for page in paginator.paginate():
                for snapshot in page['DBClusterSnapshots']:
                    cluster_snapshot_count += 1

                    snap_id = snapshot.get('DBClusterSnapshotIdentifier')
                    cluster_id = snapshot.get('DBClusterIdentifier')
                    size = snapshot.get('AllocatedStorage', 0)
                    engine = snapshot.get('Engine', 'N/A')
                    status = snapshot.get('Status', 'N/A')
                    encrypted = snapshot.get('StorageEncrypted', False)
                    create_time = snapshot.get('SnapshotCreateTime')
                    snap_type = snapshot.get('SnapshotType', 'manual')

                    is_orphan = cluster_id not in safe_list

                    if not snap_id:
                        continue

                    cost_item = CostItem(
                        region=self.region,
                        resource_type=f"Cluster Snapshot ({snap_type})",
                        resource_id=snap_id,
                        status=status,
                        size_gb=float(size),
                        engine=engine,
                        encrypted=encrypted,
                        is_orphan=is_orphan,
                        additional_info={
                            'source_cluster': cluster_id,
                            'create_time': str(create_time) if create_time else 'N/A'
                        }
                    )

                    self.cost_items.append(cost_item)

            self.stats['cluster_snapshots'] = cluster_snapshot_count

        except ClientError as e:
            if e.response['Error']['Code'] != 'RequestExpired':
                self.logger.error(f"[{self.region}] Cluster snapshot scan error: {e}")

    def run_audit(self) -> tuple[List[CostItem], Dict]:
        self.scan_db_instances()
        self.scan_db_clusters()
        self.scan_snapshots()
        
        return self.cost_items, self.stats


class MultiRegionRDSCostAuditor:
    
    def __init__(self, config: RDSConfig):
        self.config = config
        self.logger = get_logger('MultiRegion_RDS_Auditor', 'ERROR')
        self.all_cost_items: List[CostItem] = []
        self.region_stats: Dict[str, Dict] = {}

    def scan_region(self, region: str) -> tuple[List[CostItem], Dict]:
        try:
            auditor = RegionRDSAuditor(region, self.config)
            return auditor.run_audit()
        except Exception as e:
            self.logger.error(f"Error scanning region {region}: {e}")
            return [], {}

    def run_parallel_audit(self):
        print(f"\n{'═' * 80}")
        print(f"{'RDS MULTI-REGION COST AUDIT':^80}")
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S'):^80}")
        print(f"{'═' * 80}\n")

        completed_regions = 0
        total_regions = len(self.config.regions)

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_region = {
                executor.submit(self.scan_region, region): region 
                for region in self.config.regions
            }

            for future in as_completed(future_to_region):
                region = future_to_region[future]
                completed_regions += 1

                try:
                    cost_items, stats = future.result()
                    self.all_cost_items.extend(cost_items)
                    self.region_stats[region] = stats
                    
                    total_snaps = stats.get('manual_snapshots', 0) + stats.get('automated_snapshots', 0) + stats.get('cluster_snapshots', 0)
                    print(f"\r[{completed_regions:2d}/{total_regions:2d}] {region:18} → "
                          f"Inst:{stats.get('db_instances', 0):3d} | "
                          f"Clus:{stats.get('clusters', 0):3d} | "
                          f"Snap:{total_snaps:4d}", end="", flush=True)
                    
                except Exception as e:
                    print(f"\r[{completed_regions:2d}/{total_regions:2d}] {region:18} → ERROR", end="", flush=True)
            return self.all_cost_items
        print("\n")
        self.display_summary_tables()

    def display_summary_tables(self) -> None:
        
        region_table = PrettyTable()
        region_table.field_names = ["Region", "Instances", "Clusters", "Man.Snap", "Auto.Snap", "Clus.Snap", "Total", "Storage (GB)"]
        region_table.align["Region"] = "l"
        for field in region_table.field_names[1:]:
            region_table.align[field] = "r"

        by_region = defaultdict(list)
        for item in self.all_cost_items:
            by_region[item.region].append(item)

        for region in sorted(by_region.keys()):
            items = by_region[region]
            stats = self.region_stats.get(region, {})
            total_size = sum(item.size_gb for item in items)
            
            region_table.add_row([
                region,
                stats.get('db_instances', 0),
                stats.get('clusters', 0),
                stats.get('manual_snapshots', 0),
                stats.get('automated_snapshots', 0),
                stats.get('cluster_snapshots', 0),
                len(items),
                f"{total_size:.2f}"
            ])

        print(f"\n{'SUMMARY BY REGION':^80}")
        print(region_table)

        type_table = PrettyTable()
        type_table.field_names = ["Resource Type", "Count", "Storage (GB)", "Orphans", "Orphan (GB)"]
        type_table.align["Resource Type"] = "l"
        for field in type_table.field_names[1:]:
            type_table.align[field] = "r"

        by_type = defaultdict(list)
        for item in self.all_cost_items:
            by_type[item.resource_type].append(item)

        for resource_type in sorted(by_type.keys()):
            items = by_type[resource_type]
            total_size = sum(item.size_gb for item in items)
            orphan_count = sum(1 for item in items if item.is_orphan)
            orphan_size = sum(item.size_gb for item in items if item.is_orphan)

            type_table.add_row([
                resource_type,
                len(items),
                f"{total_size:.2f}",
                orphan_count if orphan_count > 0 else "-",
                f"{orphan_size:.2f}" if orphan_size > 0 else "-"
            ])

        print(f"\n{'SUMMARY BY RESOURCE TYPE':^80}")
        print(type_table)

        total_storage = sum(item.size_gb for item in self.all_cost_items)
        orphan_count_total = sum(1 for item in self.all_cost_items if item.is_orphan)
        orphan_storage = sum(item.size_gb for item in self.all_cost_items if item.is_orphan)

        summary_table = PrettyTable()
        summary_table.field_names = ["Metric", "Value"]
        summary_table.align["Metric"] = "l"
        summary_table.align["Value"] = "r"
        summary_table.add_row(["Total Regions", len(by_region)])
        summary_table.add_row(["Total Resources", len(self.all_cost_items)])
        summary_table.add_row(["Total Storage", f"{total_storage:.2f} GB"])
        summary_table.add_row(["Orphan Resources", orphan_count_total])
        summary_table.add_row(["Orphan Storage", f"{orphan_storage:.2f} GB"])

        print(f"\n{'OVERALL SUMMARY':^80}")
        print(summary_table)

        if orphan_count_total > 0:
            print(f"\n{'⚠ WARNING ⚠':^80}")
            print(f"{orphan_count_total} orphan resources consuming {orphan_storage:.2f} GB".center(80))
            print(f"{'Consider removing to reduce costs':^80}\n")
        
        print(f"{'═' * 80}\n")


if __name__ == '__main__':
    regions_env = os.getenv('AWS_REGIONS', '')
    
    if regions_env == 'ALL':
        session_mgr = AWSSessionManager.get_instance()
        ec2_client = session_mgr.get_client('ec2', region='us-east-1')
        response = ec2_client.describe_regions()
        regions = [region['RegionName'] for region in response['Regions']]
    elif regions_env:
        regions = [r.strip() for r in regions_env.split(',')]
    else:
        regions = []

    config = RDSConfig(
        regions=regions,
        include_clusters=True,
        include_snapshots=True,
        include_reserved=True,
        include_proxies=True,
        max_workers=int(os.getenv('MAX_WORKERS', '5'))
    )

    auditor = MultiRegionRDSCostAuditor(config)
    auditor.run_parallel_audit()