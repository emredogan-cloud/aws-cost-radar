import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from prettytable import PrettyTable
from core.session import AWSSessionManager
from core.logging import get_logger
from utils.config import REGION_LOCATION_MAP

logger = get_logger("Collector_ec2", "INFO")


@dataclass
class ResourceInfo:
    service: str
    resource_id: str
    name: str
    region: str
    size: Optional[str] = None
    create_time: Optional[str] = None
    status: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "service": self.service,
            "resource_id": self.resource_id,
            "name": self.name,
            "region": self.region,
            "size": self.size,
            "create_time": self.create_time,
            "status": self.status,
            "meta": self.meta
        }
class EC2RegionCollector:
    
    def __init__(self, session_manager: AWSSessionManager):
        self.session_manager = session_manager

    def collect(self, region: str) -> List[ResourceInfo]:
        findings = []
        try:
            ec2_client = self.session_manager.get_client("ec2", region)
        except Exception as e:
            logger.error(f"Client connection failed for {region}: {e}")
            return []

        print(f"\rScanning region: {region}".ljust(60), end="", flush=True)

        findings.extend(self._scan_instances(ec2_client, region))
        findings.extend(self._scan_volumes(ec2_client, region))
        findings.extend(self._scan_orphan_volumes(ec2_client, region))
        findings.extend(self._scan_snapshots(ec2_client, region))
        findings.extend(self._scan_eips(ec2_client, region))

        return findings

    def _scan_instances(self, ec2, region) -> List[ResourceInfo]:
        findings = []
        try:
            paginator = ec2.get_paginator("describe_instances")
            for page in paginator.paginate():
                for reservation in page["Reservations"]:
                    for instance in reservation["Instances"]:
                        name = "N/A"
                        for tag in instance.get("Tags", []):
                            if tag["Key"] == "Name":
                                name = tag["Value"]
                                break
                        
                        findings.append(ResourceInfo(
                            service="EC2",
                            resource_id=instance["InstanceId"],
                            name=name,
                            region=region,
                            size=instance["InstanceType"],
                            create_time=instance.get("LaunchTime", "").strftime("%Y-%m-%d %H:%M:%S") if instance.get("LaunchTime") else "N/A",
                            status=instance["State"]["Name"],
                            meta={"platform": instance.get("PlatformDetails", "Linux")}
                        ))
        except Exception as e:
            logger.error(f"EC2 scan error in {region}: {e}")
        return findings

    def _scan_volumes(self, ec2, region) -> List[ResourceInfo]:
        findings = []
        try:
            paginator = ec2.get_paginator("describe_volumes")
            for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["in-use"]}]):
                for vol in page["Volumes"]:
                    name = "N/A"
                    for tag in vol.get("Tags", []):
                        if tag["Key"] == "Name":
                            name = tag["Value"]
                            break
                    
                    attached_to = "N/A"
                    if vol.get("Attachments"):
                        attached_to = vol["Attachments"][0].get("InstanceId", "N/A")
                    
                    findings.append(ResourceInfo(
                        service="EBS Volume",
                        resource_id=vol["VolumeId"],
                        name=name,
                        region=region,
                        size=f"{vol['Size']} GB ({vol['VolumeType']})",
                        create_time=vol.get("CreateTime", "").strftime("%Y-%m-%d %H:%M:%S") if vol.get("CreateTime") else "N/A",
                        status=vol["State"],
                        meta={"attached_to": attached_to}
                    ))
        except Exception as e:
            logger.error(f"Volume scan error in {region}: {e}")
        return findings

    def _scan_orphan_volumes(self, ec2, region) -> List[ResourceInfo]:
        findings = []
        try:
            paginator = ec2.get_paginator("describe_volumes")
            for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
                for vol in page["Volumes"]:
                    name = "N/A"
                    for tag in vol.get("Tags", []):
                        if tag["Key"] == "Name":
                            name = tag["Value"]
                            break
                    
                    findings.append(ResourceInfo(
                        service="Orphan Volume",
                        resource_id=vol["VolumeId"],
                        name=name,
                        region=region,
                        size=f"{vol['Size']} GB ({vol['VolumeType']})",
                        create_time=vol.get("CreateTime", "").strftime("%Y-%m-%d %H:%M:%S") if vol.get("CreateTime") else "N/A",
                        status="Available (Not Attached)",
                        meta={"type": vol["VolumeType"]}
                    ))
        except Exception as e:
            logger.error(f"Orphan volume scan error in {region}: {e}")
        return findings

    def _scan_snapshots(self, ec2, region) -> List[ResourceInfo]:
        findings = []
        try:
            paginator = ec2.get_paginator("describe_snapshots")
            for page in paginator.paginate(OwnerIds=["self"]):
                for snap in page["Snapshots"]:
                    name = "N/A"
                    for tag in snap.get("Tags", []):
                        if tag["Key"] == "Name":
                            name = tag["Value"]
                            break
                    
                    findings.append(ResourceInfo(
                        service="Snapshot",
                        resource_id=snap["SnapshotId"],
                        name=name,
                        region=region,
                        size=f"{snap['VolumeSize']} GB",
                        create_time=snap.get("StartTime", "").strftime("%Y-%m-%d %H:%M:%S") if snap.get("StartTime") else "N/A",
                        status=snap.get("State", "N/A"),
                        meta={"description": snap.get("Description", "N/A")}
                    ))
        except Exception as e:
            logger.error(f"Snapshot scan error in {region}: {e}")
        return findings

    def _scan_eips(self, ec2, region) -> List[ResourceInfo]:
        findings = []
        try:
            response = ec2.describe_addresses()
            for addr in response.get("Addresses", []):
                status = "Attached" if addr.get("InstanceId") else "Detached"
                attached_to = addr.get("InstanceId", "N/A")
                
                name = "N/A"
                for tag in addr.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break
                
                findings.append(ResourceInfo(
                    service="Elastic IP",
                    resource_id=addr["PublicIp"],
                    name=name,
                    region=region,
                    size="N/A",
                    create_time="N/A",
                    status=status,
                    meta={"attached_to": attached_to, "allocation_id": addr.get("AllocationId", "N/A")}
                ))
        except Exception as e:
            logger.error(f"EIP scan error in {region}: {e}")
        return findings

class ResourceInventoryManager:
    def __init__(self, max_workers=10):
        self.session_manager = AWSSessionManager.get_instance()
        self.max_workers = max_workers

    def get_regions(self) -> List[str]:
        try:
            ec2 = self.session_manager.get_client("ec2", "us-east-1")
            response = ec2.describe_regions(AllRegions=False)
            return [
                r["RegionName"] for r in response["Regions"]
                if r["OptInStatus"] in ["opt-in-not-required", "opted-in"]
            ]
        except Exception as e:
            logger.error(f"Region list could not be retrieved.: {e}")
            return ["us-east-1"]

    def run(self, target_region: Optional[str] = None) -> List[Dict]:
        regions = [target_region] if target_region else self.get_regions()


        collector = EC2RegionCollector(self.session_manager)
        all_findings = []

        logger.info(f"Starting scan for {len(regions)} regions with {self.max_workers} threads...")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_region = {
                executor.submit(collector.collect, region): region
                for region in regions
            }
            for future in as_completed(future_to_region):
                try:
                    result = future.result()
                    all_findings.extend([f.to_dict() for f in result])
                except Exception as e:
                    region = future_to_region[future]
                    logger.error(f"Region {region} taranırken hata: {e}")

        print("\n")  
        logger.info("Scan completed.")
        return all_findings

    def display_results(self, findings: List[Dict]):
        if not findings:
            print("\n No resources found!")
            return

        services = {}
        for finding in findings:
            service = finding["service"]
            if service not in services:
                services[service] = []
            services[service].append(finding)

        for service, items in services.items():
            print(f"\n{'='*100}")
            print(f"  {service.upper()} RESOURCES ({len(items)} items)")
            print(f"{'='*100}")
            
            table = PrettyTable()
            table.field_names = ["Region", "Resource ID", "Name", "Size", "Create Time", "Status"]
            table.align = "l"
            table.max_width = 30
            
            for item in items:
                table.add_row([
                    item["region"],
                    item["resource_id"],
                    item["name"][:30] if item["name"] else "N/A",
                    item["size"] if item["size"] else "N/A",
                    item["create_time"] if item["create_time"] else "N/A",
                    item["status"] if item["status"] else "N/A"
                ])
            
            print(table)

        
        print(f"\n{'='*100}")
        print(f"  SUMMARY")
        print(f"{'='*100}")
        summary_table = PrettyTable()
        summary_table.field_names = ["Resource Type", "Count"]
        summary_table.align = "l"
        
        for service, items in sorted(services.items()):
            summary_table.add_row([service, len(items)])
        
        print(summary_table)
        print(f"\nTotal Resources: {len(findings)}")

def run(region=None):
    manager = ResourceInventoryManager()
    findings = manager.run(region)
    manager.display_results(findings)
    return findings

if __name__ == "__main__":
    run() 