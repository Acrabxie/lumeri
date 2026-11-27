"""Backend surfaces for graph-targeted video rendering."""
from lumeri.video.backends.base import RenderBackend, RenderProfile, RenderResult
from lumeri.video.backends.opencv_flow import (
    FlowTrackingAnalysisResult,
    OpenCVFlowTrackingBackend,
    render_opencv_flow_tracking_backend_manifest,
)
from lumeri.video.backends.selection import (
    BackendDecision,
    GRAPH_NATIVE_SOFTWARE_STRATEGY,
    SUPPORTED_RENDER_BACKENDS,
    choose_render_backend,
)
from lumeri.video.backends.software import SoftwareGraphBackend, SoftwareRenderBackend

__all__ = [
    "BackendDecision",
    "FlowTrackingAnalysisResult",
    "GRAPH_NATIVE_SOFTWARE_STRATEGY",
    "OpenCVFlowTrackingBackend",
    "RenderBackend",
    "RenderProfile",
    "RenderResult",
    "SUPPORTED_RENDER_BACKENDS",
    "SoftwareGraphBackend",
    "SoftwareRenderBackend",
    "choose_render_backend",
    "render_opencv_flow_tracking_backend_manifest",
]
