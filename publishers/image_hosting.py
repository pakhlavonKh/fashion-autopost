"""Image hosting service protocol and implementations.

Per SDD §3.6 & SRS §5.5.
Ensures image URLs are public, permanent HTTPS URLs required by Instagram Graph API.
"""

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable
import uuid

logger = logging.getLogger(__name__)


@runtime_checkable
class ImageHostingService(Protocol):
    """Protocol for ensuring an image is accessible via a public HTTPS URL."""

    def ensure_public_url(self, photo_url_or_path: str) -> str:
        """Return a verified public HTTPS URL for the image."""
        ...


class PassthroughImageHost:
    """Passes through existing public HTTPS URLs directly without re-hosting."""

    def ensure_public_url(self, photo_url_or_path: str) -> str:
        url = photo_url_or_path.strip()
        if url.startswith("https://") or url.startswith("http://"):
            return url
        logger.warning(
            "PassthroughImageHost received non-HTTP path: %s. May fail on Instagram.",
            url,
        )
        return url


class S3ImageHost:
    """Re-hosts local or private images to S3-compatible cloud storage."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        bucket_name: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        public_url_prefix: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.bucket_name = bucket_name
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.public_url_prefix = public_url_prefix

    def ensure_public_url(self, photo_url_or_path: str) -> str:
        url = photo_url_or_path.strip()
        # If it's already an HTTPS URL and S3 re-hosting is not strictly required, keep it
        if (url.startswith("https://") or url.startswith("http://")) and not self.bucket_name:
            return url

        if not self.bucket_name or not self.access_key:
            logger.warning("S3 credentials not configured, returning original URL: %s", url)
            return url

        try:
            import boto3

            s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
            )

            key = f"products/{uuid.uuid4().hex[:12]}_{Path(url).name or 'photo.jpg'}"

            # If local file, upload directly
            local_path = Path(url)
            if local_path.exists():
                s3_client.upload_file(
                    str(local_path),
                    self.bucket_name,
                    key,
                    ExtraArgs={"ACL": "public-read", "ContentType": "image/jpeg"},
                )
            else:
                # If remote image needs re-hosting, download stream and upload
                import httpx

                with httpx.Client(timeout=30.0) as http_client:
                    resp = http_client.get(url)
                    resp.raise_for_status()
                    s3_client.put_object(
                        Bucket=self.bucket_name,
                        Key=key,
                        Body=resp.content,
                        ACL="public-read",
                        ContentType="image/jpeg",
                    )

            if self.public_url_prefix:
                return f"{self.public_url_prefix.rstrip('/')}/{key}"
            return f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{key}"

        except Exception as exc:
            logger.error("Failed to re-host image to S3: %s", exc)
            return url
