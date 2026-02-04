from core.logging import get_logger
from core.session import AWSSessionManager
from dataclasses import dataclass,field
from prettytable import PrettyTable
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor,as_completed
from typing import Optional


@dataclass
class KMSFinding:
    Region : str
    Key_İd : str
    Alias: str
    Key_Arn: str
    Key_State : str = 'UNKOWN'
    Key_Usage: str = 'UNKOWN'
    Key_Spec: str = 'UNKOWN'
    Key_Manager: str = 'UNKOWN'
    Origin: str = 'UNKOWN'
    Rotation_Reason: str = 'N/A'
    rotation_enabled: Optional[bool] = None
    

class KMSCollector:
    def __init__(self) -> None:
        self.manager = AWSSessionManager.get_instance()
        self.logger = get_logger('KMS_LİST' , 'INFO')
        self.ec2 = self.manager.get_client('ec2')
        self.kms = self.manager.get_client('kms')
        self.table = PrettyTable(["Region", "KeyId","Alias","KeyArn","Rotation","KeyState","KeyUsage","KeySpec","KeyManager","Origin","RotationReason"])

    def get_regions(self):
        
        region = [r['RegionName'] for r in self.ec2.describe_regions()['Regions']]
        return region

    def get_rotation_status(self, kms_client ,key_id) -> Optional[bool]:
        try:
            response = kms_client.get_key_rotation_status(KeyId=key_id)
            return response['KeyRotationEnabled']
        except ClientError as e:
            error = e.response['Error']['Code']
            self.logger.debug(f'Rotation status not available for {key_id}: {error}')
            return None


    def kms_alias(self,kms_client):

        alias_map = {}

        for page in kms_client.get_paginator('list_aliases').paginate():
            for key in page['Aliases']:
                target_key_id = key.get('TargetKeyId')
                alias_name = key['AliasName']
                if target_key_id and alias_name:
                    alias_map[target_key_id] = alias_name
        return  alias_map


    def decribe_key_meta(self, kms_client ,key_id : str) -> dict:
        try:
            response = kms_client.describe_key(KeyId = key_id)
            meta = response['KeyMetadata']

            return {
            'key_state' : meta['KeyState'],
            'key_usage' : meta['KeyUsage'],
            'key_manager' : meta['KeyManager'],
            'origin' : meta['Origin'],
            'key_spec' : meta['KeySpec']
            }
        except ClientError:
            return {
                'KeyState':'ERROR'
            }



    def list_keys(self,kms_client):
        keys_list = []
        for page in kms_client.get_paginator('list_keys').paginate():
            for key in page['Keys']:
                keys_list.append(key)
        return keys_list


    def scan_region(self,reg : str):
        kms = self.manager.get_client('kms' , region=reg)
        rows = []
        alias_map = self.kms_alias(kms)
        keys = self.list_keys(kms)
        

        for key in keys:
            key_id = key['KeyId']
            key_arn = key['KeyArn']
            alias = alias_map.get(key_id , 'No Alias')
            rotation = self.get_rotation_status(kms , key_id)
            rows.append(KMSFinding(
                Region=reg,
                Key_İd=key_id,
                Alias=alias,
                Key_Arn=key_arn,
                rotation_enabled=rotation
            ))

        return rows

        
    def run(self):
        try:
            region = self.get_regions()
            max_workers = 8
            completed_regions = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_region={
                    executor.submit(self.scan_region , reg):reg
                    for reg in region
                }

                for future in as_completed(future_to_region):                       
                    reg = future_to_region[future]
                    rows = future.result()

                    for finding in rows:
                        self.table.add_row([
                            finding.region,
                            finding.key_id,
                            finding.alias,
                            finding.key_arn,
                            'ENABLED' if finding.rotation_enabled is True else 'DİSABLED' if finding.rotation_enabled is False else 'N/A'
                        ])

                        
                    completed_regions += 1
                    print(
            f"Progress: {completed_regions}/{len(region)} | Last finished: {reg}\x1b[K",
            end="\r"
        )
                    
            print(self.table)
        except KeyboardInterrupt:
            self.logger.warning('Script is Stopped...')
        except ClientError as e:
            error = e.response['Error']['Code']
            self.logger.error(f'AWS ERROR : {error}')

if __name__ == '__main__':
    kmscollect = KMSCollector()
    kmscollect.run()

        