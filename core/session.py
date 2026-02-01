import boto3
from typing import TYPE_CHECKING, overload, Literal

# Bu blok sadece sen kod yazarken çalışır (IDE için),
# Kod çalıştırıldığında (Runtime) burası atlanır, performans kaybı olmaz.
if TYPE_CHECKING:
    from mypy_boto3_ec2 import EC2Client
    from mypy_boto3_rds import RDSClient
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_pricing import PricingClient

AWSService = Literal['ec2','cloudwatch','s3','rds','pricing']

class AWSSessionManager:
    _instance = None
    
    def __init__(self):
        self._session = {}

    @classmethod
    def get_instance(cls):
        """Singleton Pattern: Her yerden aynı yöneticiye ulaşmak için."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_session(self, region: str = "us-east-1") -> boto3.Session:
        if region not in self._session:
            # Burada ileride profil veya assume role mantığı ekleyebilirsin
            self._session[region] = boto3.Session(region_name=region)
        return self._session[region]

    # --- SİHİRLİ KISIM: OVERLOADLAR ---
    # IDE'ye diyoruz ki: Eğer sana 'ec2' stringi gelirse, EC2Client tipinde dön.
    
    @overload
    def get_client(self, service_name: Literal['ec2'], region: str = "us-east-1") -> "EC2Client": ...

    @overload
    def get_client(self, service_name: Literal['rds'], region: str = "us-east-1") -> "RDSClient": ...

    @overload
    def get_client(self, service_name: Literal['s3'], region: str = "us-east-1") -> "S3Client": ...

    @overload
    def get_client(self, service_name: Literal['cloudwatch'], region: str = "us-east-1") -> "CloudWatchClient": ...

    @overload
    def get_client(self, service_name: Literal['pricing'], region: str = "us-east-1") -> "PricingClient": ...

    # --- GERÇEK ÇALIŞAN KOD ---
    def get_client(self, service_name: AWSService, region: str = "us-east-1"):
        session = self.get_session(region)
        return session.client(service_name)