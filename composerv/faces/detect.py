"""insightface detection wrapper: image path -> [(bbox, embedding)].

Impure (loads the buffalo_l model once, runs on-device via onnxruntime CPU). Validated live;
the enroll orchestration that uses it is unit-tested with a fake detector.
"""

from __future__ import annotations


class FaceDetector:
    def __init__(self, model: str = "buffalo_l", det_size: tuple[int, int] = (640, 640)):
        try:
            from insightface.app import FaceAnalysis
        except ImportError as e:
            raise RuntimeError(
                "face features need the optional `faces` extra: uv sync --extra faces "
                "(note: the buffalo model pack is non-commercial research only, "
                "see THIRD_PARTY_NOTICES.md)"
            ) from e

        self.app = FaceAnalysis(name=model, providers=["CPUExecutionProvider"])
        self.app.prepare(ctx_id=0, det_size=det_size)

    def detect(self, image_path: str) -> list[tuple[list[float], list[float]]]:
        import cv2

        img = cv2.imread(image_path)
        if img is None:
            return []
        return [(f.bbox.tolist(), f.embedding.tolist()) for f in self.app.get(img)]
