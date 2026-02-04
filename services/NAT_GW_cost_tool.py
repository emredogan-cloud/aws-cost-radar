from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from prettytable import PrettyTable

from core.session import AWSSessionManager
from core.logging import get_logger

logger = get_logger("Collector_NAT", "INFO")

@dataclass
class NATGatewayInfo:
    service: str
    resource_id: str
    name: str
    region: str
    vpc_id: str
    subnet_id: str
    state: str
    create_time: str
    traffic_gb: Optional[float] = None
    public_ip: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "service": self.service,
            "resource_id": self.resource_id,
            "name": self.name,
            "region": self.region,
            "vpc_id": self.vpc_id,
            "subnet_id": self.subnet_id,
            "state": self.state,
            "create_time": self.create_time,
            "traffic_gb": self.traffic_gb,
            "public_ip": self.public_ip,
            "meta": self.meta
        }

class NATGatewayCollector:
    
    def __init__(self, session_manager: AWSSessionManager):
        self.session_manager = session_manager

    def collect(self, region: str) -> List[NATGatewayInfo]:
        findings = []
        try:
            ec2_client = self.session_manager.get_client("ec2", region)
            cw_client = self.session_manager.get_client("cloudwatch", region)
        except Exception as e:
            logger.error(f"Client connection failed for {region}: {e}")
            return []

        print(f"\rScanning NAT Gateways in region: {region}".ljust(70), end="", flush=True)

        findings.extend(self._scan_nat_gateways(ec2_client, cw_client, region))

        return findings

    def _scan_nat_gateways(self, ec2, cw, region) -> List[NATGatewayInfo]:
        findings = []
        try:
            response = ec2.describe_nat_gateways()
            
            for nat_gw in response.get('NatGateways', []):
                nat_id = nat_gw['NatGatewayId']
                
                name = "N/A"
                for tag in nat_gw.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break
                
                public_ip = "N/A"
                nat_addresses = nat_gw.get('NatGatewayAddresses', [])
                if nat_addresses:
                    public_ip = nat_addresses[0].get('PublicIp', 'N/A')
                
                create_time = "N/A"
                if nat_gw.get('CreateTime'):
                    create_time = nat_gw['CreateTime'].strftime("%Y-%m-%d %H:%M:%S")
                
                traffic_gb = self._get_traffic_metrics(cw, nat_id, days=30)
                
                is_zombie = False
                if nat_gw.get('CreateTime'):
                    created_at = nat_gw['CreateTime'].replace(tzinfo=None)
                    running_hours = (datetime.utcnow() - created_at).total_seconds() / 3600
                    is_zombie = running_hours > 24 and traffic_gb < 1.0
                
                findings.append(NATGatewayInfo(
                    service="NAT Gateway",
                    resource_id=nat_id,
                    name=name,
                    region=region,
                    vpc_id=nat_gw.get('VpcId', 'N/A'),
                    subnet_id=nat_gw.get('SubnetId', 'N/A'),
                    state=nat_gw.get('State', 'N/A'),
                    create_time=create_time,
                    traffic_gb=traffic_gb,
                    public_ip=public_ip,
                    meta={
                        "is_zombie": is_zombie,
                        "connectivity_type": nat_gw.get('ConnectivityType', 'N/A')
                    }
                ))
        except Exception as e:
            logger.error(f"NAT Gateway scan error in {region}: {e}")
        
        return findings

    def _get_traffic_metrics(self, cw, nat_gw_id: str, days: int = 30) -> float:
        try:
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=days)

            total_bytes = 0
            
            for metric_name in ['BytesInFromSource', 'BytesOutToDestination']:
                try:
                    response = cw.get_metric_statistics(
                        Namespace='AWS/NATGateway',
                        MetricName=metric_name,
                        Dimensions=[{'Name': 'NatGatewayId', 'Value': nat_gw_id}],
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=86400,  
                        Statistics=['Sum']
                    )
                    
                    if response.get('Datapoints'):
                        total_bytes += sum(dp['Sum'] for dp in response['Datapoints'])
                except Exception as e:
                    logger.debug(f"Metric error for {nat_gw_id} - {metric_name}: {e}")
            
            total_gb = total_bytes / (1024 ** 3)
            return round(total_gb, 2)
        except Exception as e:
            logger.error(f"Traffic metrics error for {nat_gw_id}: {e}")
            return 0.0

class NATGatewayInventoryManager:
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

        collector = NATGatewayCollector(self.session_manager)
        all_findings = []

        logger.info(f"Starting NAT Gateway scan for {len(regions)} regions with {self.max_workers} threads...")

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
        logger.info("NAT Gateway scan completed.")
        return all_findings

    def display_results(self, findings: List[Dict]):
        if not findings:
            print("\n No NAT Gateways found!")
            return

        print(f"\n{'='*150}")
        print(f"  NAT GATEWAY RESOURCES ({len(findings)} items)")
        print(f"{'='*150}")
        
        table = PrettyTable()
        table.field_names = [
            "Region", 
            "NAT Gateway ID", 
            "Name", 
            "VPC ID",
            "Public IP",
            "State",
            "Traffic (GB)",
            "Create Time",
            "Status"
        ]
        table.align = "l"
        table.max_width = 20
        
        zombie_count = 0
        for item in findings:
            status = " ZOMBIE" if item.get("meta", {}).get("is_zombie", False) else " Active"
            if item.get("meta", {}).get("is_zombie", False):
                zombie_count += 1
            
            traffic = f"{item['traffic_gb']}" if item.get('traffic_gb') is not None else "N/A"
            
            table.add_row([
                item["region"],
                item["resource_id"],
                item["name"][:20] if item["name"] else "N/A",
                item["vpc_id"],
                item["public_ip"],
                item["state"],
                traffic,
                item["create_time"],
                status
            ])
        
        print(table)

        print(f"\n{'='*150}")
        print(f"  SUMMARY")
        print(f"{'='*150}")
        
        summary_table = PrettyTable()
        summary_table.field_names = ["Metric", "Count"]
        summary_table.align = "l"
        
        state_counts = {}
        for item in findings:
            state = item.get("state", "unknown")
            state_counts[state] = state_counts.get(state, 0) + 1
        
        summary_table.add_row(["Total NAT Gateways", len(findings)])
        summary_table.add_row(["Zombie NAT Gateways (Low Traffic)", zombie_count])
        for state, count in sorted(state_counts.items()):
            summary_table.add_row([f"State: {state}", count])
        
        print(summary_table)
        
        if zombie_count > 0:
            print(f"\n  Warning: {zombie_count} NAT Gateway(s) with very low traffic detected!")
            print("   Consider reviewing these for potential cost optimization.")


def run(region=None):
    manager = NATGatewayInventoryManager()

    findings = manager.run(region)
    manager.display_results(findings)
    return findings

if __name__ == "__main__":
    run()  
