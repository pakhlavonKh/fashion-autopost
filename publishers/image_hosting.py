"""Image hosting service protocol and implementations.

Per SDD §3.6 & SRS §5.5.
Ensures image URLs are public, permanent HTTPS URLs required by Instagram Graph API.
"""

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable
import uuid
import httpx

logger = logging.getLogger(__name__)


@runtime_checkable
class ImageHostingService(Protocol):
    """Protocol for ensuring an image is accessible via a public HTTPS URL."""

    def ensure_public_url(self, photo_url_or_path: str) -> str:
        """Return a verified public HTTPS URL for the image."""
        ...


_FACEBOOK_CRAWLER = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"


class LitterboxImageHost:
    """Uploads a local JPEG to a public HTTPS URL that Instagram's crawler can download.

    Meta rejects a post with error 9004 when it cannot fetch image_url. litterbox
    often answers the crawler with 403, so the permanent catbox host is first.
    A host that fails upload or does not serve a real JPEG is skipped for the
    rest of the process. 0x0.st and litterbox remain as backups.
    """

    UPLOAD_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
    FALLBACK_URL = "https://catbox.moe/user/api.php"
    ZEROX_URL = "https://0x0.st"

    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._disabled: set[str] = set()
        self._by_url: dict[str, Path] = {}
        self._host_for_url: dict[str, str] = {}

    def ensure_public_url(self, photo_url_or_path: str) -> str:
        local_path = Path(photo_url_or_path)
        if not local_path.is_file():
            url = photo_url_or_path.strip()
            if url.startswith("https://"):
                return url
            raise ValueError(f"Instagram image is not a local file or public HTTPS URL: {url}")
        return self._upload_first_working(local_path)

    def replace_unfetchable(self, public_url: str) -> str:
        """Upload the same file again after Instagram failed to download public_url."""
        local_path = self._by_url.get(public_url)
        if local_path is None or not local_path.is_file():
            raise RuntimeError(f"No local file left for unfetchable Instagram image {public_url}")
        failed_host = self._host_for_url.get(public_url)
        if failed_host:
            self._disabled.add(failed_host)
            logger.warning(
                "Instagram could not fetch %s from %s. Trying another image host.",
                public_url,
                failed_host,
            )
        return self._upload_first_working(local_path)

    def _upload_first_working(self, local_path: Path) -> str:
        errors: list[str] = []
        for name, upload in self._hosts():
            if name in self._disabled:
                continue
            try:
                public_url = upload(local_path)
                if not self._is_public_jpeg(public_url):
                    raise RuntimeError(f"URL is not a public JPEG: {public_url}")
            except Exception as exc:
                self._disabled.add(name)
                errors.append(f"{name}: {exc}")
                logger.warning("Image host %s failed: %s", name, exc)
                continue
            self._by_url[public_url] = local_path
            self._host_for_url[public_url] = name
            logger.info("Uploaded Instagram image via %s to %s", name, public_url)
            return public_url
        detail = "; ".join(errors) or "no image hosts left"
        raise RuntimeError(f"Could not publish a public JPEG for Instagram: {detail}")

    def _hosts(self):
        return (
            ("catbox", self._upload_catbox),
            ("0x0", self._upload_0x0),
            ("litterbox", self._upload_litterbox),
        )

    def _upload_catbox(self, local_path: Path) -> str:
        return self._upload_multipart(
            local_path,
            self.FALLBACK_URL,
            {"reqtype": "fileupload"},
            "fileToUpload",
        )

    def _upload_litterbox(self, local_path: Path) -> str:
        return self._upload_multipart(
            local_path,
            self.UPLOAD_URL,
            {"reqtype": "fileupload", "time": "24h"},
            "fileToUpload",
        )

    def _upload_0x0(self, local_path: Path) -> str:
        return self._upload_multipart(local_path, self.ZEROX_URL, {}, "file")

    def _upload_multipart(self, local_path: Path, upload_url: str, data: dict, field: str) -> str:
        with local_path.open("rb") as handle:
            files = {field: (local_path.name, handle, "image/jpeg")}
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
                resp = client.post(upload_url, data=data, files=files)
                resp.raise_for_status()
        public_url = resp.text.strip()
        if not public_url.startswith("https://"):
            raise RuntimeError(f"Image host did not return a public URL: {public_url[:180]}")
        return public_url

    def _is_public_jpeg(self, url: str) -> bool:
        """True when Facebook's crawler would receive a JPEG, not an error page."""
        try:
            with httpx.Client(timeout=min(self.timeout_seconds, 30.0), follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": _FACEBOOK_CRAWLER})
        except Exception as exc:
            logger.warning("Could not verify Instagram image %s: %s", url, exc)
            return False
        return resp.status_code == 200 and len(resp.content) > 200 and resp.content[:3] == b"\xff\xd8\xff"



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
