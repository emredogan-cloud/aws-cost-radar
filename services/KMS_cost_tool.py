from core.logging import get_logger
from core.session import AWSSessionManager
from dataclasses import dataclass,field
from prettytable import PrettyTable
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor,as_completed
from typing import Optional


@dataclass
class KMSFinding:
    region: str
    key_id: str
    alias: str
    key_arn: str
    key_state: str = "UNKNOWN"
    key_usage: str = "UNKNOWN"
    key_spec: str = "UNKNOWN"
    key_manager: str = "UNKNOWN"
    origin: str = "UNKNOWN"
    rotation_reason: str = "N/A"
    rotation_enabled: Optional[bool] = None

    

class KMSCollector:
    def __init__(self) -> None:
        self.manager = AWSSessionManager.get_instance()
        self.logger = get_logger('KMS_COLLECTOR' , 'INFO')
        self.ec2 = self.manager.get_client('ec2')
        self.kms = self.manager.get_client('kms')
        self.table = PrettyTable(["Region", "Alias", "KeyId", "Mgr", "State", "Rotation", "Reason"])

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


    def describe_key_meta(self, kms_client, key_id: str) -> dict:
        try:
            resp = kms_client.describe_key(KeyId=key_id)
            meta = resp["KeyMetadata"]

            key_spec = meta.get("KeySpec") or meta.get("CustomerMasterKeySpec") or "UNKNOWN"

            return {
                "key_state": meta.get("KeyState", "UNKNOWN"),
                "key_usage": meta.get("KeyUsage", "UNKNOWN"),
                "key_manager": meta.get("KeyManager", "UNKNOWN"),
                "origin": meta.get("Origin", "UNKNOWN"),
                "key_spec": key_spec,
            }
        except ClientError as e:
            code = e.response["Error"]["Code"]
            self.logger.debug(f"describe_key failed for {key_id}: {code}")
            return {
                "key_state": "ERROR",
                "key_usage": "UNKNOWN",
                "key_manager": "UNKNOWN",
                "origin": "UNKNOWN",
                "key_spec": "UNKNOWN",
            }


    def _short(self, s: str, head: int = 8, tail: int = 4) -> str:
        if not s or len(s) <= head + tail + 3:
            return s
        return f"{s[:head]}...{s[-tail:]}"


    def rotation_applicability(self, meta: dict) -> tuple[bool, str]:
        key_manager = meta.get("key_manager")
        key_usage = meta.get("key_usage")
        origin = meta.get("origin")
        key_spec = meta.get("key_spec")

        if key_manager == "AWS":
            return False, "AWS_MANAGED"

        if key_usage != "ENCRYPT_DECRYPT":
            return False, "NOT_ENCRYPT_DECRYPT"

        if origin == "EXTERNAL":
            return False, "IMPORTED_KEY_MATERIAL"

        asymmetric_prefixes = ("RSA_", "ECC_", "HMAC_")
        if isinstance(key_spec, str) and key_spec.startswith(asymmetric_prefixes):
            return False, "ASYMMETRIC_OR_HMAC"

        return True, "APPLICABLE"

        
    
    def list_keys(self,kms_client):
        keys_list = []
        for page in kms_client.get_paginator('list_keys').paginate():
            for key in page['Keys']:
                keys_list.append(key)
        return keys_list


    def scan_region(self, reg: str):
        kms = self.manager.get_client("kms", region=reg)
        findings: list[KMSFinding] = []

        alias_map = self.kms_alias(kms)
        keys = self.list_keys(kms)

        for key in keys:
            key_id = key["KeyId"]
            key_arn = key["KeyArn"]
            alias = alias_map.get(key_id, "No Alias")

            meta = self.describe_key_meta(kms, key_id)
            applicable, reason = self.rotation_applicability(meta)

            rotation = None
            rotation_reason = reason

            if applicable:
                rotation = self.get_rotation_status(kms, key_id)
                if rotation is True:
                    rotation_reason = "ENABLED"
                elif rotation is False:
                    rotation_reason = "DISABLED"
                else:
                    rotation_reason = "UNKNOWN_OR_NO_PERMISSION"

            findings.append(
                KMSFinding(
                    region=reg,
                    key_id=key_id,
                    alias=alias,
                    key_arn=key_arn,
                    key_state=meta["key_state"],
                    key_usage=meta["key_usage"],
                    key_spec=meta["key_spec"],
                    key_manager=meta["key_manager"],
                    origin=meta["origin"],
                    rotation_enabled=rotation,
                    rotation_reason=rotation_reason,
                )
            )

        return findings


        
    def run(self):
        all_results = []  
        try:
            regions = self.get_regions()
            max_workers = 8
            completed = 0

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_region = {
                    executor.submit(self.scan_region, reg): reg
                    for reg in regions
                }

                for future in as_completed(future_to_region):
                    reg = future_to_region[future]

                    try:
                        findings = future.result()
                        all_results.extend(findings) 
                    except ClientError as e:
                        error = e.response["Error"]["Code"]
                        self.logger.error(f'AWS ERROR: {error}')
                        continue
                    except Exception as e:
                        self.logger.error(f'ERROR..: {e}')
                        continue

                    for f in findings:
                        rotation_display = (
                            "ENABLED" if f.rotation_enabled is True
                            else "DISABLED" if f.rotation_enabled is False
                            else "N/A"
                        )
                        self.table.add_row([
                            f.region, f.alias, self._short(f.key_id),
                            f.key_manager, f.key_state, rotation_display, f.rotation_reason
                        ])

                    completed += 1
                    print(f"Progress: {completed}/{len(regions)} | Last finished: {reg}\x1b[K", end="\r")

            print() 
            print(self.table)
            return all_results 

        except KeyboardInterrupt:
            self.logger.warning("Script is Stopped...")
            return all_results
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return all_results

        
if __name__ == '__main__':
    kmscollect = KMSCollector()
    kmscollect.run()

        