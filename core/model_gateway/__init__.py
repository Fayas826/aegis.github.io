from .base_provider import BaseLLMProvider

__all__ = ["model_gateway", "BaseLLMProvider", "CloudProvider", "LocalProvider"]


def __getattr__(name):
    """Inspect/use a local provider without initializing cloud/model routing."""
    if name == "LocalProvider":
        from .local_provider import LocalProvider
        return LocalProvider
    if name == "CloudProvider":
        from .cloud_provider import CloudProvider
        return CloudProvider
    if name == "model_gateway":
        from .model_router import model_gateway
        return model_gateway
    raise AttributeError(name)
