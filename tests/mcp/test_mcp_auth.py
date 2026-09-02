"""
MCP Authentication Management Unit Tests
"""

import pytest
import time
from smartclaw.mcp.auth import McpAuth
from smartclaw.mcp.oauth2 import McpOAuth2ClientCredentials


@pytest.fixture(autouse=True)
def clean_auth():
    """Clear authentication info before each test"""
    McpAuth.clear()
    McpOAuth2ClientCredentials._registrations.clear()
    yield
    McpAuth.clear()
    McpOAuth2ClientCredentials._registrations.clear()


class TestMcpAuth:
    """Test MCP Authentication Management"""
    
    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test setting and getting authentication info"""
        tokens = {"access_token": "token123", "refresh_token": "refresh123"}
        await McpAuth.set("test_server", tokens, expires_in=3600)
        
        entry = await McpAuth.get("test_server")
        assert entry is not None
        assert entry.server_name == "test_server"
        assert entry.tokens == tokens
        assert entry.expires_at is not None
    
    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        """Test getting non-existent authentication info"""
        entry = await McpAuth.get("nonexistent")
        assert entry is None
    
    @pytest.mark.asyncio
    async def test_remove(self):
        """Test removing authentication info"""
        tokens = {"access_token": "token123"}
        await McpAuth.set("test_server", tokens)
        
        await McpAuth.remove("test_server")
        entry = await McpAuth.get("test_server")
        assert entry is None
    
    @pytest.mark.asyncio
    async def test_is_token_expired(self):
        """Test token expiration check"""
        # Set long-lived token (expires in 3600s)
        tokens = {"access_token": "token123"}
        await McpAuth.set("test_server", tokens, expires_in=3600)
        
        # Check immediately, should not be expired
        expired = await McpAuth.is_token_expired("test_server")
        assert not expired
        
        # Short-lived tokens should not be considered expired immediately.
        await McpAuth.set("test_server_expiring", tokens, expires_in=200)
        expired = await McpAuth.is_token_expired("test_server_expiring")
        assert not expired

        # Once the same token is near the end of its dynamic refresh window, expire it.
        entry = await McpAuth.get("test_server_expiring")
        assert entry is not None
        entry.expires_at = time.time() + 5
        expired = await McpAuth.is_token_expired("test_server_expiring")
        assert expired
        
        # Set token without expiration
        await McpAuth.set("test_server2", tokens, expires_in=None)
        expired = await McpAuth.is_token_expired("test_server2")
        assert not expired
        
        # Non-existent server
        expired = await McpAuth.is_token_expired("nonexistent")
        assert not expired
    
    @pytest.mark.asyncio
    async def test_list_all(self):
        """Test listing all authentication info"""
        await McpAuth.set("server1", {"token": "1"})
        await McpAuth.set("server2", {"token": "2"})
        
        all_auth = await McpAuth.list_all()
        assert len(all_auth) == 2
        assert "server1" in all_auth
        assert "server2" in all_auth
    
    def test_clear(self):
        """Test clearing all authentication info"""
        McpAuth._auth_storage["test"] = None
        McpAuth.clear()
        assert len(McpAuth._auth_storage) == 0


class TestMcpOAuth2ClientCredentials:
    """Test MCP OAuth2 Client Credentials support."""

    @pytest.mark.asyncio
    async def test_registers_client_and_requests_token(self, monkeypatch):
        calls = []

        class FakeResponse:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class FakeAsyncClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, **kwargs):
                calls.append((url, kwargs))
                if url.endswith("/register"):
                    return FakeResponse(200, {"client_id": "mcp-client", "client_secret": "secret"})
                return FakeResponse(200, {"access_token": "token-123", "expires_in": 3600})

        monkeypatch.setattr("smartclaw.mcp.oauth2.httpx.AsyncClient", FakeAsyncClient)

        token = await McpOAuth2ClientCredentials.get_access_token(
            "ais-mcp",
            {
                "type": "oauth2_client_credentials",
                "registration_url": "http://auth.example.com/oauth2/register",
                "token_url": "http://auth.example.com/oauth2/token",
                "audience": "mcp-server",
            },
        )

        assert token == "token-123"
        assert calls[0][0] == "http://auth.example.com/oauth2/register"
        assert calls[1][0] == "http://auth.example.com/oauth2/token"
        assert calls[1][1]["data"]["grant_type"] == "client_credentials"
        assert calls[1][1]["data"]["audience"] == "mcp-server"
        assert calls[1][1]["headers"]["Authorization"] == "Basic bWNwLWNsaWVudDpzZWNyZXQ="

        cached_token = await McpOAuth2ClientCredentials.get_access_token(
            "ais-mcp",
            {
                "type": "oauth2_client_credentials",
                "registration_url": "http://auth.example.com/oauth2/register",
                "token_url": "http://auth.example.com/oauth2/token",
                "audience": "mcp-server",
            },
        )
        assert cached_token == "token-123"
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_stale_dynamic_registration_is_replaced_after_token_401(self, monkeypatch):
        calls = []
        registrations = iter([
            {"client_id": "old-client", "client_secret": "old-secret"},
            {"client_id": "new-client", "client_secret": "new-secret"},
        ])
        token_attempt = 0

        class FakeResponse:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class FakeAsyncClient:
            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def post(self, url, **kwargs):
                nonlocal token_attempt
                calls.append((url, kwargs))
                if url.endswith("/register"):
                    return FakeResponse(200, next(registrations))

                token_attempt += 1
                if token_attempt == 1:
                    return FakeResponse(200, {"access_token": "old-token", "expires_in": 60})
                if token_attempt == 2:
                    return FakeResponse(401, {"error": "invalid_client"})
                return FakeResponse(200, {"access_token": "new-token", "expires_in": 60})

        monkeypatch.setattr("smartclaw.mcp.oauth2.httpx.AsyncClient", FakeAsyncClient)

        auth_config = {
            "type": "oauth2_client_credentials",
            "registration_url": "http://auth.example.com/oauth2/register",
            "token_url": "http://auth.example.com/oauth2/token",
            "audience": "mcp-server",
        }

        token = await McpOAuth2ClientCredentials.get_access_token("ais-mcp", auth_config)
        assert token == "old-token"

        entry = await McpAuth.get("ais-mcp")
        assert entry is not None
        entry.expires_at = time.time() - 1

        token = await McpOAuth2ClientCredentials.get_access_token("ais-mcp", auth_config)

        assert token == "new-token"
        assert [url for url, _ in calls].count("http://auth.example.com/oauth2/register") == 2
        assert [url for url, _ in calls].count("http://auth.example.com/oauth2/token") == 3
        assert McpOAuth2ClientCredentials._registrations["ais-mcp"]["client_id"] == "new-client"
