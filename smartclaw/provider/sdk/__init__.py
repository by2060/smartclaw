"""
Provider SDK implementations

This module contains all provider SDK implementations for different AI platforms.
Each provider implements the BaseProvider interface for consistent API access.

Providers:
- Core Providers (Batch 1+2):
  - OpenAI, Anthropic, Google, Azure
  - OpenAI Compatible, Mistral, Groq, Cohere, Together

- Extended Providers (Batch 3):
  - xAI, DeepInfra, Cerebras, Perplexity, OpenRouter
  - Amazon Bedrock, Google Vertex, Local

- Enterprise Providers (Batch 5):
  - Gateway, GitLab

- Additional Providers (Batch 6):
  - GitHub Copilot, GitHub Copilot Enterprise
  - Vercel AI, SmartClaw
  - SAP AI Core, Cloudflare AI Gateway
"""

# Core providers
from smartclaw.provider.sdk.openai import OpenAIProvider
from smartclaw.provider.sdk.anthropic import AnthropicProvider
from smartclaw.provider.sdk.google import GoogleProvider
from smartclaw.provider.sdk.azure import AzureProvider
from smartclaw.provider.sdk.openai_compatible import OpenAICompatibleProvider
from smartclaw.provider.sdk.mistral import MistralProvider
from smartclaw.provider.sdk.groq import GroqProvider
from smartclaw.provider.sdk.cohere import CohereProvider
from smartclaw.provider.sdk.together import TogetherProvider

# Extended providers
from smartclaw.provider.sdk.xai import XAIProvider
from smartclaw.provider.sdk.deepinfra import DeepInfraProvider
from smartclaw.provider.sdk.cerebras import CerebrasProvider
from smartclaw.provider.sdk.perplexity import PerplexityProvider
from smartclaw.provider.sdk.openrouter import OpenRouterProvider
from smartclaw.provider.sdk.bedrock import BedrockProvider
from smartclaw.provider.sdk.vertex import VertexProvider
from smartclaw.provider.sdk.local import LocalProvider

# Enterprise providers
from smartclaw.provider.sdk.gateway import GatewayProvider
from smartclaw.provider.sdk.gitlab import GitLabProvider

# Additional providers (Batch 6)
from smartclaw.provider.sdk.github_copilot import (
    GitHubCopilotProvider,
    GitHubCopilotEnterpriseProvider,
)
from smartclaw.provider.sdk.vercel import VercelProvider
from smartclaw.provider.sdk.opencode import OpenCodeProvider as SmartClawCompatProvider
from smartclaw.provider.sdk.sap_ai_core import SAPAICoreProvider
from smartclaw.provider.sdk.cloudflare_gateway import CloudflareGatewayProvider

# Final providers (Batch 7)
from smartclaw.provider.sdk.vertex_anthropic import VertexAnthropicProvider
from smartclaw.provider.sdk.azure_cognitive import AzureCognitiveServicesProvider
from smartclaw.provider.sdk.zenmux import ZenMuxProvider


__all__ = [
    # Core providers
    "OpenAIProvider",
    "AnthropicProvider",
    "GoogleProvider",
    "AzureProvider",
    "OpenAICompatibleProvider",
    "MistralProvider",
    "GroqProvider",
    "CohereProvider",
    "TogetherProvider",
    # Extended providers
    "XAIProvider",
    "DeepInfraProvider",
    "CerebrasProvider",
    "PerplexityProvider",
    "OpenRouterProvider",
    "BedrockProvider",
    "VertexProvider",
    "LocalProvider",
    # Enterprise providers
    "GatewayProvider",
    "GitLabProvider",
    # Additional providers (Batch 6)
    "GitHubCopilotProvider",
    "GitHubCopilotEnterpriseProvider",
    "VercelProvider",
    "SmartClawCompatProvider",
    "SAPAICoreProvider",
    "CloudflareGatewayProvider",
    # Final providers (Batch 7)
    "VertexAnthropicProvider",
    "AzureCognitiveServicesProvider",
    "ZenMuxProvider",
]
