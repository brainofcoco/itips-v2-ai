"""Dahua camera clients — events, HTTP, face DB, plate DB, PTZ, deterrence."""

from .dahua_http import DahuaCameraEndpoint, endpoints_from_settings
from .dahua_face_db import DahuaFaceDB, DahuaFaceDBError, Person
from .dahua_plate_db import (
    BLACK_LIST,
    RED_LIST,
    DahuaPlateDB,
    DahuaPlateDBError,
    PlateRecord,
)
from .dahua_deterrence import DahuaDeterrence, DeterrenceStatus
from .dahua_ptz import DahuaPTZ, PresetInfo

__all__ = [
    "DahuaCameraEndpoint",
    "endpoints_from_settings",
    "DahuaFaceDB",
    "DahuaFaceDBError",
    "Person",
    "DahuaPlateDB",
    "DahuaPlateDBError",
    "PlateRecord",
    "RED_LIST",
    "BLACK_LIST",
    "DahuaDeterrence",
    "DeterrenceStatus",
    "DahuaPTZ",
    "PresetInfo",
]
