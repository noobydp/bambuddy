"""Printables.com model metadata and download service.

Printables exposes the model-detail data used by its own web application
through a public GraphQL endpoint.  Bambuddy uses that endpoint for
interoperability: resolve a pasted model URL, list the model files, request a
short-lived download URL for the selected file, and save it to the library.

The integration is anonymous and limited to public, freely downloadable model
files.  It does not attempt to bypass paid/premium access controls and is not
affiliated with or endorsed by Printables or Prusa Research.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

PRINTABLES_GRAPHQL_URL = "https://api.printables.com/graphql/"
PRINTABLES_HOSTS = {"printables.com", "www.printables.com"}
PRINTABLES_MEDIA_HOSTS = {"media.printables.com", "files.printables.com"}

_MODEL_ID_RE = re.compile(r"/model/(\d+)")
_MAX_GRAPHQL_BYTES = 5 * 1024 * 1024
_MAX_MODEL_BYTES = 200 * 1024 * 1024
_MAX_THUMBNAIL_BYTES = 10 * 1024 * 1024
_SUPPORTED_MODEL_EXTENSIONS = {".3mf", ".stl", ".step", ".stp"}
_REFUSED_THUMBNAIL_MIMES = ("text/html", "text/plain", "application/json")

_CLIENT_HEADERS = {
    "User-Agent": "Bambuddy/1.0 (+https://github.com/maziggy/bambuddy)",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.printables.com",
    "Referer": "https://www.printables.com/",
}

_MODEL_QUERY = """
query BambuddyPrintablesModel($id: ID!) {
  print(id: $id) {
    id
    name
    slug
    summary
    description
    downloadCount
    filesCount
    premium
    price
    license { name abbreviation }
    user { publicUsername }
    image { filePath }
    images { filePath }
    stls { id name fileSize folder }
  }
}
""".strip()

_DOWNLOAD_MUTATION = """
mutation BambuddyPrintablesDownload(
  $printId: ID!,
  $source: DownloadSourceEnum!,
  $files: [DownloadFileInput]
) {
  getDownloadLink(printId: $printId, source: $source, files: $files) {
    ok
    errors { field messages }
    output {
      link
      ttl
      files { id link ttl fileType }
    }
  }
}
""".strip()

_shared_http_client: httpx.AsyncClient | None = None


def set_shared_http_client(client: httpx.AsyncClient | None) -> None:
    """Register the app-scoped HTTP client used by service instances."""

    global _shared_http_client
    _shared_http_client = client


class PrintablesError(Exception):
    """Base exception for Printables integration failures."""


class PrintablesUrlError(PrintablesError):
    """Raised when the input is not a Printables model URL."""


class PrintablesNotFoundError(PrintablesError):
    """Raised when a model or selected file no longer exists."""


class PrintablesForbiddenError(PrintablesError):
    """Raised when Printables denies a paid, premium, or gated download."""


class PrintablesUnavailableError(PrintablesError):
    """Raised for transient network/provider failures or malformed responses."""


def _graphql_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return None
    messages = []
    for error in errors[:3]:
        if not isinstance(error, dict):
            continue
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
    return "; ".join(messages) or None


def _mutation_error_message(errors: Any) -> str | None:
    if not isinstance(errors, list):
        return None
    messages = []
    for error in errors[:3]:
        if not isinstance(error, dict):
            continue
        value = error.get("messages")
        if isinstance(value, list):
            messages.extend(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            messages.append(value.strip())
    return "; ".join(messages) or None


def _media_url(path: str | None) -> str:
    if not isinstance(path, str) or not path.strip():
        return ""
    value = path.strip()
    if value.startswith(("https://", "http://")):
        return value
    return f"https://media.printables.com/{value.lstrip('/')}"


class PrintablesService:
    """Async client for Printables' public model-detail flow."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        if client is not None:
            self._client = client
            self._owns_client = False
        elif _shared_http_client is not None:
            self._client = _shared_http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=30.0)
            self._owns_client = True

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def parse_url(url: str) -> int:
        raw = (url or "").strip()
        if not raw:
            raise PrintablesUrlError("Enter a Printables.com model URL")
        if "://" not in raw:
            raw = f"https://{raw}"
        parsed = urlparse(raw)
        if (parsed.hostname or "").lower() not in PRINTABLES_HOSTS:
            raise PrintablesUrlError("URL must be a Printables.com model page")
        match = _MODEL_ID_RE.search(parsed.path)
        if not match:
            raise PrintablesUrlError("Printables URL must contain /model/{id}")
        return int(match.group(1))

    @staticmethod
    def canonical_url(model: dict[str, Any]) -> str:
        model_id = model.get("id")
        slug = model.get("slug")
        suffix = f"-{slug}" if isinstance(slug, str) and slug.strip() else ""
        return f"https://www.printables.com/model/{model_id}{suffix}"

    @staticmethod
    def canonical_file_url(model_id: int, file_id: int) -> str:
        return f"https://www.printables.com/model/{model_id}#file-{file_id}"

    async def _post(self, query: str, variables: dict[str, Any], operation_name: str) -> dict[str, Any]:
        try:
            response = await self._client.post(
                PRINTABLES_GRAPHQL_URL,
                json={
                    "query": query,
                    "variables": variables,
                    "operationName": operation_name,
                },
                headers=_CLIENT_HEADERS,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PrintablesUnavailableError(f"Printables request failed: {exc}") from exc

        if len(response.content) > _MAX_GRAPHQL_BYTES:
            raise PrintablesUnavailableError("Printables response exceeded the size limit")
        if response.status_code == 429:
            raise PrintablesUnavailableError("Printables is rate-limiting requests; try again shortly")
        if response.status_code in (401, 403):
            raise PrintablesForbiddenError("Printables refused this request")
        if response.status_code >= 500:
            raise PrintablesUnavailableError(f"Printables returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise PrintablesUnavailableError("Printables returned invalid JSON") from exc

        graph_error = _graphql_error_message(payload)
        if graph_error:
            raise PrintablesUnavailableError(f"Printables error: {graph_error}")
        if response.status_code >= 400:
            raise PrintablesUnavailableError(f"Printables returned HTTP {response.status_code}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise PrintablesUnavailableError("Printables response was missing data")
        return data

    async def get_model(self, model_id: int) -> dict[str, Any]:
        data = await self._post(
            _MODEL_QUERY,
            {"id": str(model_id)},
            "BambuddyPrintablesModel",
        )
        model = data.get("print")
        if not isinstance(model, dict):
            raise PrintablesNotFoundError(f"Printables model {model_id} was not found")
        return model

    @staticmethod
    def list_supported_files(model: dict[str, Any]) -> list[dict[str, Any]]:
        """Return sliceable model files with stable, frontend-friendly fields.

        Printables calls the collection ``stls`` even when a row is a 3MF.
        The filename is therefore the source of truth for the actual format;
        the GraphQL download input remains ``fileType: stl`` for every row in
        this collection.
        """

        cover = _media_url((model.get("image") or {}).get("filePath"))
        pictures = []
        for image in model.get("images") or []:
            if not isinstance(image, dict):
                continue
            url = _media_url(image.get("filePath"))
            if url:
                pictures.append({"name": "image", "url": url})

        files = []
        for item in model.get("stls") or []:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            name = item.get("name")
            if not str(raw_id).isdigit() or not isinstance(name, str):
                continue
            extension = os.path.splitext(name)[1].lower()
            if extension not in _SUPPORTED_MODEL_EXTENSIONS:
                continue
            file_id = int(raw_id)
            files.append(
                {
                    "id": file_id,
                    "profileId": file_id,
                    "title": name,
                    "cover": cover,
                    "pictures": pictures,
                    "fileSize": item.get("fileSize"),
                    "fileExtension": extension.lstrip(".").upper(),
                    "folder": item.get("folder") or "",
                }
            )
        return files

    @staticmethod
    def normalize_design(model: dict[str, Any]) -> dict[str, Any]:
        user = model.get("user") or {}
        license_data = model.get("license") or {}
        summary = model.get("description") or model.get("summary") or ""
        return {
            "id": int(model["id"]),
            "title": model.get("name") or "",
            "summary": summary,
            "coverUrl": _media_url((model.get("image") or {}).get("filePath")),
            "downloadCount": model.get("downloadCount"),
            "license": license_data.get("name") or license_data.get("abbreviation") or "",
            "designCreator": {"name": user.get("publicUsername") or ""},
            "premium": bool(model.get("premium")),
            "price": model.get("price"),
        }

    async def get_download_link(self, model_id: int, file_id: int) -> str:
        data = await self._post(
            _DOWNLOAD_MUTATION,
            {
                "printId": str(model_id),
                "source": "model_detail",
                "files": [{"fileType": "stl", "ids": [str(file_id)]}],
            },
            "BambuddyPrintablesDownload",
        )
        result = data.get("getDownloadLink")
        if not isinstance(result, dict):
            raise PrintablesUnavailableError("Printables did not return a download result")
        if result.get("ok") is not True:
            detail = _mutation_error_message(result.get("errors"))
            raise PrintablesForbiddenError(detail or "This Printables file cannot be downloaded anonymously")
        output = result.get("output") or {}
        files = output.get("files") or []
        for item in files:
            if isinstance(item, dict) and str(item.get("id")) == str(file_id):
                link = item.get("link")
                if isinstance(link, str) and link:
                    return link
        link = output.get("link")
        if isinstance(link, str) and link:
            return link
        raise PrintablesUnavailableError("Printables did not return a file download URL")

    async def download_model_file(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in PRINTABLES_MEDIA_HOSTS:
            raise PrintablesUnavailableError("Printables returned an untrusted download host")
        try:
            response = await self._client.get(
                url,
                headers={"User-Agent": _CLIENT_HEADERS["User-Agent"]},
                follow_redirects=False,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PrintablesUnavailableError(f"Printables download failed: {exc}") from exc
        if response.status_code != 200:
            raise PrintablesUnavailableError(f"Printables download returned HTTP {response.status_code}")
        if len(response.content) > _MAX_MODEL_BYTES:
            raise PrintablesUnavailableError(f"Printables file exceeds {_MAX_MODEL_BYTES // (1024 * 1024)} MB cap")
        return response.content

    async def fetch_thumbnail(self, url: str) -> tuple[bytes, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in PRINTABLES_MEDIA_HOSTS:
            raise PrintablesUrlError("Thumbnail URL must use a Printables media host")
        try:
            response = await self._client.get(
                url,
                headers={"User-Agent": _CLIENT_HEADERS["User-Agent"]},
                follow_redirects=False,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise PrintablesUnavailableError(f"Printables thumbnail failed: {exc}") from exc
        if response.status_code != 200:
            raise PrintablesUnavailableError(f"Printables thumbnail returned HTTP {response.status_code}")
        payload = response.content
        if len(payload) > _MAX_THUMBNAIL_BYTES:
            raise PrintablesUnavailableError("Printables thumbnail exceeds the 10 MB cap")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/") or content_type in _REFUSED_THUMBNAIL_MIMES:
            raise PrintablesUnavailableError("Printables thumbnail response was not an image")
        return payload, content_type
