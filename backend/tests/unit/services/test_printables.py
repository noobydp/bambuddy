"""Tests for the Printables model metadata and download client."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.app.services.printables import (
    PRINTABLES_GRAPHQL_URL,
    PrintablesNotFoundError,
    PrintablesService,
    PrintablesUnavailableError,
    PrintablesUrlError,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestParseUrl:
    def test_accepts_slug_and_locale_free_url(self):
        assert (
            PrintablesService.parse_url("https://www.printables.com/model/180860-35-silicone-cartridge-holders")
            == 180860
        )

    def test_accepts_scheme_omitted(self):
        assert PrintablesService.parse_url("printables.com/model/180860") == 180860

    def test_rejects_non_printables_host(self):
        with pytest.raises(PrintablesUrlError):
            PrintablesService.parse_url("https://example.com/model/180860")

    def test_rejects_non_model_path(self):
        with pytest.raises(PrintablesUrlError):
            PrintablesService.parse_url("https://www.printables.com/@designer")


@pytest.mark.asyncio
async def test_get_model_uses_public_graphql_and_honest_identity():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PRINTABLES_GRAPHQL_URL
        assert request.headers["user-agent"].startswith("Bambuddy/")
        assert "Mozilla" not in request.headers["user-agent"]
        body = json.loads(request.content)
        assert body["operationName"] == "BambuddyPrintablesModel"
        assert body["variables"] == {"id": "180860"}
        return httpx.Response(
            200,
            json={"data": {"print": {"id": "180860", "name": "Holder"}}},
        )

    client = _client(handler)
    service = PrintablesService(client=client)
    try:
        model = await service.get_model(180860)
    finally:
        await client.aclose()
    assert model["name"] == "Holder"


@pytest.mark.asyncio
async def test_get_model_maps_null_to_not_found():
    client = _client(lambda _request: httpx.Response(200, json={"data": {"print": None}}))
    service = PrintablesService(client=client)
    try:
        with pytest.raises(PrintablesNotFoundError):
            await service.get_model(999)
    finally:
        await client.aclose()


def test_normalizes_design_and_filters_to_sliceable_model_files():
    model = {
        "id": "180860",
        "name": "Cartridge holders",
        "slug": "cartridge-holders",
        "description": "<p>Useful holders</p>",
        "downloadCount": 217,
        "license": {"name": "GPL v2"},
        "user": {"publicUsername": "PaSe"},
        "image": {"filePath": "media/prints/180860/images/cover.jpg"},
        "images": [{"filePath": "media/prints/180860/images/one.jpg"}],
        # Printables calls this collection "stls", but it also contains 3MF.
        "stls": [
            {"id": "10", "name": "holder.stl", "fileSize": 100, "folder": ""},
            {"id": "11", "name": "coloured.3mf", "fileSize": 200, "folder": ""},
            {"id": "12", "name": "readme.pdf", "fileSize": 300, "folder": ""},
        ],
    }

    design = PrintablesService.normalize_design(model)
    files = PrintablesService.list_supported_files(model)

    assert design["title"] == "Cartridge holders"
    assert design["designCreator"] == {"name": "PaSe"}
    assert design["coverUrl"] == "https://media.printables.com/media/prints/180860/images/cover.jpg"
    assert [item["title"] for item in files] == ["holder.stl", "coloured.3mf"]
    assert [item["fileExtension"] for item in files] == ["STL", "3MF"]
    assert files[0]["profileId"] == 10


@pytest.mark.asyncio
async def test_get_download_link_selects_requested_file():
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["variables"]["files"] == [{"fileType": "stl", "ids": ["764187"]}]
        return httpx.Response(
            200,
            json={
                "data": {
                    "getDownloadLink": {
                        "ok": True,
                        "errors": None,
                        "output": {
                            "files": [
                                {
                                    "id": "764187",
                                    "link": "https://files.printables.com/media/model.stl",
                                    "fileType": "stl",
                                }
                            ]
                        },
                    }
                }
            },
        )

    client = _client(handler)
    service = PrintablesService(client=client)
    try:
        link = await service.get_download_link(180860, 764187)
    finally:
        await client.aclose()
    assert link == "https://files.printables.com/media/model.stl"


@pytest.mark.asyncio
async def test_download_rejects_untrusted_host_before_request():
    client = _client(lambda _request: pytest.fail("untrusted URL must not be fetched"))
    service = PrintablesService(client=client)
    try:
        with pytest.raises(PrintablesUnavailableError, match="untrusted"):
            await service.download_model_file("https://example.com/model.stl")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_thumbnail_requires_image_content_type():
    client = _client(
        lambda _request: httpx.Response(
            200,
            content=b"<html>challenge</html>",
            headers={"content-type": "text/html"},
        )
    )
    service = PrintablesService(client=client)
    try:
        with pytest.raises(PrintablesUnavailableError, match="not an image"):
            await service.fetch_thumbnail("https://media.printables.com/media/prints/180860/cover.jpg")
    finally:
        await client.aclose()
