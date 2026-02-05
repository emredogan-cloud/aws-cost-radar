import os
import json
import csv
from datetime import datetime
from dataclasses import asdict
from core.logging import get_logger
from core.session import AWSSessionManager

from services.KMS_cost_tool import KMSCollector
from services.EC2_cost_tool import ResourceInventoryManager
from services.NAT_GW_cost_tool import run as runNAT
from services.RDS_cost_tool import MultiRegionRDSCostAuditor, RDSConfig

logger = get_logger('Service_report' , 'INFO')

class ReportGenerator:
    def __init__(self, output_dir="reports"):
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def save_to_json(self, data, service_name):
        if not data: return
        filename = f"{self.output_dir}/{service_name}_{self.timestamp}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, default=str)
        logger.info(f"JSON Report Completed: {filename}")

    def save_to_csv(self, data, service_name):
        if not data: return
        filename = f"{self.output_dir}/{service_name}_{self.timestamp}.csv"
        keys = data[0].keys()
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            dict_writer = csv.DictWriter(f, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(data)
        logger.info(f"CSV Report Completed: {filename}")

def run_rds(reporter):
    logger.info("RDS Screening and Reporting Has Begun")
    session_mgr = AWSSessionManager.get_instance()
    ec2 = session_mgr.get_client('ec2', region='us-east-1')
    all_regions = [r['RegionName'] for r in ec2.describe_regions()['Regions']]

    config = RDSConfig(regions=all_regions, max_workers=12)
    auditor = MultiRegionRDSCostAuditor(config)
    
    rds_findings = auditor.run_parallel_audit()
    
    rds_data = [asdict(item) for item in rds_findings]
    
    reporter.save_to_json(rds_data, "RDS_Report")
    reporter.save_to_csv(rds_data, "RDS_Report")

def run_ec2(reporter):
    logger.info("EC2 Screening and Reporting Has Begun")
    manager = ResourceInventoryManager()
    findings = manager.run() 
    manager.display_results(findings)
    
    reporter.save_to_json(findings, "EC2_Report")
    reporter.save_to_csv(findings, "EC2_Report")

def run_kms(reporter):
    logger.info("KMS Screening and Reporting Has Begun")
    collector = KMSCollector()
    
    kms_findings = collector.run()
    
    kms_data = [asdict(item) for item in kms_findings]
    
    reporter.save_to_json(kms_data, "KMS_Report")
    reporter.save_to_csv(kms_data, "KMS_Report")

def run_nat(reporter):
    logger.info("NAT Gateway Screening and Reporting Has Begun")
    nat_data = runNAT() 
    
    if nat_data:
        reporter.save_to_json(nat_data, "NAT_GW_Report")
        reporter.save_to_csv(nat_data, "NAT_GW_Report")

if __name__ == '__main__':
    reporter = ReportGenerator(output_dir="reports")
    
    print("\n" + "═"*70)
    run_rds(reporter)
    
    print("\n" + "═"*70)
    run_kms(reporter)
    
    print("\n" + "═"*70)
    run_ec2(reporter)
    
    print("\n" + "═"*70)
    run_nat(reporter)
    
    print("\n" + "═"*70)
    logger.info("ALL SCANS COMPLETE. Reports are in the 'reports/' folder..")