import os
import sys
from typing import Set, Tuple, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

# 3. Party Imports
from botocore.exceptions import ClientError

# --- CORE MODÜLLER ---
try:
    from core.logging import get_logger
    from core.session import AWSSessionManager
except ImportError:
    print("HATA: 'core' modülü bulunamadı. Lütfen dosya yapısını kontrol edin.")
    sys.exit(1)


# --- KONFIGÜRASYON ---
@dataclass
class RDSConfig:
    """RDS Maliyet Denetim Konfigürasyonu"""
    region: str = os.getenv('AWS_REGION', 'us-east-1')
    include_clusters: bool = True
    include_snapshots: bool = True
    include_reserved: bool = True
    include_proxies: bool = True


@dataclass
class CostItem:
    """Maliyet kalemi için veri yapısı"""
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


# --- MALİYET DENETLEME YÖNETİCİSİ ---
class RDSCostAuditor:
    def __init__(self, config: RDSConfig):
        self.config = config
        self.logger = get_logger('RDS_Cost_Auditor', 'INFO')
        
        # Session Manager entegrasyonu
        session_mgr = AWSSessionManager.get_instance()
        self.client = session_mgr.get_client('rds', region=config.region)
        
        # Maliyet kalemlerini saklamak için
        self.cost_items: List[CostItem] = []
        self.active_instances: Set[str] = set()
        self.active_clusters: Set[str] = set()

    # ==================== INSTANCE ANALİZİ ====================
    def scan_db_instances(self) -> None:
        """
        Tüm RDS Instance'ları tarar ve maliyet bilgilerini toplar.
        """
        self.logger.info("=" * 80)
        self.logger.info("RDS DB INSTANCES TARANYOR...")
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
                    
                    # Aktif instance listesine ekle
                    self.active_instances.add(db_id)
                    
                    # Maliyet kalemi oluştur
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
                    
                    # Detaylı log
                    self.logger.info(
                        f"  Instance: {db_id:40} | Class: {instance_class:15} | "
                        f"Engine: {engine:12} | Status: {status:15} | "
                        f"Storage: {allocated_storage}GB ({storage_type})" +
                        (f" | IOPS: {iops}" if iops else "") +
                        (f" | Multi-AZ" if multi_az else "")
                    )
            
            self.logger.info(f"\n✓ Toplam {instance_count} DB Instance bulundu.\n")
            
        except ClientError as e:
            self.logger.error(f"Instance tarama hatası: {e}")

    # ==================== CLUSTER ANALİZİ ====================
    def scan_db_clusters(self) -> None:
        """
        Aurora Cluster'ları tarar ve maliyet bilgilerini toplar.
        """
        if not self.config.include_clusters:
            return
            
        self.logger.info("=" * 80)
        self.logger.info("AURORA CLUSTERS TARANYOR...")
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
                    
                    # Cluster member sayısı
                    members = cluster.get('DBClusterMembers', [])
                    member_count = len(members)
                    
                    # Aktif cluster listesine ekle
                    self.active_clusters.add(cluster_id)
                    
                    # Maliyet kalemi oluştur
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
            
            self.logger.info(f"\n✓ Toplam {cluster_count} Aurora Cluster bulundu.\n")
            
        except ClientError as e:
            self.logger.error(f"Cluster tarama hatası: {e}")

    # ==================== SNAPSHOT ANALİZİ ====================
    def scan_snapshots(self) -> None:
        """
        Tüm snapshotları tarar (Manual ve Automated).
        Orphan (sahipsiz) snapshotları işaretler.
        """
        if not self.config.include_snapshots:
            return
            
        self.logger.info("=" * 80)
        self.logger.info("DB SNAPSHOTS TARANYOR...")
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
                        snapshot_type = snapshot.get('SnapshotType', snap_type)
                        create_time = snapshot.get('SnapshotCreateTime')
                        
                        # Orphan kontrolü
                        is_orphan = instance_id not in safe_list
                        
                        if is_orphan:
                            orphan_count += 1
                            total_orphan_size += size
                        
                        # Maliyet kalemi oluştur
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
                                'snapshot_type': snapshot_type,
                                'create_time': str(create_time) if create_time else 'N/A'
                            }
                        )
                        
                        self.cost_items.append(cost_item)
                        
                        # Orphan ise özel işaretle
                        orphan_marker = " [ORPHAN!]" if is_orphan else ""
                        self.logger.info(
                            f"  Snapshot: {snap_id:50} | Source: {instance_id:30} | "
                            f"Size: {size:6}GB | Status: {status:12}{orphan_marker}"
                        )
                
                self.logger.info(
                    f"\n✓ {snap_type.upper()}: {snapshot_count} snapshot bulundu "
                    f"({orphan_count} orphan, {total_orphan_size}GB)\n"
                )
                
            except ClientError as e:
                self.logger.error(f"Snapshot tarama hatası ({snap_type}): {e}")

    # ==================== CLUSTER SNAPSHOT ANALİZİ ====================
    def scan_cluster_snapshots(self) -> None:
        """Aurora Cluster Snapshot'larını tarar."""
        if not self.config.include_clusters or not self.config.include_snapshots:
            return
            
        self.logger.info("=" * 80)
        self.logger.info("CLUSTER SNAPSHOTS TARANYOR...")
        self.logger.info("=" * 80)
        
        snapshot_types = ['manual', 'automated']
        
        for snap_type in snapshot_types:
            self.logger.info(f"\n--- {snap_type.upper()} Cluster Snapshots ---")
            
            try:
                paginator = self.client.get_paginator('describe_db_cluster_snapshots')
                snapshot_count = 0
                orphan_count = 0
                total_orphan_size = 0.0
                
                for page in paginator.paginate(SnapshotType=snap_type):
                    for snapshot in page['DBClusterSnapshots']:
                        snapshot_count += 1
                        
                        snap_id = snapshot.get('DBClusterSnapshotIdentifier')
                        cluster_id = snapshot.get('DBClusterIdentifier')
                        size = snapshot.get('AllocatedStorage', 0)
                        engine = snapshot.get('Engine', 'N/A')
                        status = snapshot.get('Status', 'N/A')
                        encrypted = snapshot.get('StorageEncrypted', False)
                        create_time = snapshot.get('SnapshotCreateTime')
                        
                        # Orphan kontrolü
                        is_orphan = cluster_id not in self.active_clusters
                        
                        if is_orphan:
                            orphan_count += 1
                            total_orphan_size += size
                        
                        # Maliyet kalemi oluştur
                        cost_item = CostItem(
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
                        
                        orphan_marker = " [ORPHAN!]" if is_orphan else ""
                        self.logger.info(
                            f"  Snapshot: {snap_id:50} | Source: {cluster_id:30} | "
                            f"Size: {size:6}GB | Status: {status:12}{orphan_marker}"
                        )
                
                self.logger.info(
                    f"\n✓ {snap_type.upper()}: {snapshot_count} cluster snapshot bulundu "
                    f"({orphan_count} orphan, {total_orphan_size}GB)\n"
                )
                
            except ClientError as e:
                self.logger.error(f"Cluster snapshot tarama hatası ({snap_type}): {e}")

    # ==================== RESERVED INSTANCES ANALİZİ ====================
    def scan_reserved_instances(self) -> None:
        """Reserved DB Instance'ları tarar."""
        if not self.config.include_reserved:
            return
            
        self.logger.info("=" * 80)
        self.logger.info("RESERVED DB INSTANCES TARANYOR...")
        self.logger.info("=" * 80)
        
        try:
            paginator = self.client.get_paginator('describe_reserved_db_instances')
            reserved_count = 0
            
            for page in paginator.paginate():
                for reserved in page['ReservedDBInstances']:
                    reserved_count += 1
                    
                    reserved_id = reserved.get('ReservedDBInstanceId')
                    instance_class = reserved.get('DBInstanceClass', 'N/A')
                    instance_count = reserved.get('DBInstanceCount', 0)
                    state = reserved.get('State', 'N/A')
                    offering_type = reserved.get('OfferingType', 'N/A')
                    duration = reserved.get('Duration', 0)
                    start_time = reserved.get('StartTime')
                    multi_az = reserved.get('MultiAZ', False)
                    
                    # Maliyet kalemi oluştur
                    cost_item = CostItem(
                        resource_type="Reserved Instance",
                        resource_id=reserved_id,
                        status=state,
                        instance_class=instance_class,
                        multi_az=multi_az,
                        additional_info={
                            'instance_count': instance_count,
                            'offering_type': offering_type,
                            'duration_months': duration // (30 * 24 * 60 * 60),
                            'start_time': str(start_time) if start_time else 'N/A',
                            'product_description': reserved.get('ProductDescription', 'N/A')
                        }
                    )
                    
                    self.cost_items.append(cost_item)
                    
                    self.logger.info(
                        f"  Reserved: {reserved_id:40} | Class: {instance_class:15} | "
                        f"Count: {instance_count} | Type: {offering_type:15} | "
                        f"State: {state}" +
                        (f" | Multi-AZ" if multi_az else "")
                    )
            
            self.logger.info(f"\n✓ Toplam {reserved_count} Reserved Instance bulundu.\n")
            
        except ClientError as e:
            self.logger.error(f"Reserved instance tarama hatası: {e}")

    # ==================== RDS PROXY ANALİZİ ====================
    def scan_db_proxies(self) -> None:
        """RDS Proxy'leri tarar."""
        if not self.config.include_proxies:
            return
            
        self.logger.info("=" * 80)
        self.logger.info("RDS PROXIES TARANYOR...")
        self.logger.info("=" * 80)
        
        try:
            paginator = self.client.get_paginator('describe_db_proxies')
            proxy_count = 0
            
            for page in paginator.paginate():
                for proxy in page['DBProxies']:
                    proxy_count += 1
                    
                    proxy_name = proxy.get('DBProxyName')
                    status = proxy.get('Status', 'N/A')
                    engine_family = proxy.get('EngineFamily', 'N/A')
                    require_tls = proxy.get('RequireTLS', False)
                    
                    # Maliyet kalemi oluştur
                    cost_item = CostItem(
                        resource_type="RDS Proxy",
                        resource_id=proxy_name,
                        status=status,
                        engine=engine_family,
                        additional_info={
                            'vpc_id': proxy.get('VpcId', 'N/A'),
                            'require_tls': require_tls,
                            'idle_client_timeout': proxy.get('IdleClientTimeout', 0)
                        }
                    )
                    
                    self.cost_items.append(cost_item)
                    
                    self.logger.info(
                        f"  Proxy: {proxy_name:40} | Engine: {engine_family:12} | "
                        f"Status: {status:15} | TLS: {require_tls}"
                    )
            
            self.logger.info(f"\n✓ Toplam {proxy_count} RDS Proxy bulundu.\n")
            
        except ClientError as e:
            self.logger.error(f"RDS Proxy tarama hatası: {e}")

    # ==================== ÖZET RAPOR ====================
    def generate_summary_report(self) -> None:
        """Toplanan tüm maliyet bilgilerinin özetini çıkarır."""
        self.logger.info("=" * 80)
        self.logger.info("MALİYET RAPORU ÖZETİ")
        self.logger.info("=" * 80)
        
        # Kaynak tipine göre gruplama
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
                f"\n  Toplam Kaynak: {count}"
                f"\n  Toplam Depolama: {total_size:.2f} GB"
            )
            
            if orphan_count > 0:
                self.logger.warning(
                    f"  ⚠️  ORPHAN Kaynak: {orphan_count}"
                    f"\n  ⚠️  ORPHAN Depolama: {orphan_size:.2f} GB"
                )
        
        # Genel Özet
        self.logger.info("\n" + "=" * 80)
        self.logger.info(f"GENEL ÖZET:")
        self.logger.info(f"  Toplam Kaynak Sayısı: {len(self.cost_items)}")
        self.logger.info(f"  Toplam Depolama: {total_storage:.2f} GB")
        
        orphan_count_total = sum(1 for item in self.cost_items if item.is_orphan)
        if orphan_count_total > 0:
            self.logger.warning(
                f"\n  ⚠️  TOPLAM ORPHAN KAYNAK: {orphan_count_total}"
                f"\n  ⚠️  TOPLAM ORPHAN DEPOLAMA: {orphan_storage:.2f} GB"
                f"\n  💰 TAVSİYE: Orphan kaynakları temizleyerek maliyet tasarrufu sağlayabilirsiniz!"
            )
        else:
            self.logger.info(f"\n  ✓ ORPHAN kaynak bulunamadı. Hesabınız temiz!")
        
        self.logger.info("=" * 80)

    # ==================== ANA DENETİM METODU ====================
    def run_audit(self):
        """Tam maliyet denetimini çalıştırır."""
        self.logger.info("\n\n")
        self.logger.info("╔" + "═" * 78 + "╗")
        self.logger.info("║" + " " * 20 + "RDS MALİYET DENETİMİ BAŞLIYOR" + " " * 28 + "║")
        self.logger.info("║" + f" Bölge: {self.config.region:67} ║")
        self.logger.info("║" + f" Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):67} ║")
        self.logger.info("╚" + "═" * 78 + "╝")
        self.logger.info("\n")
        
        # 1. DB Instances
        self.scan_db_instances()
        
        # 2. Aurora Clusters
        self.scan_db_clusters()
        
        # 3. DB Snapshots
        self.scan_snapshots()
        
        # 4. Cluster Snapshots
        self.scan_cluster_snapshots()
        
        # 5. Reserved Instances
        self.scan_reserved_instances()
        
        # 6. RDS Proxies
        self.scan_db_proxies()
        
        # 7. Özet Rapor
        self.generate_summary_report()
        
        self.logger.info("\n✓ RDS Maliyet Denetimi tamamlandı!\n")


# --- ÇALIŞTIRMA ---
if __name__ == '__main__':
    # Konfigürasyon oluştur
    config = RDSConfig(
        region=os.getenv('AWS_REGION', 'us-east-1'),
        include_clusters=True,
        include_snapshots=True,
        include_reserved=True,
        include_proxies=True
    )
    
    # Auditor'ı başlat
    auditor = RDSCostAuditor(config)
    
    # Denetimi çalıştır
    auditor.run_audit()
