"""Public Resource Monitor Agent package facade."""

from .agent import ResourceMonitorAgent, ResourceMonitorOutputError
from .catalog import ResourceCatalog
from .factory import build_resource_monitor_agent
from .response_processor import ResourceMonitorResponseProcessor
from .schemas import (
    DetectedResourceGroup,
    LearnedResourceRule,
    MonitoredOperation,
    ResourceIdentifierSummary,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorErrorSummary,
    ResourceMonitorResult,
    ResourceMonitorWarning,
    ResourceNameSummary,
    ResourceObservation,
    ResourceOperationSummary,
)
from .tool import RESOURCE_LOOKUP_TOOL_NAME, register_resource_lookup_tool

__all__ = [
    "DetectedResourceGroup",
    "LearnedResourceRule",
    "MonitoredOperation",
    "ResourceCatalog",
    "ResourceIdentifierSummary",
    "ResourceLookupRequest",
    "ResourceLookupResult",
    "ResourceMonitorErrorSummary",
    "ResourceMonitorResult",
    "ResourceMonitorAgent",
    "ResourceMonitorOutputError",
    "ResourceMonitorResponseProcessor",
    "ResourceMonitorWarning",
    "ResourceNameSummary",
    "ResourceObservation",
    "ResourceOperationSummary",
    "RESOURCE_LOOKUP_TOOL_NAME",
    "build_resource_monitor_agent",
    "register_resource_lookup_tool",
]
