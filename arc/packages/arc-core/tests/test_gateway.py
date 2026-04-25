"""
Tests for arc.core.gateway — MockGatewayConnector, HttpGateway, MultiGateway.
Native after migration module 6. Foundry exercises the same code via the shim
(agent-foundry/tests/test_gateway.py).

Covers:
  - MockGatewayConnector: hit, miss, meta flag
  - HttpGateway: URL building (path vs param mode), GET/POST dispatch,
    retry with exponential back-off, exhausted retries raise RuntimeError,
    missing httpx raises ImportError, close() resets client
  - MultiGateway: longest-prefix routing, exact match, catch-all default,
    no-match raises KeyError, register() at runtime
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from arc.core.gateway import (
    DataRequest,
    DataResponse,
    MockGatewayConnector,
    MultiGateway,
    HttpGateway,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_request(source: str = "participant.data", params: dict | None = None) -> DataRequest:
    return DataRequest(source=source, params=params or {"id": "p-001"})


def make_mock_response(source: str = "participant.data", data: dict | None = None) -> DataResponse:
    return DataResponse(source=source, data=data or {"balance": 100})


# ─── MockGatewayConnector ─────────────────────────────────────────────────────

class TestMockGatewayConnector:

    @pytest.mark.asyncio
    async def test_registered_source_returns_data(self):
        gw = MockGatewayConnector({"participant.data": {"balance": 5000}})
        resp = await gw.fetch(make_request("participant.data"))
        assert resp.data == {"balance": 5000}
        assert resp.source == "participant.data"

    @pytest.mark.asyncio
    async def test_unregistered_source_raises_permission_error(self):
        gw = MockGatewayConnector({})
        with pytest.raises(PermissionError, match="not registered"):
            await gw.fetch(make_request("unknown.source"))

    @pytest.mark.asyncio
    async def test_meta_contains_mock_flag(self):
        gw = MockGatewayConnector({"plan.data": {}})
        resp = await gw.fetch(make_request("plan.data"))
        assert resp.meta.get("mock") is True

    @pytest.mark.asyncio
    async def test_cached_is_false(self):
        gw = MockGatewayConnector({"market.data": [1, 2, 3]})
        resp = await gw.fetch(make_request("market.data"))
        assert resp.cached is False

    @pytest.mark.asyncio
    async def test_empty_constructor_raises_on_any_source(self):
        gw = MockGatewayConnector()
        with pytest.raises(PermissionError):
            await gw.fetch(make_request("participant.data"))


# ─── HttpGateway — URL building ───────────────────────────────────────────────

class TestHttpGatewayUrlBuilding:

    def test_path_mode_appends_source(self):
        gw = HttpGateway("https://api.example.com", source_key="path")
        req = make_request("participant.data")
        assert gw._build_url(req) == "https://api.example.com/participant.data"

    def test_path_mode_strips_trailing_slash_from_base(self):
        gw = HttpGateway("https://api.example.com/", source_key="path")
        req = make_request("plan.data")
        assert gw._build_url(req) == "https://api.example.com/plan.data"

    def test_param_mode_returns_base_url_unchanged(self):
        gw = HttpGateway("https://api.example.com", source_key="param")
        req = make_request("fund.performance.read")
        assert gw._build_url(req) == "https://api.example.com"

    def test_repr_shows_base_url_and_method(self):
        gw = HttpGateway("https://api.example.com", method="POST")
        assert "api.example.com" in repr(gw)
        assert "POST" in repr(gw)


# ─── HttpGateway — fetch ──────────────────────────────────────────────────────

class TestHttpGatewayFetch:

    def _make_response(self, status: int = 200, json_data: dict | None = None):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_data or {"result": "ok"}
        if status >= 400:
            resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
        else:
            resp.raise_for_status.return_value = None
        return resp

    @pytest.mark.asyncio
    async def test_get_success_returns_data_response(self):
        gw = HttpGateway("https://api.example.com", retries=0)
        mock_resp = self._make_response(200, {"balance": 9999})
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        gw._client = mock_client

        resp = await gw.fetch(make_request("participant.data"))
        assert isinstance(resp, DataResponse)
        assert resp.data == {"balance": 9999}
        assert resp.source == "participant.data"
        assert resp.cached is False

    @pytest.mark.asyncio
    async def test_get_passes_params_as_query_string(self):
        gw = HttpGateway("https://api.example.com", retries=0)
        mock_resp = self._make_response(200)
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        gw._client = mock_client

        req = DataRequest(source="plan.data", params={"plan_id": "p-42"})
        await gw.fetch(req)
        mock_client.get.assert_awaited_once()
        call_kwargs = mock_client.get.call_args
        assert call_kwargs[1]["params"] == {"plan_id": "p-42"}

    @pytest.mark.asyncio
    async def test_post_sends_json_body(self):
        gw = HttpGateway("https://api.example.com", method="POST", retries=0)
        mock_resp = self._make_response(200)
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        gw._client = mock_client

        req = DataRequest(source="risk.score.compute", params={"participant_id": "p-1"})
        await gw.fetch(req)
        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs[1]["json"] == {"participant_id": "p-1"}

    @pytest.mark.asyncio
    async def test_meta_contains_status_code_and_url(self):
        gw = HttpGateway("https://api.example.com", retries=0)
        mock_resp = self._make_response(200)
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        gw._client = mock_client

        resp = await gw.fetch(make_request("participant.data"))
        assert resp.meta["status_code"] == 200
        assert "url" in resp.meta
        assert resp.meta["attempt"] == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        gw = HttpGateway("https://api.example.com", retries=2)
        good_resp = self._make_response(200, {"ok": True})
        mock_client = MagicMock()
        # Fail twice, succeed on third attempt
        mock_client.get = AsyncMock(
            side_effect=[Exception("timeout"), Exception("timeout"), good_resp]
        )
        gw._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock):
            resp = await gw.fetch(make_request("participant.data"))

        assert resp.data == {"ok": True}
        assert resp.meta["attempt"] == 3
        assert mock_client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_exhausted_retries_raise_runtime_error(self):
        gw = HttpGateway("https://api.example.com", retries=1)
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        gw._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="all.*attempts failed"):
                await gw.fetch(make_request("participant.data"))

        assert mock_client.get.await_count == 2  # 1 attempt + 1 retry

    @pytest.mark.asyncio
    async def test_missing_httpx_raises_import_error(self):
        gw = HttpGateway("https://api.example.com")
        with patch.dict("sys.modules", {"httpx": None}):
            gw._client = None
            with pytest.raises(ImportError, match="httpx"):
                gw._get_client()

    @pytest.mark.asyncio
    async def test_close_resets_client(self):
        gw = HttpGateway("https://api.example.com")
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        gw._client = mock_client

        await gw.close()
        mock_client.aclose.assert_awaited_once()
        assert gw._client is None

    @pytest.mark.asyncio
    async def test_close_is_safe_when_no_client(self):
        gw = HttpGateway("https://api.example.com")
        # Should not raise
        await gw.close()


# ─── MultiGateway ─────────────────────────────────────────────────────────────

class TestMultiGateway:

    def _mock_connector(self, data: dict) -> MagicMock:
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=DataResponse(
            source="test", data=data, cached=False
        ))
        return conn

    @pytest.mark.asyncio
    async def test_exact_prefix_routes_correctly(self):
        participant_conn = self._mock_connector({"type": "participant"})
        plan_conn = self._mock_connector({"type": "plan"})
        gw = MultiGateway({"participant": participant_conn, "plan": plan_conn})

        await gw.fetch(make_request("participant.data"))
        participant_conn.fetch.assert_awaited_once()
        plan_conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_longest_prefix_wins(self):
        short_conn = self._mock_connector({"match": "short"})
        long_conn = self._mock_connector({"match": "long"})
        gw = MultiGateway({
            "participant": short_conn,
            "participant.activity": long_conn,
        })

        await gw.fetch(make_request("participant.activity.read"))
        long_conn.fetch.assert_awaited_once()
        short_conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_catch_all_default_used_when_no_match(self):
        default_conn = self._mock_connector({"match": "default"})
        other_conn = self._mock_connector({"match": "other"})
        gw = MultiGateway({"plan": other_conn}, default=default_conn)

        await gw.fetch(make_request("market.data"))
        default_conn.fetch.assert_awaited_once()
        other_conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_match_and_no_default_raises_key_error(self):
        gw = MultiGateway({"plan": self._mock_connector({})})
        with pytest.raises(KeyError, match="no connector registered"):
            await gw.fetch(make_request("unknown.source"))

    @pytest.mark.asyncio
    async def test_register_adds_connector_at_runtime(self):
        gw = MultiGateway({})
        new_conn = self._mock_connector({"added": True})
        gw.register("market", new_conn)

        await gw.fetch(make_request("market.data"))
        new_conn.fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_replaces_existing_prefix(self):
        old_conn = self._mock_connector({"version": "old"})
        new_conn = self._mock_connector({"version": "new"})
        gw = MultiGateway({"plan": old_conn})
        gw.register("plan", new_conn)

        await gw.fetch(make_request("plan.data"))
        new_conn.fetch.assert_awaited_once()
        old_conn.fetch.assert_not_called()

    def test_repr_shows_prefixes(self):
        gw = MultiGateway({"participant": MagicMock(), "plan": MagicMock()})
        r = repr(gw)
        assert "participant" in r
        assert "plan" in r

    @pytest.mark.asyncio
    async def test_empty_string_prefix_acts_as_catch_all(self):
        default_conn = self._mock_connector({"default": True})
        gw = MultiGateway({"": default_conn})
        # Any source should match the empty-string prefix
        await gw.fetch(make_request("anything.at.all"))
        default_conn.fetch.assert_awaited_once()
