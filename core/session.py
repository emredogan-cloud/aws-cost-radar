import boto3
from typing import TYPE_CHECKING, overload, Literal


if TYPE_CHECKING:
    from mypy_boto3_ec2 import EC2Client
    from mypy_boto3_rds import RDSClient

AWSService = Literal['ec2', 'rds']

class AWSSessionManager:
    _instance = None
    
    def __init__(self):
        self._session = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_session(self, region: str = "us-east-1") -> boto3.Session:
        if region not in self._session:
            self._session[region] = boto3.Session(region_name=region)
        return self._session[region]

    
    @overload
    def get_client(self, service_name: Literal['ec2'], region: str = "us-east-1") -> "EC2Client": ...

    @overload
    def get_client(self, service_name: Literal['rds'], region: str = "us-east-1") -> "RDSClient": ...

    def get_client(self, service_name: AWSService, region: str = "us-east-1"):
        session = self.get_session(region)
        return session.client(service_name)