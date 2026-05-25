"""Models: YOLO11n + ByteTrack, InsightFace, plate recognition, face auth."""

from .face import FaceRecognitionEngine, FaceResult
from .face_authorizer import FaceAuthorizer
from .plate import PlateRecognizerClient
from .yolo import Detection, YOLOEngine, YOLOResult

__all__ = [
    "Detection",
    "FaceAuthorizer",
    "FaceRecognitionEngine",
    "FaceResult",
    "PlateRecognizerClient",
    "YOLOEngine",
    "YOLOResult",
]
