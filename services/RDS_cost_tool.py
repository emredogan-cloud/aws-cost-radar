import os
import sys
from typing import Set, Tuple, Generator, Optional
from dataclasses import dataclass

# 3. Party Imports
from botocore.exceptions import ClientError

# Varsayılan importlarınızın (core.logging vb.) çalıştığını varsayıyorum.
# Çalışması için mock/placeholder kullanıyorum, siz kendi importlarınızı açabilirsiniz.
import logging
def get_logger(name, level):
    logger = logging.getLogger(name)
    logging.basicConfig(level=level)
    return logger

# AWS Session Manager'ın mock hali (Sizin kodunuzda zaten var)
import boto3
class AWSSessionManager:
    @staticmethod
    def get_instance():
        return AWSSessionManager()
    def get_client(self, service, region):
        return boto3.client(service, region_name=region)

# --- KONFIGÜRASYON SINIFI (Best Practice) ---

# --- ANA YÖNETİCİ SINIFI ---
class RDSReportManager:
    def __init__(self,):
        self.logger = get_logger('RDS_Report_Manager', 'INFO')
        self._session_manager = AWSSessionManager.get_instance()
        self._client = self._session_manager.get_client('rds', 'us-east-1')

    def get_active_instances(self) -> Set[str]:
        """Aktif (Available) instance ID'lerini set olarak döner."""
        active_instances = set()
        try:
            paginator = self._client.get_paginator('describe_db_instances')
            for page in paginator.paginate():
                for instance in page['DBInstances']:
                    if instance['DBInstanceStatus'] == 'available':
                        active_instances.add(instance['DBInstanceIdentifier'])
            
            self.logger.info(f"Active Instances Found: {len(active_instances)}")
            return active_instances
        except ClientError as e:
            self.logger.error(f"Failed to describe instances: {e}")
            raise

    def get_active_clusters(self) -> Set[str]:
        """Aktif Cluster ID'lerini döner."""
        clusters = set()
        try:
            paginator = self._client.get_paginator('describe_db_clusters')
            for page in paginator.paginate():
                for cluster in page['DBClusters']:
                    if cluster['Status'] == 'available':
                        clusters.add(cluster['DBClusterIdentifier'])
            self.logger.info(f"Active Clusters Found: {len(clusters)}")
            return clusters
        except ClientError as e:
            self.logger.error(f"Failed to describe clusters: {e}")
            return set() # Hata durumunda boş set dönerek akışı kırmıyoruz

    def _get_snapshots_generator(self, snapshot_type: str = 'automated') -> Generator[dict, None, None]:
        """
        Generic Snapshot Generator.
        Tüm snapshotları belleğe yüklemek yerine tek tek yield eder (Memory Efficiency).
        """
        paginator = self._client.get_paginator('describe_db_snapshots')
        for page in paginator.paginate(SnapshotType=snapshot_type):
            for snapshot in page['DBSnapshots']:
                yield snapshot

    def analyze_orphan_snapshots(self, active_instances: Set[str], snapshot_type: str) -> Tuple[Set[str], Set[str]]:
        """
        Belirtilen tipteki snapshotları tarar ve sahipsiz (orphan) olanları bulur.
        Return: (orphan_aurora_snapshots, orphan_standard_snapshots)
        """
        orphan_aurora = set()
        orphan_standard = set()

        self.logger.info(f"Analyzing {snapshot_type} snapshots for orphans...")

        try:
            # Generator kullanarak iterasyon yapıyoruz
            for snapshot in self._get_snapshots_generator(snapshot_type):
                instance_id = snapshot.get('DBInstanceIdentifier')
                snapshot_id = snapshot.get('DBSnapshotIdentifier')
                engine = snapshot.get('Engine', '')

                # Eğer snapshot'ın ait olduğu instance, aktif instancelar arasında yoksa ORPHAN'dır.
                if instance_id not in active_instances:
                    if engine.startswith('aurora'):
                        orphan_aurora.add(snapshot_id)
                    else:
                        orphan_standard.add(snapshot_id)
            
            self.logger.info(f"[{snapshot_type}] Orphan Aurora: {len(orphan_aurora)} | Orphan Standard: {len(orphan_standard)}")
            return orphan_aurora, orphan_standard

        except ClientError as e:
            self.logger.error(f"Error analyzing snapshots: {e}")
            return set(), set()

# --- UYGULAMA KOŞUM KATMANI ---
def run_report():
    # 2. Manager'ı Başlat
    manager = RDSReportManager()

    # 3. Operasyonlar
    # manager.create_db_instance() # İsteğe bağlı açılabilir

    # 4. Raporlama
    active_instances = manager.get_active_instances()
    manager.get_active_clusters()

    # DRY prensibine uygun, tekrar eden kod yok:
    orphans_manual = manager.analyze_orphan_snapshots(active_instances, snapshot_type='manual')
    orphans_automated = manager.analyze_orphan_snapshots(active_instances, snapshot_type='automated')

    # Sonuçları işle (Örn: Slack'e gönder, CSV'ye yaz vs.)
    print(f"\n--- REPORT SUMMARY ---")
    print(f"Manual Orphans: {len(orphans_manual[1])}")
    print(f"Automated Orphans: {len(orphans_automated[1])}")

if __name__ == '__main__':
    # Env değişkenlerini terminalden vermiş gibi simüle edelim (Test için)
    os.environ['DB_PASS'] = 'SuperSecretPass123!' 
    run_report()